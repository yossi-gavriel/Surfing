#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TERRAFORM_DIR="$SCRIPT_DIR/../infra/terraform"

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

get_output() {
    cd "$TERRAFORM_DIR" && terraform output -raw "$1" 2>/dev/null
}

INSTANCE_ID=$(get_output ec2_instance_id || echo "")
REGION=$(get_output aws_region || echo "us-east-1")
IP=$(get_output elastic_ip || echo "")

if [ -z "$INSTANCE_ID" ]; then
    echo -e "${RED}Error: Could not read instance ID from Terraform state.${NC}"
    echo "Make sure you have run 'terraform apply' in infra/terraform/ first."
    exit 1
fi

case "${1:-}" in
    up)
        echo -e "${BLUE}🚀 Starting Surf AI System...${NC}"
        aws ec2 start-instances --instance-ids "$INSTANCE_ID" --region "$REGION" > /dev/null
        echo -e "${YELLOW}⏳ Waiting for instance to be running...${NC}"
        aws ec2 wait instance-running --instance-ids "$INSTANCE_ID" --region "$REGION"
        echo -e "${GREEN}✅ Instance is running!${NC}"
        echo ""
        echo -e "  ${BLUE}🌐 Elastic IP:${NC} $IP"
        echo -e "  ${BLUE}🔗 URL:${NC}        https://surfing.heyi.co.il"
        echo -e "  ${BLUE}🔑 SSH:${NC}        ssh ubuntu@$IP"
        echo ""
        echo -e "${YELLOW}⏳ Services may take 1-2 minutes to fully start after boot.${NC}"
        ;;

    down)
        echo -e "${RED}🛑 Stopping Surf AI System...${NC}"
        aws ec2 stop-instances --instance-ids "$INSTANCE_ID" --region "$REGION" > /dev/null
        echo -e "${YELLOW}⏳ Waiting for instance to stop...${NC}"
        aws ec2 wait instance-stopped --instance-ids "$INSTANCE_ID" --region "$REGION"
        echo -e "${GREEN}✅ Instance stopped. No compute charges while stopped.${NC}"
        echo ""
        echo -e "  ${YELLOW}💰 Still paying for:${NC}"
        echo "     - EBS storage (30GB gp3): ~\$2.40/month"
        echo "     - Elastic IP (unattached): ~\$3.60/month"
        echo "     - S3 storage: per usage"
        echo "     - SQS: per usage (likely \$0)"
        ;;

    status)
        STATE=$(aws ec2 describe-instances \
            --instance-ids "$INSTANCE_ID" \
            --region "$REGION" \
            --query 'Reservations[0].Instances[0].State.Name' \
            --output text)
        echo -e "  ${BLUE}📊 Instance:${NC}  $STATE"
        echo -e "  ${BLUE}🌐 Elastic IP:${NC} $IP"
        echo -e "  ${BLUE}🔗 URL:${NC}        https://surfing.heyi.co.il"
        ;;

    *)
        echo "╔══════════════════════════════════════════╗"
        echo "║   Surf AI System — Cost Controller       ║"
        echo "╚══════════════════════════════════════════╝"
        echo ""
        echo "Usage: $0 {up|down|status}"
        echo ""
        echo "  up      Start EC2 instance"
        echo "  down    Stop EC2 instance (save costs)"
        echo "  status  Check current instance state"
        exit 1
        ;;
esac
