#!/usr/bin/env bash
# deploy_watchdog.sh — Install the Surf AI Watchdog on the existing EC2.
#
# user_data.sh only runs on first boot, so this script pushes the watchdog
# to a running (or stopped-then-started) instance via AWS SSM.
#
# Usage: ./scripts/deploy_watchdog.sh
# Prerequisites: aws CLI configured, terraform state available.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TERRAFORM_DIR="$SCRIPT_DIR/../infra/terraform"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

get_tf_output() {
  cd "$TERRAFORM_DIR" && terraform output -raw "$1" 2>/dev/null
}

echo -e "${BLUE}╔══════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║   Surf AI Watchdog — Deploy to EC2           ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════════════╝${NC}"
echo ""

INSTANCE_ID=$(get_tf_output ec2_instance_id)
REGION=$(get_tf_output aws_region)

if [ -z "$INSTANCE_ID" ]; then
  echo -e "${RED}Error: Could not read instance ID from Terraform state.${NC}"
  echo "Run 'terraform apply' in infra/terraform/ first."
  exit 1
fi

echo -e "  ${BLUE}Instance:${NC} $INSTANCE_ID"
echo -e "  ${BLUE}Region:${NC}   $REGION"
echo ""

# Check instance state
STATE=$(aws ec2 describe-instances \
  --instance-ids "$INSTANCE_ID" \
  --region "$REGION" \
  --query 'Reservations[0].Instances[0].State.Name' \
  --output text)

if [ "$STATE" != "running" ]; then
  echo -e "${RED}Instance is $STATE — must be running to deploy watchdog.${NC}"
  echo "Start it first: ./scripts/dev.sh up"
  exit 1
fi

echo -e "${YELLOW}⏳ Sending SSM deploy command...${NC}"

COMMAND_ID=$(aws ssm send-command \
  --instance-ids "$INSTANCE_ID" \
  --region "$REGION" \
  --document-name "AWS-RunShellScript" \
  --comment "Deploy Surf AI Watchdog" \
  --parameters 'commands=[
    "set -e",
    "echo \">>> Pulling latest code...\"",
    "cd /home/ubuntu/surf-ai-system && git pull",
    "echo \">>> Installing boto3...\"",
    "apt-get install -y python3-pip -q && pip3 install boto3 --quiet",
    "echo \">>> Creating watchdog log file...\"",
    "touch /var/log/surf-ai-watchdog.log && chown ubuntu:ubuntu /var/log/surf-ai-watchdog.log",
    "echo \">>> Writing systemd service...\"",
    "cat > /etc/systemd/system/surf-ai-watchdog.service << '"'"'EOF'"'"'\n[Unit]\nDescription=Surf AI Watchdog — auto-shutdown when queues drain\nAfter=docker.service\nRequires=docker.service\n\n[Service]\nType=simple\nUser=ubuntu\nWorkingDirectory=/home/ubuntu/surf-ai-system\nEnvironmentFile=/home/ubuntu/surf-ai-system/.env\nExecStart=/usr/bin/python3 /home/ubuntu/surf-ai-system/infra/watchdog.py\nRestart=on-failure\nRestartSec=30\nStandardOutput=journal\nStandardError=journal\n\n[Install]\nWantedBy=multi-user.target\nEOF",
    "echo \">>> Enabling and starting watchdog...\"",
    "systemctl daemon-reload",
    "systemctl enable surf-ai-watchdog",
    "systemctl restart surf-ai-watchdog",
    "sleep 3",
    "systemctl status surf-ai-watchdog --no-pager",
    "echo \">>> Watchdog deployed successfully!\""
  ]' \
  --query 'Command.CommandId' \
  --output text)

echo -e "  ${BLUE}Command ID:${NC} $COMMAND_ID"
echo -e "${YELLOW}⏳ Waiting for command to complete (up to 90s)...${NC}"

# Poll until command finishes
for i in $(seq 1 18); do
  sleep 5
  STATUS=$(aws ssm get-command-invocation \
    --command-id "$COMMAND_ID" \
    --instance-id "$INSTANCE_ID" \
    --region "$REGION" \
    --query 'Status' \
    --output text 2>/dev/null || echo "Pending")

  if [ "$STATUS" = "Success" ]; then
    echo ""
    echo -e "${GREEN}✅ Watchdog deployed successfully!${NC}"
    echo ""
    echo -e "  ${BLUE}Status:${NC}  sudo systemctl status surf-ai-watchdog"
    echo -e "  ${BLUE}Logs:${NC}    journalctl -u surf-ai-watchdog -f"
    echo -e "  ${BLUE}File:${NC}    /var/log/surf-ai-watchdog.log"
    echo ""
    echo -e "${YELLOW}The watchdog is now running. It will:${NC}"
    echo "  1. Wait until /tmp/ingestion-stopped is created (end of day)"
    echo "  2. Monitor all 5 SQS queues"
    echo "  3. Stop this EC2 when queues are empty for 20 minutes"
    exit 0
  elif [ "$STATUS" = "Failed" ] || [ "$STATUS" = "Cancelled" ] || [ "$STATUS" = "TimedOut" ]; then
    echo ""
    echo -e "${RED}Deploy failed with status: $STATUS${NC}"
    aws ssm get-command-invocation \
      --command-id "$COMMAND_ID" \
      --instance-id "$INSTANCE_ID" \
      --region "$REGION" \
      --query 'StandardErrorContent' \
      --output text
    exit 1
  fi

  echo -n "."
done

echo ""
echo -e "${YELLOW}Command still running — check manually:${NC}"
echo "aws ssm get-command-invocation --command-id $COMMAND_ID --instance-id $INSTANCE_ID --region $REGION"
