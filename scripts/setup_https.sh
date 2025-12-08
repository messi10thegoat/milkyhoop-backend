#!/bin/bash
# ==============================================
# MilkyHoop HTTPS Setup Script
# Automated Let's Encrypt certificate setup
# ==============================================

set -e

# Configuration
DOMAIN="${1:-milkyhoop.com}"
EMAIL="${2:-admin@milkyhoop.com}"
NGINX_CONF="/root/milkyhoop-dev/frontend/nginx.conf"
CERTBOT_DIR="/var/www/certbot"
CERT_DIR="/etc/letsencrypt/live/${DOMAIN}"

echo "=== MilkyHoop HTTPS Setup ==="
echo "Domain: $DOMAIN"
echo "Email: $EMAIL"
echo ""

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "ERROR: Please run as root"
    exit 1
fi

# Install certbot if not present
if ! command -v certbot &> /dev/null; then
    echo "[1/6] Installing certbot..."
    apt-get update
    apt-get install -y certbot python3-certbot-nginx
else
    echo "[1/6] Certbot already installed"
fi

# Create webroot directory
echo "[2/6] Creating webroot directory..."
mkdir -p $CERTBOT_DIR

# Obtain certificate using webroot method
echo "[3/6] Obtaining SSL certificate..."
certbot certonly \
    --webroot \
    --webroot-path=$CERTBOT_DIR \
    --email $EMAIL \
    --agree-tos \
    --no-eff-email \
    --force-renewal \
    -d $DOMAIN \
    -d www.$DOMAIN

# Verify certificate
if [ ! -f "$CERT_DIR/fullchain.pem" ]; then
    echo "ERROR: Certificate not found at $CERT_DIR"
    exit 1
fi
echo "[4/6] Certificate obtained successfully"

# Update nginx configuration
echo "[5/6] Updating nginx configuration..."
cat > /root/milkyhoop-dev/frontend/nginx.ssl.conf << 'NGINX_SSL'
# ==============================================
# MilkyHoop Nginx Configuration - HTTPS Enabled
# Security-hardened with SSL/TLS
# ==============================================

# Rate limiting zones (DDoS protection)
limit_req_zone $binary_remote_addr zone=general:10m rate=10r/s;
limit_req_zone $binary_remote_addr zone=api:10m rate=30r/s;
limit_req_zone $binary_remote_addr zone=auth:10m rate=3r/s;
limit_req_zone $binary_remote_addr zone=static:10m rate=50r/s;

# Connection limiting (prevent slowloris)
limit_conn_zone $binary_remote_addr zone=conn_limit:10m;

# Request size limits
client_max_body_size 10M;
client_body_buffer_size 128k;
client_header_buffer_size 1k;
large_client_header_buffers 4 8k;

# Timeouts (prevent slowloris)
client_body_timeout 10s;
client_header_timeout 10s;
keepalive_timeout 15s;
send_timeout 10s;

# Hide nginx version
server_tokens off;

# HTTP server - redirect to HTTPS
server {
    listen 80;
    listen [::]:80;
    server_name DOMAIN_PLACEHOLDER www.DOMAIN_PLACEHOLDER;

    # Connection limit
    limit_conn conn_limit 20;

    # For Let's Encrypt certificate validation
    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }

    # Redirect all HTTP to HTTPS
    location / {
        return 301 https://$host$request_uri;
    }
}

# HTTPS server
server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name DOMAIN_PLACEHOLDER www.DOMAIN_PLACEHOLDER;

    # Connection limit
    limit_conn conn_limit 20;

    # SSL Configuration
    ssl_certificate /etc/letsencrypt/live/DOMAIN_PLACEHOLDER/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/DOMAIN_PLACEHOLDER/privkey.pem;

    # SSL Security Settings (Mozilla Modern)
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers off;
    ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384:ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-RSA-CHACHA20-POLY1305:DHE-RSA-AES128-GCM-SHA256:DHE-RSA-AES256-GCM-SHA384;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 1d;
    ssl_session_tickets off;

    # OCSP Stapling
    ssl_stapling on;
    ssl_stapling_verify on;
    resolver 8.8.8.8 8.8.4.4 valid=300s;
    resolver_timeout 5s;

    # HSTS (HTTP Strict Transport Security)
    add_header Strict-Transport-Security "max-age=63072000; includeSubDomains; preload" always;

    # Security headers
    add_header X-Frame-Options "DENY" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
    add_header Permissions-Policy "geolocation=(), microphone=(), camera=()" always;
    add_header Content-Security-Policy "default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; font-src 'self' data:; connect-src 'self' https://api.milkyhoop.com wss://api.milkyhoop.com;" always;

    # Block common attack paths
    location ~* /(\.git|\.env|\.htaccess|\.htpasswd|wp-admin|wp-login|xmlrpc\.php|phpmyadmin) {
        deny all;
        return 444;
    }

    # Block bad bots
    if ($http_user_agent ~* (scrapy|curl|wget|python|nikto|sqlmap|nmap|masscan)) {
        return 444;
    }

    # Frontend
    location / {
        limit_req zone=general burst=20 nodelay;
        root /usr/share/nginx/html;
        index index.html index.htm;
        try_files $uri $uri/ /index.html;
    }

    # Static assets (higher rate limit)
    location ~* \.(js|css|png|jpg|jpeg|gif|ico|svg|woff|woff2|ttf|eot)$ {
        limit_req zone=static burst=100 nodelay;
        root /usr/share/nginx/html;
        expires 1y;
        add_header Cache-Control "public, immutable";
        add_header X-Content-Type-Options "nosniff" always;
    }

    # Custom error pages
    error_page 429 /429.html;
    location = /429.html {
        internal;
        default_type text/html;
        return 429 '<!DOCTYPE html><html><head><title>Rate Limited</title></head><body><h1>429 Too Many Requests</h1><p>You have been rate limited. Please try again later.</p></body></html>';
    }

    error_page 500 502 503 504 /50x.html;
    location = /50x.html {
        root /usr/share/nginx/html;
    }
}
NGINX_SSL

# Replace domain placeholder
sed -i "s/DOMAIN_PLACEHOLDER/$DOMAIN/g" /root/milkyhoop-dev/frontend/nginx.ssl.conf

# Backup and replace nginx config
cp $NGINX_CONF ${NGINX_CONF}.backup
cp /root/milkyhoop-dev/frontend/nginx.ssl.conf $NGINX_CONF

# Setup auto-renewal
echo "[6/6] Setting up auto-renewal..."
(crontab -l 2>/dev/null; echo "0 0 * * * certbot renew --quiet --post-hook 'docker restart milkyhoop-dev-frontend-1'") | crontab -

echo ""
echo "=== HTTPS Setup Complete ==="
echo "Certificate: $CERT_DIR"
echo "Auto-renewal: Enabled (daily cron)"
echo ""
echo "Next steps:"
echo "1. Rebuild frontend: docker compose build frontend"
echo "2. Restart services: docker compose up -d"
echo "3. Test HTTPS: curl -I https://$DOMAIN"
echo ""
echo "SSL Labs test: https://www.ssllabs.com/ssltest/analyze.html?d=$DOMAIN"
