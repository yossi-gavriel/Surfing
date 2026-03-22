#!/bin/bash
set -e

echo "============================================="
echo "  Surf AI — HTTPS Certificate Setup"
echo "============================================="

# Install certbot if not present
if ! command -v certbot &> /dev/null; then
    echo ">>> Installing Certbot..."
    sudo apt-get update -y
    sudo apt-get install -y certbot python3-certbot-nginx
fi

# Request certificate with automatic HTTP→HTTPS redirect
echo ">>> Requesting SSL certificate for ${domain_name}..."
sudo certbot --nginx \
    -d ${domain_name} \
    --redirect \
    --non-interactive \
    --agree-tos \
    -m ${admin_email}

# Setup auto-renewal cron (skip if already exists)
if ! crontab -l 2>/dev/null | grep -q "certbot renew"; then
    echo ">>> Setting up auto-renewal cron..."
    (crontab -l 2>/dev/null; echo "0 3 * * * certbot renew --quiet --deploy-hook 'systemctl reload nginx'") | crontab -
fi

echo ""
echo "============================================="
echo "  HTTPS enabled successfully!"
echo "  Visit: https://${domain_name}"
echo "============================================="
