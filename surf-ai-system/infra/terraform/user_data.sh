#!/bin/bash
set -e
exec > >(tee /var/log/user-data.log) 2>&1
echo "=== Surf AI System Bootstrap - $(date) ==="

# -----------------------------------------------
# 1. System Dependencies
# -----------------------------------------------
apt-get update -y
apt-get install -y ca-certificates curl gnupg git software-properties-common

# -----------------------------------------------
# 2. Install Docker (idempotent)
# -----------------------------------------------
if ! command -v docker &> /dev/null; then
    echo ">>> Installing Docker..."
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg
    ARCH=$(dpkg --print-architecture)
    CODENAME=$(. /etc/os-release && echo "$VERSION_CODENAME")
    echo "deb [arch=$ARCH signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $CODENAME stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null
    apt-get update -y
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi

# -----------------------------------------------
# 3. Install docker-compose standalone (idempotent)
# -----------------------------------------------
if ! command -v docker-compose &> /dev/null; then
    echo ">>> Installing docker-compose..."
    curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
    chmod +x /usr/local/bin/docker-compose
fi

# -----------------------------------------------
# 4. Install Node.js 20 (idempotent)
# -----------------------------------------------
if ! command -v node &> /dev/null; then
    echo ">>> Installing Node.js 20..."
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
    apt-get install -y nodejs
fi

# -----------------------------------------------
# 5. Enable Docker on boot
# -----------------------------------------------
systemctl enable docker
systemctl start docker

# -----------------------------------------------
# 6. Clone Repository (idempotent)
# -----------------------------------------------
cd /home/ubuntu
if [ ! -d "surf-ai-system" ]; then
    echo ">>> Cloning repository..."
    git clone ${repo_url} _clone_tmp
    # Handle repo structure: project may be in subdirectory
    if [ -d "_clone_tmp/surf-ai-system" ]; then
        mv _clone_tmp/surf-ai-system ./surf-ai-system
        rm -rf _clone_tmp
    else
        mv _clone_tmp ./surf-ai-system
    fi
fi
cd /home/ubuntu/surf-ai-system
chown -R ubuntu:ubuntu /home/ubuntu/surf-ai-system

# -----------------------------------------------
# 7. Write Environment Configuration
# -----------------------------------------------
cat <<'__ENV_EOF__' > .env
AWS_REGION=${region}
S3_BUCKET=${bucket}
SQS_QUEUE_URL=${q_chunks}
CHUNK_DURATION=10
INPUT_SQS_URL=${q_chunks}
OUTPUT_SQS_URL=${q_tracks}
YOLO_MODEL=yolov8n.pt
EMBEDDING_INPUT_SQS_URL=${q_tracks}
EMBEDDING_OUTPUT_SQS_URL=${q_embed}
INSIGHTFACE_MODEL=buffalo_s
MATCHING_INPUT_SQS_URL=${q_embed}
MATCHING_OUTPUT_SQS_URL=${q_match}
CLIPPER_OUTPUT_SQS_URL=${q_clip}
CLIPPER_INPUT_SQS_URL=${q_clip}
ANALYSIS_ENABLED=false
ANALYSIS_SQS_URL=${q_analysis}
ANALYSIS_INPUT_SQS_URL=${q_analysis}
ANALYSIS_DLQ_SQS_URL=${q_analysis_dlq}
ANALYSIS_MODEL_VERSION=wave_surfer_v1.0
ANALYSIS_DEBUG_ARTIFACTS=false
PORT=8000
__ENV_EOF__

# Copy env to infra directory for docker-compose
cp .env infra/.env

# -----------------------------------------------
# 8. Start Backend Services (Docker Compose)
# -----------------------------------------------
echo ">>> Building and starting backend services..."
cd /home/ubuntu/surf-ai-system/infra
docker-compose up -d --build 2>&1 | tee /home/ubuntu/docker-compose-build.log

# -----------------------------------------------
# 9. Setup Frontend with systemd (auto-restart)
# -----------------------------------------------
echo ">>> Setting up Angular frontend..."
cd /home/ubuntu/surf-ai-system/frontend
npm install 2>&1 | tee /home/ubuntu/frontend-install.log
npm install -g @angular/cli

cat <<'__SVC_EOF__' > /etc/systemd/system/surf-ai-frontend.service
[Unit]
Description=Surf AI Angular Frontend
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/surf-ai-system/frontend
ExecStart=/usr/bin/npx ng serve --host 0.0.0.0 --port 4200 --disable-host-check
Restart=always
RestartSec=5
Environment=HOME=/home/ubuntu
Environment=PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

[Install]
WantedBy=multi-user.target
__SVC_EOF__

systemctl daemon-reload
systemctl enable surf-ai-frontend
systemctl start surf-ai-frontend

# -----------------------------------------------
# 10. Setup Nginx Reverse Proxy
# -----------------------------------------------
echo ">>> Setting up Nginx..."
apt-get install -y nginx

cat <<'__NGINX_EOF__' > /etc/nginx/sites-available/surf-ai
${nginx_conf}
__NGINX_EOF__

cat <<'__UPSTREAMS_EOF__' > /etc/nginx/conf.d/surf-ai-upstreams.conf
upstream api_upstream {
    server 127.0.0.1:8000;
}

upstream frontend_upstream {
    server 127.0.0.1:4200;
}
__UPSTREAMS_EOF__

ln -sf /etc/nginx/sites-available/surf-ai /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default

nginx -t && systemctl restart nginx
systemctl enable nginx

# -----------------------------------------------
# 11. Place HTTPS Script (manual execution only)
# -----------------------------------------------
cat <<'__HTTPS_EOF__' > /home/ubuntu/enable_https.sh
${enable_https_script}
__HTTPS_EOF__

chmod +x /home/ubuntu/enable_https.sh
chown ubuntu:ubuntu /home/ubuntu/enable_https.sh

echo "=== Bootstrap Complete - $(date) ==="
echo "HTTP available at: http://${domain_name}"
echo "Run ~/enable_https.sh for HTTPS"
