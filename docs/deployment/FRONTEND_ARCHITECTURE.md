# MilkyHoop Frontend Deployment Architecture

> **Last Updated:** 2025-12-17
> **Maintainer:** DevOps Team
> **Status:** Production

---

## Quick Reference

| Component | Port | Description |
|-----------|------|-------------|
| Frontend Container (HTTPS) | 443 | SSL termination, static files, API proxy |
| Frontend Container (HTTP) | 3000 | Redirects to HTTPS |
| API Gateway | 8001 | Backend services |
| Docker Network | - | `milkyhoop_dev_network` |

---

## Architecture Overview

```
                                    ┌─────────────────────────────────────────┐
                                    │     milkyhoop-frontend-1 Container      │
                                    │                                         │
   Internet                         │  ┌─────────────────────────────────┐   │
      │                             │  │      nginx (nginx-ssl.conf)     │   │
      │                             │  │                                 │   │
      ▼                             │  │  Port 443 (SSL)                 │   │
┌──────────┐                        │  │    ├── Static files             │   │
│Cloudflare│                        │  │    ├── SPA routing              │   │
│  (CDN)   │ ───────────────────────┼──┼──► │    └── API proxy ──────────┼───┼──► API Gateway:8000
└──────────┘                        │  │                                 │   │    (Docker network)
                                    │  │  Port 80 (HTTP)                 │   │
                                    │  │    └── Redirect to HTTPS        │   │
                                    │  │                                 │   │
                                    │  └─────────────────────────────────┘   │
                                    │                                         │
                                    │  Volumes:                               │
                                    │    /usr/share/nginx/html ← build/      │
                                    │    /etc/nginx/conf.d/   ← nginx-ssl    │
                                    │    /etc/letsencrypt     ← SSL certs    │
                                    └─────────────────────────────────────────┘
```

### Traffic Flow

1. **User** → `https://milkyhoop.com`
2. **Cloudflare** → SSL passthrough → Server port 443
3. **Container nginx** → SSL termination with Let's Encrypt certs
4. **Static files** served from `/usr/share/nginx/html`
5. **API requests** (`/api/*`) proxied to `milkyhoop-dev-api_gateway:8000` via Docker network

---

## Critical Rules

### Rule 1: Host nginx MUST be STOPPED

```bash
# BEFORE deploying frontend, ALWAYS stop host nginx
sudo systemctl stop nginx

# Verify it's stopped
systemctl status nginx | grep "Active:"
# Should show: Active: inactive (dead)
```

**Why?** Both host nginx and container try to bind port 443. Only ONE can use it.

### Rule 2: Container Handles SSL Directly

The frontend container handles SSL termination using:
- Certificate: `/etc/letsencrypt/live/milkyhoop.com/fullchain.pem`
- Private Key: `/etc/letsencrypt/live/milkyhoop.com/privkey.pem`

These are mounted from the host into the container.

### Rule 3: Never Modify Port Bindings in deploy-prod.sh

The script MUST include these port bindings:

```bash
docker run -d \
    -p 3000:80 \      # HTTP (redirects to HTTPS)
    -p 443:443 \      # HTTPS (SSL termination)
    ...
```

**Removing `-p 443:443` will break SSL!**

---

## File Reference

### `/root/milkyhoop/frontend/web/deploy-prod.sh`

Main deployment script. What it does:

1. Builds frontend with `npm run build`
2. Stops and removes existing container
3. Deploys new container with:
   - Port 3000:80 (HTTP)
   - Port 443:443 (HTTPS)
   - Volume mounts for build files, nginx config, SSL certs
4. Runs health checks

**Usage:**
```bash
# Full rebuild and deploy
./deploy-prod.sh

# Quick deploy (skip npm build, use existing build/)
./deploy-prod.sh --skip-build
```

### `/root/milkyhoop/frontend/web/nginx-ssl.conf`

nginx configuration inside the container:

| Block | Purpose |
|-------|---------|
| `server :80` | HTTP redirect to HTTPS |
| `server :443 ssl` | HTTPS with SSL, serves static files |
| `location /api/` | Proxy to API gateway |
| `location /api/auth/qr/ws/` | WebSocket for QR login |
| `location /api/devices/ws/` | WebSocket for device notifications |

**API Proxy Target:** `http://milkyhoop-dev-api_gateway:8000`

### `/etc/letsencrypt/`

SSL certificates managed by Let's Encrypt (Certbot):

```
/etc/letsencrypt/live/milkyhoop.com/
├── fullchain.pem   # Certificate + chain
├── privkey.pem     # Private key
├── cert.pem        # Certificate only
└── chain.pem       # Intermediate chain
```

**Auto-renewal:** Certbot handles renewal. Container uses latest certs on restart.

---

## Deployment Commands

### Standard Deployment

```bash
cd /root/milkyhoop/frontend/web

# 1. Stop host nginx (CRITICAL!)
sudo systemctl stop nginx

# 2. Deploy
./deploy-prod.sh
```

### Quick Deployment (Skip Build)

```bash
# Use when only nginx config changed, not source code
./deploy-prod.sh --skip-build
```

### Manual Container Management

```bash
# Check status
docker ps | grep frontend

# View logs
docker logs -f milkyhoop-frontend-1

# Restart container
docker restart milkyhoop-frontend-1

# Full redeploy
docker stop milkyhoop-frontend-1
docker rm milkyhoop-frontend-1
./deploy-prod.sh
```

---

## Common Mistakes to Avoid

### Mistake 1: Running Host nginx

**Symptom:** Container fails to start, port 443 already in use.

```
Error: bind: address already in use
```

**Fix:**
```bash
sudo systemctl stop nginx
./deploy-prod.sh
```

### Mistake 2: Removing Port 443 Binding

**Symptom:** Site loads over HTTP but HTTPS doesn't work.

**Fix:** Restore deploy-prod.sh to git version:
```bash
git checkout HEAD -- deploy-prod.sh
```

### Mistake 3: Using Wrong nginx Config

**Symptom:** Container in restart loop, nginx error in logs.

**Fix:** Ensure `nginx-ssl.conf` is used, not `nginx.conf` or others:
```bash
# Check current config
docker exec milkyhoop-frontend-1 cat /etc/nginx/conf.d/default.conf | head -20
```

### Mistake 4: Missing SSL Cert Mount

**Symptom:** nginx fails with "cannot load certificate" error.

**Fix:** Ensure `/etc/letsencrypt` is mounted in docker run command.

---

## Verification Checklist

After deployment, verify everything is working:

```bash
# 1. Container is running
docker ps | grep frontend
# Expected: milkyhoop-frontend-1 with ports 443->443, 3000->80

# 2. Local HTTPS works
curl -sk https://localhost:443 | grep -oE 'main\.[a-z0-9]+\.js'
# Expected: main.XXXXXXXX.js

# 3. Public site works
curl -s https://milkyhoop.com | grep -oE 'main\.[a-z0-9]+\.js'
# Expected: same JS filename as above

# 4. API proxy works
curl -s -X POST https://milkyhoop.com/api/auth/qr/generate \
  -H "Content-Type: application/json" \
  -d '{"fingerprint":"test"}'
# Expected: {"success":true,"token":"..."}

# 5. HTTP redirects to HTTPS
curl -sI http://localhost:3000 | grep Location
# Expected: Location: https://milkyhoop.com/...
```

---

## Troubleshooting

### Container Won't Start

```bash
# Check logs
docker logs milkyhoop-frontend-1

# Common issues:
# - "bind: address already in use" → Stop host nginx
# - "cannot load certificate" → Check /etc/letsencrypt mount
# - "no such file" → Check build/ folder exists
```

### Site Returns 502 Bad Gateway

```bash
# Check API gateway is running
docker ps | grep api_gateway

# Check container can reach API gateway
docker exec milkyhoop-frontend-1 ping -c 2 milkyhoop-dev-api_gateway
```

### SSL Certificate Expired

```bash
# Renew certificate
sudo certbot renew

# Restart container to pick up new cert
docker restart milkyhoop-frontend-1
```

---

## Related Documentation

- [Backend Architecture](../../backend/README.md)
- [API Gateway Routes](../../backend/api_gateway/README.md)
- [Docker Network Setup](../DOCKER_NETWORK.md)

---

## Change History

| Date | Change | Author |
|------|--------|--------|
| 2025-12-17 | Initial documentation after architecture clarification | DevOps |
