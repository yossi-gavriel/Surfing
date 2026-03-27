# Surf AI System — Project Context

This file is read automatically by Claude Code at the start of every session.
It contains the full architecture, infrastructure state, and operational knowledge of the system.

---

## 1. What Is This System?

A surf session recording and analysis system that:
- Captures RTSP camera streams with FFmpeg (ingestion)
- Detects and tracks people with YOLOv8 (frame processor)
- Generates face embeddings with InsightFace (embedding service)
- Matches surfers to known profiles (matching service)
- Clips matched sessions into short videos (clipper service)
- Serves results via a FastAPI backend + Angular frontend

---

## 2. Current Architecture

### Execution Model — On-Demand (NOT always-on)

```
User clicks "▶ Start System" in admin UI
  → API Gateway (always-on)
  → Lambda: surf-ai-start-on-demand
  → EC2 starts → Docker restart:always → all 7 services up (~60s)
  → Ingestion connects to RTSP cameras
  → Pipeline: Ingest → SQS → FrameProc → SQS → Embed → SQS → Match → SQS → Clip
  → Watchdog monitors all 5 SQS queues

User clicks "■ Stop System"
  → API Gateway → Lambda: surf-ai-stop-system
  → SSM → docker stop ingestion + touch /tmp/ingestion-stopped
  → Watchdog: queues empty for 20min → ec2:StopInstances(self)
```

### EC2 Instance
- **Instance ID:** `i-0d0c8b95962dc0f55`
- **Type:** `t3.small`
- **Region:** `us-east-1`
- **Key pair:** `face_gpu_key`
- **Tag:** `Name=SurfAISystem`
- **Elastic IP:** static (see `infra/terraform/eip.tf`)
- **Domain:** `surfing.heyi.co.il`
- **IAM Role:** `surf-ai-ec2-role` (S3 Full, SQS Full, SSM Managed, ec2:StopInstances self)

### AWS Account
- **Account ID:** `539241383982`
- **Region:** `us-east-1`

---

## 3. Services (Docker Compose — `infra/docker-compose.yml`)

| Container | Purpose | Always Restarts |
|---|---|---|
| `redis` | IoU tracker state across chunks | yes |
| `ingestion-service` | FFmpeg RTSP → S3 chunks + SQS | yes |
| `frame-processor` | YOLOv8 detection + IoU tracking | yes |
| `embedding-service` | InsightFace face embeddings | yes |
| `matching-service` | Embedding → known profiles match | yes |
| `clipper-service` | Matched tracks → S3 video clips | yes |
| `api-gateway` | FastAPI REST API on :8000 | yes |
| `frontend` | Nginx static Angular build on :4200 | yes |

All containers have `restart: always` — they auto-start when Docker daemon starts (i.e., after EC2 start/stop).

### Nginx (host, not Docker)
- Proxies `/api/` → api-gateway:8000
- Proxies `/` → frontend:4200
- Config: `infra/terraform/nginx.conf`

### Frontend Dev Server
- Runs as systemd service: `surf-ai-frontend`
- Command: `npx ng serve --host 0.0.0.0 --port 4200`
- Angular standalone components, signals, inject() pattern

---

## 4. SQS Queues (5 queues, 24h retention)

| Queue Name | URL Variable in .env | Flow |
|---|---|---|
| `video-chunks-queue` | `INPUT_SQS_URL` | Ingestion → Frame Processor |
| `tracks-queue` | `OUTPUT_SQS_URL` | Frame Processor → Embedding |
| `embeddings-queue` | `EMBEDDING_OUTPUT_SQS_URL` | Embedding → Matching |
| `matching-queue` | `MATCHING_OUTPUT_SQS_URL` | Matching → Clipper |
| `clipper-queue` | `CLIPPER_INPUT_SQS_URL` | Clipper input |

---

## 5. Lambda Functions

| Function | Purpose |
|---|---|
| `surf-ai-start-on-demand` | Start EC2 (idempotent) + resume ingestion if running |
| `surf-ai-stop-system` | Stop ingestion via SSM + signal watchdog |
| `surf-ai-system-status` | Return EC2 state + human label |
| `surf-ai-start-ec2` | Old schedule-based start (kept, unused) |
| `surf-ai-stop-ingestion` | Old schedule-based stop (kept, unused) |

**IAM Role for Lambdas:** `surf-ai-lambda-scheduler`
- Permissions: `ec2:StartInstances`, `ec2:StopInstances`, `ec2:DescribeInstances`, `ssm:SendCommand`, `logs:*`

---

## 6. API Gateway (Always-On)

**URL:** `https://djcqadh3mg.execute-api.us-east-1.amazonaws.com`
**Type:** HTTP API v2 (auto-deploy, `$default` stage)
**CORS:** `https://surfing.heyi.co.il`, `http://localhost:4200`

| Route | Lambda | Description |
|---|---|---|
| `GET /system-status` | `surf-ai-system-status` | EC2 state polling |
| `POST /start-system` | `surf-ai-start-on-demand` | Start EC2 |
| `POST /stop-system` | `surf-ai-stop-system` | Stop ingestion → watchdog shuts EC2 |

---

## 7. Watchdog (`infra/watchdog.py`)

Runs as systemd service `surf-ai-watchdog` on the EC2.

- Monitors all 5 SQS queues every 60 seconds
- **Only activates after `/tmp/ingestion-stopped` is created**
- When all queues empty for 20 minutes → calls `ec2:StopInstances(self)`
- Logs to `/var/log/surf-ai-watchdog.log` + journald
- Config via env vars: `WATCHDOG_EMPTY_MINUTES` (default 20), `WATCHDOG_CHECK_SECONDS` (default 60)

---

## 8. EventBridge Rules (DISABLED — on-demand mode)

| Rule | Schedule | State |
|---|---|---|
| `surf-ai-start-ec2` | `cron(0 3 * * ? *)` (6am IDT) | **DISABLED** |
| `surf-ai-stop-ingestion` | `cron(0 15 * * ? *)` (6pm IDT) | **DISABLED** |

To re-enable schedule mode:
```bash
aws events enable-rule --name surf-ai-start-ec2 --region us-east-1
aws events enable-rule --name surf-ai-stop-ingestion --region us-east-1
```

To disable again (on-demand mode):
```bash
aws events disable-rule --name surf-ai-start-ec2 --region us-east-1
aws events disable-rule --name surf-ai-stop-ingestion --region us-east-1
```

---

## 9. S3 Bucket

- Created by Terraform (name in `infra/terraform/s3.tf`)
- Stores: raw video chunks, final clipped videos
- EC2 IAM role has `AmazonS3FullAccess`

---

## 10. Terraform State

**The Terraform state is INCOMPLETE** — the infra was deployed from another machine and only the EC2 instance has been imported into local state (`infra/terraform/terraform.tfstate`).

**Do NOT run `terraform apply` without `-target` flags** — it will try to recreate existing resources (SQS queues, IAM roles, etc.) that it doesn't know about.

Safe Terraform operations:
```bash
cd infra/terraform
terraform plan -var="key_name=face_gpu_key" -target=<specific_resource>
terraform apply -var="key_name=face_gpu_key" -target=<specific_resource>
```

For new infra (Lambda, CloudWatch), **use AWS CLI directly** — it's safer given the partial state.

---

## 11. Key Files

```
surf-ai-system/
├── CLAUDE.md                          ← this file
├── infra/
│   ├── docker-compose.yml             ← all 7 Docker services
│   ├── watchdog.py                    ← auto-shutdown watchdog
│   └── terraform/
│       ├── ec2.tf                     ← EC2 instance definition
│       ├── iam.tf                     ← IAM roles + policies
│       ├── sqs.tf                     ← 5 SQS queues
│       ├── lambda.tf                  ← Lambda + CloudWatch Terraform
│       ├── variables.tf               ← schedule_start, schedule_stop, etc.
│       ├── user_data.sh               ← EC2 bootstrap (first boot only)
│       ├── nginx.conf                 ← Nginx reverse proxy config
│       └── lambdas/
│           ├── start_on_demand.py     ← POST /start-system
│           ├── stop_system.py         ← POST /stop-system
│           ├── system_status.py       ← GET /system-status
│           ├── start_ec2.py           ← (old schedule-based, kept)
│           └── stop_ingestion.py      ← (old schedule-based, kept)
├── frontend/
│   ├── start.html                     ← standalone start page (open locally)
│   └── src/app/pages/admin/
│       ├── admin-layout.component.ts  ← sidebar with EC2 control panel
│       ├── admin-ec2-control.service.ts ← Angular service for API calls
│       ├── admin.component.ts         ← main dashboard page
│       └── admin-system.service.ts    ← admin data service
└── scripts/
    ├── dev.sh                         ← manual up/down/status
    └── deploy_watchdog.sh             ← deploy watchdog to EC2 via SSM
```

---

## 12. How to Deploy Changes

### Frontend (Angular) changes → EC2
```bash
# From local machine with AWS CLI configured:
INSTANCE_ID="i-0d0c8b95962dc0f55"

aws ssm send-command \
  --instance-ids "$INSTANCE_ID" \
  --region us-east-1 \
  --document-name "AWS-RunShellScript" \
  --parameters 'commands=[
    "export HOME=/root",
    "git config --global --add safe.directory /home/ubuntu/surf-ai-system",
    "sudo -u ubuntu HOME=/home/ubuntu git -C /home/ubuntu/surf-ai-system pull",
    "systemctl restart surf-ai-frontend"
  ]'
```

### Lambda changes → AWS CLI
```bash
# 1. Zip the updated Python file
cd infra/terraform/lambdas
powershell Compress-Archive -Path start_on_demand.py -DestinationPath start_on_demand.zip -Force

# 2. Update function code
aws lambda update-function-code \
  --function-name surf-ai-start-on-demand \
  --zip-file "fileb://C:\...\start_on_demand.zip" \
  --region us-east-1
```

### Backend (Docker services) changes → EC2
```bash
aws ssm send-command \
  --instance-ids "i-0d0c8b95962dc0f55" \
  --region us-east-1 \
  --document-name "AWS-RunShellScript" \
  --parameters 'commands=[
    "export HOME=/root",
    "sudo -u ubuntu HOME=/home/ubuntu git -C /home/ubuntu/surf-ai-system pull",
    "cd /home/ubuntu/surf-ai-system/infra && docker-compose up -d --build"
  ]'
```

### Manual EC2 start/stop
```bash
./scripts/dev.sh up      # start EC2
./scripts/dev.sh down    # stop EC2
./scripts/dev.sh status  # check state
```

---

## 13. Git

- **Repo:** `https://github.com/yossi-gavriel/Surfing.git`
- **Working branch:** `claude/laughing-elion`
- **Main branch:** `main` (deployed on EC2)

The EC2 has the `main` branch. When deploying new frontend changes, either:
1. Merge `claude/laughing-elion` → `main` → pull on EC2, OR
2. Use `git show origin/claude/laughing-elion:path/to/file > /path/to/file` to cherry-pick files

---

## 14. Cost

| Resource | Cost |
|---|---|
| EC2 t3.small (when running) | ~$0.023/hr |
| EC2 EBS 30GB gp3 (always) | ~$2.40/month |
| Elastic IP (when EC2 running) | $0 |
| Elastic IP (when EC2 stopped) | ~$3.60/month |
| API Gateway (5 Lambdas) | ~$0 (free tier) |
| SQS | ~$0 |
| S3 | per usage |

**Total when running 4h/day:** ~$5-8/month EC2 + fixed costs
**Total when stopped:** ~$6/month (EBS + EIP)

---

## 15. Common Operations & Debugging

### Check watchdog logs on EC2
```bash
aws ssm send-command \
  --instance-ids i-0d0c8b95962dc0f55 --region us-east-1 \
  --document-name AWS-RunShellScript \
  --parameters 'commands=["journalctl -u surf-ai-watchdog -n 50 --no-pager"]'
```

### Check Docker services on EC2
```bash
aws ssm send-command \
  --instance-ids i-0d0c8b95962dc0f55 --region us-east-1 \
  --document-name AWS-RunShellScript \
  --parameters 'commands=["cd /home/ubuntu/surf-ai-system/infra && docker-compose ps"]'
```

### Check SQS queue depths
```bash
for Q in video-chunks-queue tracks-queue embeddings-queue matching-queue clipper-queue; do
  echo -n "$Q: "
  aws sqs get-queue-attributes \
    --queue-url "https://sqs.us-east-1.amazonaws.com/539241383982/$Q" \
    --attribute-names ApproximateNumberOfMessages \
    --region us-east-1 \
    --query "Attributes.ApproximateNumberOfMessages" --output text
done
```

### Test API Gateway endpoints
```bash
curl https://djcqadh3mg.execute-api.us-east-1.amazonaws.com/system-status
curl -X POST https://djcqadh3mg.execute-api.us-east-1.amazonaws.com/start-system
curl -X POST https://djcqadh3mg.execute-api.us-east-1.amazonaws.com/stop-system
```

### Reset watchdog signal (if EC2 is running but watchdog is blocking)
```bash
aws ssm send-command \
  --instance-ids i-0d0c8b95962dc0f55 --region us-east-1 \
  --document-name AWS-RunShellScript \
  --parameters 'commands=["rm -f /tmp/ingestion-stopped", "docker start ingestion-service || true"]'
```
