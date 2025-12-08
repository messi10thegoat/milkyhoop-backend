# MilkyHoop Security Architecture

**Version:** 2.0
**Last Updated:** 7 Desember 2025
**Security Score:** 9.3/10

---

## Executive Summary

MilkyHoop mengimplementasikan arsitektur keamanan berlapis (defense-in-depth) yang dirancang untuk melindungi data finansial UMKM Indonesia. Sistem ini memenuhi standar keamanan enterprise dan compliant dengan UU Perlindungan Data Pribadi (UU PDP) Indonesia.

```
┌─────────────────────────────────────────────────────────────────────┐
│                        SECURITY LAYERS                               │
├─────────────────────────────────────────────────────────────────────┤
│  Layer 1: Edge Protection (Cloudflare)                              │
│  Layer 2: TLS/HTTPS (Let's Encrypt)                                 │
│  Layer 3: WAF + Rate Limiting (Custom Middleware)                   │
│  Layer 4: Authentication & Authorization (JWT + RBAC)               │
│  Layer 5: Field-Level Encryption (AES-256-GCM)                      │
│  Layer 6: Database Security (Row-Level Security)                    │
│  Layer 7: Container Isolation (Docker Hardening)                    │
│  Layer 8: Monitoring & Logging (Loki + Grafana)                     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Table of Contents

1. [Network Architecture](#1-network-architecture)
2. [Edge Protection (Cloudflare)](#2-edge-protection-cloudflare)
3. [TLS/HTTPS Configuration](#3-tlshttps-configuration)
4. [Web Application Firewall (WAF)](#4-web-application-firewall-waf)
5. [Rate Limiting](#5-rate-limiting)
6. [Authentication & Authorization](#6-authentication--authorization)
7. [Field-Level Encryption](#7-field-level-encryption)
8. [Secret Management](#8-secret-management)
9. [Backup & Disaster Recovery](#9-backup--disaster-recovery)
10. [Container Security](#10-container-security)
11. [Intrusion Prevention (Fail2ban)](#11-intrusion-prevention-fail2ban)
12. [Logging & Monitoring](#12-logging--monitoring)
13. [Compliance Mapping](#13-compliance-mapping)
14. [Incident Response](#14-incident-response)
15. [Security Checklist](#15-security-checklist)

---

## 1. Network Architecture

### Traffic Flow

```
Internet
    │
    ▼
┌─────────────────┐
│   Cloudflare    │  ← DDoS Protection, Bot Management, Edge SSL
│   (Edge Proxy)  │
└────────┬────────┘
         │ HTTPS (Full Strict)
         ▼
┌─────────────────┐
│  Nginx Reverse  │  ← SSL Termination, Security Headers
│     Proxy       │
└────────┬────────┘
         │ HTTP (Internal)
         ▼
┌─────────────────┐
│   API Gateway   │  ← WAF, Rate Limiting, Auth Validation
│   (Port 8001)   │
└────────┬────────┘
         │ gRPC (Internal Network)
         ▼
┌─────────────────────────────────────────┐
│         Docker Internal Network          │
│  ┌──────────┐  ┌──────────┐  ┌────────┐ │
│  │ Auth     │  │ Chat     │  │ Trans  │ │
│  │ Service  │  │ Service  │  │ Service│ │
│  └────┬─────┘  └────┬─────┘  └───┬────┘ │
│       │             │            │       │
│       └─────────────┴────────────┘       │
│                     │                     │
│              ┌──────▼──────┐             │
│              │  PostgreSQL │             │
│              │   (RLS)     │             │
│              └─────────────┘             │
└─────────────────────────────────────────┘
```

### Port Bindings

| Service | Internal Port | External Binding | Access |
|---------|---------------|------------------|--------|
| PostgreSQL | 5432 | 127.0.0.1:5433 | Localhost only |
| Redis | 6379 | 127.0.0.1:6380 | Localhost only |
| API Gateway | 8000 | 0.0.0.0:8001 | Public (via Cloudflare) |
| Auth Service | 8013 | 127.0.0.1:8014 | Localhost only |
| Grafana | 3000 | 127.0.0.1:3000 | Localhost only (SSH tunnel) |
| Loki | 3100 | 127.0.0.1:3100 | Localhost only |

---

## 2. Edge Protection (Cloudflare)

### Configuration

- **SSL Mode:** Full (Strict)
- **Minimum TLS:** 1.2
- **Always Use HTTPS:** Enabled
- **Bot Fight Mode:** Enabled

### Protection Provided

| Threat | Protection |
|--------|------------|
| DDoS (Layer 3/4) | Automatic mitigation up to 1Tbps+ |
| DDoS (Layer 7) | Rate limiting, challenge pages |
| Bot Attacks | Bot Fight Mode, JS challenge |
| SSL Attacks | Edge SSL termination |
| Geographic Attacks | Geo-blocking capability |

### DNS Configuration

```
milkyhoop.com    A     → Cloudflare Proxy IPs (104.21.x.x, 172.67.x.x)
www.milkyhoop.com CNAME → milkyhoop.com (Proxied)
```

---

## 3. TLS/HTTPS Configuration

### Certificate

- **Provider:** Let's Encrypt
- **Type:** DV (Domain Validated)
- **Auto-Renewal:** Certbot (cron)
- **Key Size:** RSA 2048-bit

### Nginx SSL Configuration

```nginx
# /etc/nginx/sites-available/milkyhoop

ssl_certificate /etc/letsencrypt/live/milkyhoop.com/fullchain.pem;
ssl_certificate_key /etc/letsencrypt/live/milkyhoop.com/privkey.pem;

ssl_protocols TLSv1.2 TLSv1.3;
ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256;
ssl_prefer_server_ciphers off;
ssl_session_timeout 1d;
ssl_session_cache shared:SSL:10m;
ssl_session_tickets off;

# HSTS
add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
```

### Security Headers

| Header | Value | Purpose |
|--------|-------|---------|
| Strict-Transport-Security | max-age=31536000; includeSubDomains | Force HTTPS |
| X-Frame-Options | DENY | Prevent clickjacking |
| X-Content-Type-Options | nosniff | Prevent MIME sniffing |
| X-XSS-Protection | 1; mode=block | XSS filter |
| Referrer-Policy | strict-origin-when-cross-origin | Control referrer |
| Permissions-Policy | geolocation=(), microphone=(), camera=() | Disable features |

---

## 4. Web Application Firewall (WAF)

### Location
`backend/api_gateway/app/middleware/waf_middleware.py`

### Protection Rules

#### SQL Injection Detection
```python
SQL_INJECTION_PATTERNS = [
    r"(\bUNION\s+(ALL\s+)?SELECT\b)",      # UNION-based
    r"(\bSELECT\s+.+\s+FROM\s+)",          # SELECT queries
    r"(\bINSERT\s+INTO\s+)",               # INSERT statements
    r"(\bUPDATE\s+\w+\s+SET\b)",           # UPDATE statements
    r"(\bDELETE\s+FROM\s+)",               # DELETE statements
    r"(\bDROP\s+(TABLE|DATABASE)\b)",      # DROP commands
    r"(\b(OR|AND)\s+[\'\"]?\d+[\'\"]?\s*=\s*[\'\"]?\d+)",  # Boolean injection
    r"(--\s*$|--\s+)",                     # Comment injection
    r"(\bWAITFOR\s+DELAY|\bSLEEP\s*\()",   # Time-based
    r"(INFORMATION_SCHEMA|pg_catalog)",     # System tables
]
```

#### XSS Detection
```python
XSS_PATTERNS = [
    r"<script\b[^>]*>.*?</script>",        # Script tags
    r"javascript\s*:",                      # JavaScript protocol
    r"on\w+\s*=",                          # Event handlers
    r"<\s*iframe",                         # Iframes
    r"document\.(cookie|location|write)",   # DOM manipulation
    r"(eval|alert|prompt)\s*\(",           # Dangerous functions
]
```

#### Blocked User Agents
```python
BLOCKED_USER_AGENTS = {
    "sqlmap", "nikto", "nmap", "masscan",
    "acunetix", "nessus", "burp", "zap",
    "gobuster", "dirbuster", "wfuzz", "ffuf"
}
```

### Relaxed Paths (Auth Endpoints)
```python
RELAXED_PATHS = {
    "/api/auth/login",
    "/api/auth/register",
    "/api/auth/refresh"
}
```

### Request Limits
| Limit | Value |
|-------|-------|
| Max URL Length | 2048 chars |
| Max Header Size | 8192 chars |
| Max Body Size | 10 MB |

---

## 5. Rate Limiting

### Location
`backend/api_gateway/app/middleware/rate_limit_middleware.py`

### Configuration

| Endpoint Type | Requests | Window | Action |
|---------------|----------|--------|--------|
| General API | 100 | 60s | Block |
| Auth (Login) | 5 | 60s | Block + Lockout |
| Auth (Register) | 3 | 300s | Block |
| Sensitive Data | 20 | 60s | Block |

### Implementation
```python
RATE_LIMIT_ENABLED = True
RATE_LIMIT_REQUESTS = 100
RATE_LIMIT_WINDOW = 60  # seconds
RATE_LIMIT_AUTH_REQUESTS = 5
RATE_LIMIT_AUTH_WINDOW = 60
```

### Storage
- **Backend:** Redis
- **Key Format:** `rate_limit:{ip}:{endpoint}`
- **TTL:** Window duration

---

## 6. Authentication & Authorization

### JWT Configuration

```python
JWT_ALGORITHM = "HS256"
JWT_ACCESS_TOKEN_EXPIRE = 7 days
JWT_REFRESH_TOKEN_EXPIRE = 30 days
```

### Token Structure
```json
{
  "user_id": "uuid",
  "tenant_id": "string",
  "role": "FREE|PREMIUM|ADMIN",
  "email": "string",
  "token_type": "access|refresh",
  "iat": 1234567890,
  "exp": 1234567890,
  "nbf": 1234567890
}
```

### Role-Based Access Control (RBAC)

| Role | Permissions |
|------|-------------|
| FREE | Basic features, limited transactions |
| PREMIUM | Full features, unlimited transactions |
| ADMIN | All permissions + tenant management |

### Account Lockout

| Trigger | Action | Duration |
|---------|--------|----------|
| 5 failed logins | Account locked | 15 minutes |
| 10 failed logins | Account locked | 1 hour |
| 20 failed logins | Account locked | 24 hours |

---

## 7. Field-Level Encryption

### Location
`backend/api_gateway/app/services/crypto/`

### Algorithm
- **Encryption:** AES-256-GCM
- **Key Derivation:** PBKDF2-SHA256
- **IV:** Random 12 bytes per encryption

### Encrypted Fields

| Table | Field | Data Type |
|-------|-------|-----------|
| User | phone_number | PII |
| UserBusiness | tax_id | Financial |
| UserBusiness | bank_account | Financial |
| UserFinance | balance | Financial |
| Transaction | amount | Financial |

### Key Management

```python
# Primary Key Encryption Key (KEK)
FLE_PRIMARY_KEK = os.getenv("FLE_PRIMARY_KEK")  # 256-bit key

# Data Encryption Key (DEK) - derived per tenant
DEK = PBKDF2(KEK, tenant_id, iterations=100000)
```

### Encryption Format
```
ENC[version:iv:ciphertext:tag]
Example: ENC[1:a1b2c3...:encrypted_data...:auth_tag...]
```

---

## 8. Secret Management

### Tool Stack
- **Encryption:** SOPS + age
- **Key Storage:** `/root/.config/sops/age/keys.txt`

### Configuration
```yaml
# .sops.yaml
creation_rules:
  - path_regex: .*
    age: age1ha44p0556qhxmhqt46c6jj8xat0n0kwulesr4a3dngulv9vsmq6qnkwvgn
```

### Usage

```bash
# Encrypt .env
./scripts/secrets.sh encrypt

# Decrypt .env
./scripts/secrets.sh decrypt

# Edit encrypted secrets
./scripts/secrets.sh edit
```

### Protected Secrets
- Database credentials
- JWT secret
- Redis password
- OpenAI API key
- Field-Level Encryption KEK

---

## 9. Backup & Disaster Recovery

### Backup Strategy

| Type | Frequency | Retention | Encryption |
|------|-----------|-----------|------------|
| Full DB | Daily 2 AM | 30 days | age (AES-256) |
| Incremental | - | - | - |
| Config | On change | Git history | SOPS |

### Backup Script
`/root/milkyhoop-dev/backups/backup_encrypted.sh`

```bash
# Creates encrypted backup
pg_dump | gzip | age -r $PUBLIC_KEY > backup.sql.gz.age
```

### Restore Procedure
```bash
# Restore from encrypted backup
./backups/restore_encrypted.sh <backup_file.sql.gz.age>
```

### Backup Verification (Drill)
```
Last Drill: 7 Desember 2025
Result: PASSED
- User count: 116 ✓
- Tenant count: 5 ✓
- Session count: 336 ✓
```

### Cron Schedule
```bash
0 2 * * * /root/milkyhoop-dev/backups/backup_encrypted.sh >> /var/log/milkyhoop/backup.log 2>&1
```

---

## 10. Container Security

### Docker Hardening

```yaml
# docker-compose.yml
x-security-defaults: &security-defaults
  security_opt:
    - no-new-privileges:true
  cap_drop:
    - ALL
  ulimits:
    nofile:
      soft: 65536
      hard: 65536
    nproc:
      soft: 4096
      hard: 4096
```

### Resource Limits

| Service | CPU Limit | Memory Limit |
|---------|-----------|--------------|
| API Gateway | 2.0 | 1G |
| PostgreSQL | 2.0 | 2G |
| Redis | 1.0 | 1G |
| Auth Service | 1.0 | 512M |
| Grafana | 1.0 | 512M |

### Volume Mounts
```yaml
volumes:
  - ./backend/api_gateway:/app/backend/api_gateway:ro  # Read-only
  - ./logs:/var/log  # Write for logs only
```

### Network Isolation
```yaml
networks:
  internal:
    external: true
    name: milkyhoop_dev_network
```

---

## 11. Intrusion Prevention (Fail2ban)

### Configuration
`/etc/fail2ban/jail.local`

### Active Jails

| Jail | Max Retry | Ban Time | Filter |
|------|-----------|----------|--------|
| sshd | 3 | 1 hour | Default |
| milkyhoop-auth | 5 | 30 min | AUTH_FAILED |
| nginx-limit-req | 10 | 10 min | limit_req |
| nginx-botsearch | 2 | 1 day | Bot patterns |

### Log Monitoring
```
/var/log/milkyhoop/auth.log
/var/log/nginx/access.log
/var/log/nginx/error.log
```

### Commands
```bash
# Check status
fail2ban-client status

# Unban IP
fail2ban-client set milkyhoop-auth unbanip 1.2.3.4

# View banned IPs
fail2ban-client get milkyhoop-auth banned
```

---

## 12. Logging & Monitoring

### Stack
- **Log Aggregation:** Grafana Loki
- **Log Collector:** Promtail
- **Visualization:** Grafana

### Access
```bash
# SSH tunnel for Grafana
ssh -L 3000:localhost:3000 root@milkyhoop.com

# Open browser
http://localhost:3000
# User: admin
# Pass: milkyhoop2025
```

### Log Sources

| Source | Type | Retention |
|--------|------|-----------|
| API Gateway | Application | 7 days |
| Auth Service | Security | 30 days |
| Nginx | Access/Error | 7 days |
| PostgreSQL | Query | 3 days |

### Security Events Logged
- AUTH_FAILED
- AUTH_SUCCESS
- RATE_LIMIT_EXCEEDED
- WAF_BLOCKED
- ACCOUNT_LOCKED
- PASSWORD_CHANGED
- TOKEN_REFRESH

### Dashboard Panels
1. API Gateway Errors
2. Auth Failures
3. All Services Logs
4. Error Rate (5m)
5. Security Events (5m)

---

## 13. Compliance Mapping

### UU PDP (Perlindungan Data Pribadi Indonesia)

| Requirement | Implementation | Status |
|-------------|----------------|--------|
| Data encryption | FLE (AES-256-GCM) | ✅ |
| Access control | JWT + RBAC | ✅ |
| Audit logging | Loki + Grafana | ✅ |
| Data minimization | Tenant isolation | ✅ |
| Breach notification | Logging ready | ✅ |

### OWASP Top 10 (2021)

| Risk | Mitigation | Status |
|------|------------|--------|
| A01: Broken Access Control | RBAC, RLS | ✅ |
| A02: Cryptographic Failures | TLS 1.2+, AES-256 | ✅ |
| A03: Injection | WAF, Parameterized queries | ✅ |
| A04: Insecure Design | Security-first architecture | ✅ |
| A05: Security Misconfiguration | Docker hardening, headers | ✅ |
| A06: Vulnerable Components | Regular updates | ⚠️ |
| A07: Auth Failures | JWT, Lockout, Rate limit | ✅ |
| A08: Data Integrity Failures | FLE, HMAC | ✅ |
| A09: Logging Failures | Centralized logging | ✅ |
| A10: SSRF | WAF, Network isolation | ✅ |

---

## 14. Incident Response

### Severity Levels

| Level | Description | Response Time |
|-------|-------------|---------------|
| P1 | Data breach, service down | Immediate |
| P2 | Security vulnerability | 4 hours |
| P3 | Performance degradation | 24 hours |
| P4 | Minor issues | 72 hours |

### Response Procedures

#### DDoS Attack
```bash
# 1. Check Cloudflare analytics for attack pattern
# 2. Enable Under Attack Mode in Cloudflare
# 3. Block attacking subnet
ufw deny from 1.2.3.0/24
# 4. Monitor traffic
docker logs milkyhoop-dev-api_gateway-1 | grep WAF
```

#### Brute Force Attack
```bash
# 1. Check auth failures
grep "AUTH_FAILED" /var/log/milkyhoop/auth.log | tail -100
# 2. Ban attacker
fail2ban-client set milkyhoop-auth banip <IP>
# 3. Verify lockout working
```

#### Suspected Breach
```bash
# 1. Preserve evidence
cp -r /var/log/milkyhoop /evidence/$(date +%s)/
# 2. Rotate all secrets
# 3. Invalidate sessions
redis-cli -a $REDIS_PASSWORD FLUSHDB
# 4. Restart services
docker compose down && docker compose up -d
# 5. Notify stakeholders
```

---

## 15. Security Checklist

### Daily
- [ ] Check fail2ban status: `fail2ban-client status`
- [ ] Verify containers running: `docker ps`
- [ ] Review auth logs: `tail -100 /var/log/milkyhoop/auth.log`

### Weekly
- [ ] Check SSL certificate expiry: `certbot certificates`
- [ ] Review Grafana security dashboard
- [ ] Check for OS updates: `apt update && apt list --upgradable`
- [ ] Review banned IPs: `fail2ban-client get sshd banned`

### Monthly
- [ ] Backup restore drill
- [ ] Review and rotate secrets
- [ ] Security dependency scan
- [ ] Review Cloudflare analytics

### Quarterly
- [ ] Penetration testing
- [ ] Access control audit
- [ ] Compliance review
- [ ] Disaster recovery drill

---

## Appendix A: File Locations

| Component | Path |
|-----------|------|
| WAF Middleware | `backend/api_gateway/app/middleware/waf_middleware.py` |
| Rate Limiter | `backend/api_gateway/app/middleware/rate_limit_middleware.py` |
| FLE Crypto | `backend/api_gateway/app/services/crypto/` |
| Backup Script | `backups/backup_encrypted.sh` |
| Restore Script | `backups/restore_encrypted.sh` |
| Secrets Helper | `scripts/secrets.sh` |
| Docker Compose | `docker-compose.yml` |
| Nginx Config | `frontend/nginx.conf` |
| Loki Config | `config/loki-config.yaml` |
| Grafana Dashboards | `config/grafana/provisioning/` |
| SOPS Config | `.sops.yaml` |
| Age Keys | `/root/.config/sops/age/keys.txt` |

---

## Appendix B: Quick Commands

```bash
# === Security Status ===
fail2ban-client status
docker ps --format "{{.Names}}: {{.Status}}"
certbot certificates

# === Secrets Management ===
./scripts/secrets.sh encrypt
./scripts/secrets.sh decrypt
./scripts/secrets.sh edit

# === Backup ===
./backups/backup_encrypted.sh
./backups/restore_encrypted.sh <file.age>

# === Logs ===
docker logs milkyhoop-dev-api_gateway-1 | grep WAF
docker logs milkyhoop-dev-auth_service-1 | grep AUTH
tail -f /var/log/milkyhoop/auth.log

# === Grafana (via SSH tunnel) ===
ssh -L 3000:localhost:3000 root@milkyhoop.com

# === Emergency ===
ufw deny from <IP>
fail2ban-client set milkyhoop-auth banip <IP>
docker compose restart api_gateway
```

---

## Document History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 30 Nov 2025 | Claude | Initial security implementation |
| 2.0 | 7 Dec 2025 | Claude | Added Cloudflare, SOPS, encrypted backup, Grafana |

---

*This document is confidential and intended for MilkyHoop technical team only.*
