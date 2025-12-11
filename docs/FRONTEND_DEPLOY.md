# MilkyHoop Frontend - Deployment Guide

> **Location:** `/root/milkyhoop/frontend/web/`
> **Domain:** https://milkyhoop.com

## Infrastructure Architecture

```
Internet (HTTPS:443)
       ↓
   Cloudflare (CDN/Cache)
       ↓
   Server (Port 443 - SSL via Let's Encrypt)
       ↓
   milkyhoop-frontend-1 (Nginx)
   ├── Port 80 (HTTP) → mapped to 3000
   └── Port 443 (HTTPS) → SSL certificates
       ↓
   milkyhoop_dev_network → API Gateway → Backend Services
```

**Key Points:**
- SSL certificates from Let's Encrypt (auto-renewed)
- Frontend exposed on ports 3000 (HTTP) and 443 (HTTPS)
- Cloudflare set to "Full (strict)" mode
- Backend API accessed via Docker network

---

## Quick Reference

```bash
cd /root/milkyhoop/frontend/web

# 1. Cleanup backup files (optional, but recommended)
./cleanup.sh

# 2. Deploy to production
./deploy-prod.sh           # With cache (45-90 sec)
./deploy-prod.sh --fresh   # Without cache (2-3 min)

# 3. Development (hot reload)
docker-compose -f docker-compose.dev.yml up -d
```

---

## Deployment Scripts

### `./deploy-prod.sh`

Production deployment with versioning and health checks.

```bash
# Normal deploy (uses Docker cache - fast)
./deploy-prod.sh

# Fresh deploy (no cache - use when dependencies change)
./deploy-prod.sh --fresh
```

**Features:**
- Image tagging with timestamp (e.g., `milkyhoop-frontend:20251209-120000`)
- Automatic rollback capability
- Dual health check (local + HTTPS)
- Colorized output

### `./cleanup.sh`

Removes backup files that bloat Docker build context.

```bash
./cleanup.sh
```

**Removes:**
- `*.backup` files
- `build_backup_*` directories
- `*.swp` vim swap files
- `.DS_Store` macOS files

---

## Emergency Hotfix

For urgent fixes without full rebuild (~10 seconds):

```bash
cd /root/milkyhoop/frontend/web

# Build locally
npm run build

# Copy directly to running container
docker cp build/. milkyhoop-frontend-1:/usr/share/nginx/html/

# Verify
curl -I https://milkyhoop.com
```

---

## Rollback Procedure

Each deployment creates a tagged image:

```bash
# List available images
docker images milkyhoop-frontend

# Example output:
# REPOSITORY           TAG                  SIZE
# milkyhoop-frontend   20251209-120000      25MB
# milkyhoop-frontend   20251209-100000      25MB
# milkyhoop-frontend   latest               25MB

# Rollback to specific version
docker stop milkyhoop-frontend-1
docker rm milkyhoop-frontend-1
docker run -d \
  --name milkyhoop-frontend-1 \
  --network milkyhoop_dev_network \
  -p 3000:80 \
  -p 443:443 \
  -v /root/milkyhoop/frontend/web/nginx-ssl.conf:/etc/nginx/conf.d/default.conf:ro \
  -v /etc/letsencrypt:/etc/letsencrypt:ro \
  --restart unless-stopped \
  milkyhoop-frontend:20251209-100000  # Use your tag
```

---

## Development Workflow

### Start Development Server

```bash
cd /root/milkyhoop/frontend/web
docker-compose -f docker-compose.dev.yml up -d

# View logs
docker-compose -f docker-compose.dev.yml logs -f

# Access at http://localhost:3000
```

### Stop Development Server

```bash
docker-compose -f docker-compose.dev.yml down
```

---

## Build Performance

| Scenario | Before Optimization | After Optimization |
|----------|--------------------|--------------------|
| Docker context size | ~500MB | ~133KB |
| Cached build | 3-4 min | 45-90 sec |
| Fresh build | 3-4 min | 2-3 min |
| Emergency hotfix | N/A | 10 sec |
| Development | Rebuild each change | Instant HMR |

**Optimization files:**
- `.dockerignore` - Excludes node_modules, build, backups from Docker context
- `cleanup.sh` - Removes backup files before build

---

## Useful Commands

```bash
# Check container status
docker ps | grep frontend

# View logs
docker logs -f milkyhoop-frontend-1

# Enter container shell
docker exec -it milkyhoop-frontend-1 sh

# Test nginx config
docker exec milkyhoop-frontend-1 nginx -t

# Check what's served
docker exec milkyhoop-frontend-1 ls -la /usr/share/nginx/html/static/js/
```

---

## Troubleshooting

### Build is slow (3-4 minutes)

1. Check if `.dockerignore` exists
2. Run `./cleanup.sh` to remove backup files
3. Use cached build (default) instead of `--fresh`

### Container keeps restarting

```bash
# Check logs
docker logs milkyhoop-frontend-1

# Common issues:
# - SSL cert missing → mount /etc/letsencrypt
# - nginx config error → check nginx-ssl.conf
```

### HTTPS not working (521 error)

1. Check SSL certificates exist:
   ```bash
   ls -la /etc/letsencrypt/live/milkyhoop.com/
   ```

2. Verify container has port 443:
   ```bash
   docker ps | grep frontend
   # Should show: 0.0.0.0:443->443/tcp
   ```

3. Check Cloudflare SSL mode (should be "Full" or "Full strict")

### API requests failing

1. Check backend is running:
   ```bash
   docker ps | grep api_gateway
   ```

2. Check network connectivity:
   ```bash
   docker exec milkyhoop-frontend-1 wget -qO- http://api_gateway:8000/health
   ```

---

## File Structure

```
/root/milkyhoop/frontend/web/
├── src/                    # React source code
├── public/                 # Static assets
├── build/                  # Production build (generated)
├── Dockerfile              # Docker build config
├── nginx-ssl.conf          # Nginx HTTPS configuration
├── nginx.conf              # Nginx HTTP configuration
├── docker-compose.dev.yml  # Development server
├── deploy-prod.sh          # Production deploy script
├── cleanup.sh              # Cleanup script
├── .dockerignore           # Docker build exclusions
├── .gitignore              # Git exclusions
└── README-DEPLOY.md        # Deployment guide
```

---

## Related Documentation

- Backend: `/root/milkyhoop-dev/` (separate repository)
- API Gateway: `milkyhoop-dev-api_gateway-1` container
- Database: `milkyhoop-dev-postgres-1` container
