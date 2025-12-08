# MilkyHoop Security Quick Reference Card

**Last Updated:** 7 Desember 2025
**Security Score:** 9.3/10

---

## 🔒 Security Status Check

```bash
# All-in-one status check
echo "=== SECURITY STATUS ===" && \
echo "Fail2ban:" && fail2ban-client status | head -5 && \
echo "" && echo "SSL Cert:" && openssl s_client -connect milkyhoop.com:443 2>/dev/null | openssl x509 -noout -dates && \
echo "" && echo "Containers:" && docker ps --format "{{.Names}}: {{.Status}}" | head -10
```

---

## 🛡️ Fail2ban Commands

```bash
# Status semua jails
fail2ban-client status

# Status jail spesifik
fail2ban-client status sshd
fail2ban-client status milkyhoop-auth
fail2ban-client status nginx-limit-req

# Unban IP
fail2ban-client set sshd unbanip 1.2.3.4
fail2ban-client set milkyhoop-auth unbanip 1.2.3.4

# Ban IP manual
fail2ban-client set sshd banip 1.2.3.4

# Restart fail2ban
systemctl restart fail2ban
```

---

## 🔐 SSL/HTTPS Commands

```bash
# Cek certificate expiry
openssl s_client -connect milkyhoop.com:443 -servername milkyhoop.com 2>/dev/null | openssl x509 -noout -dates

# Test SSL grade (online)
# https://www.ssllabs.com/ssltest/analyze.html?d=milkyhoop.com

# Renew certificate
certbot renew --dry-run  # Test
certbot renew            # Actual

# Force renewal
certbot renew --force-renewal

# List certificates
certbot certificates
```

---

## 🐳 Docker Security Commands

```bash
# Check container security options
docker inspect --format='SecurityOpt: {{.HostConfig.SecurityOpt}} | CapDrop: {{.HostConfig.CapDrop}}' milkyhoop-dev-api_gateway-1

# View resource limits
docker stats --no-stream

# Restart dengan config baru
docker compose up -d --force-recreate

# Check for running as root
docker exec milkyhoop-dev-api_gateway-1 whoami
```

---

## 📊 Log Analysis

```bash
# Auth failures (last 50)
tail -50 /var/log/milkyhoop/auth.log | grep "AUTH_FAILED"

# WAF blocks
docker logs milkyhoop-dev-api_gateway-1 2>&1 | grep "WAF" | tail -20

# Rate limit hits
docker logs milkyhoop-dev-api_gateway-1 2>&1 | grep "RATE_LIMIT" | tail -20

# Top attacking IPs
grep "AUTH_FAILED" /var/log/milkyhoop/auth.log | awk '{print $NF}' | sort | uniq -c | sort -rn | head -10
```

---

## 🔥 Firewall (UFW)

```bash
# Status
ufw status numbered

# Block IP
ufw deny from 1.2.3.4

# Unblock IP
ufw delete deny from 1.2.3.4

# Allow port
ufw allow 8080/tcp

# Reload
ufw reload
```

---

## 🚨 Emergency Response

### DDoS Attack
```bash
# 1. Lower rate limits immediately
docker exec milkyhoop-dev-api_gateway-1 env RATE_LIMIT_REQUESTS=10

# 2. Block attacking subnet
ufw deny from 1.2.3.0/24

# 3. Enable WAF strict mode (requires code change)
# waf_middleware.py: strict_mode=True
```

### Brute Force Attack
```bash
# 1. Find attackers
grep "AUTH_FAILED" /var/log/milkyhoop/auth.log | tail -100

# 2. Ban immediately
fail2ban-client set milkyhoop-auth banip <IP>

# 3. Lower auth limits
# RATE_LIMIT_AUTH_REQUESTS=3
```

### Suspected Breach
```bash
# 1. Preserve logs
cp -r /var/log/milkyhoop /evidence/$(date +%s)/

# 2. Rotate all secrets
# - JWT_SECRET
# - DB_PASSWORD
# - REDIS_PASSWORD
# - FLE_PRIMARY_KEK

# 3. Invalidate sessions
redis-cli -a $REDIS_PASSWORD FLUSHDB

# 4. Restart all services
docker compose down && docker compose up -d
```

---

## 🔑 Secret Management (SOPS + age)

```bash
# Encrypt .env
./scripts/secrets.sh encrypt

# Decrypt .env
./scripts/secrets.sh decrypt

# Edit encrypted secrets in-place
./scripts/secrets.sh edit

# Show public key
./scripts/secrets.sh show-public-key

# Manual encrypt/decrypt
sops --encrypt --input-type dotenv .env > .env.encrypted
sops --decrypt --input-type dotenv .env.encrypted > .env
```

**Key Location:** `/root/.config/sops/age/keys.txt`

---

## 💾 Encrypted Backup

```bash
# Run manual backup
./backups/backup_encrypted.sh

# Restore from backup
./backups/restore_encrypted.sh <backup_file.sql.gz.age>

# List backups
ls -lht /root/milkyhoop-dev/backups/*.age

# Verify backup can be decrypted
age --decrypt -i /root/.config/sops/age/keys.txt <backup.age> | gunzip | head
```

**Cron:** Daily 2 AM → `/var/log/milkyhoop/backup.log`
**Retention:** 30 days encrypted, 7 days unencrypted

---

## ☁️ Cloudflare

```bash
# Check if traffic goes through Cloudflare
curl -sI https://milkyhoop.com | grep -i "cf-ray\|server"

# Verify nameservers
dig +short NS milkyhoop.com

# Test via Cloudflare IP
curl -sI --resolve milkyhoop.com:443:104.21.70.193 https://milkyhoop.com
```

**Dashboard:** https://dash.cloudflare.com
**SSL Mode:** Full (strict)
**Features:** DDoS, Bot Fight Mode, Edge SSL

---

## 📈 Grafana Dashboard

```bash
# Access via SSH tunnel
ssh -L 3000:localhost:3000 root@milkyhoop.com

# Then open browser
# URL: http://localhost:3000
# User: admin
# Pass: milkyhoop2025

# Check Loki health
curl -s http://localhost:3100/ready

# Query logs via Loki
curl -s "http://localhost:3100/loki/api/v1/query?query={container_name=~\"milkyhoop.*\"}" | jq
```

---

## 📋 Daily Checklist

- [ ] Check fail2ban status: `fail2ban-client status`
- [ ] Check container health: `docker ps`
- [ ] Review auth logs: `tail -100 /var/log/milkyhoop/auth.log`
- [ ] Check SSL cert (if < 30 days): `certbot certificates`

## 📋 Weekly Checklist

- [ ] Run security scan: `./scripts/security_scan.sh`
- [ ] Check for OS updates: `apt update && apt list --upgradable`
- [ ] Review banned IPs: `fail2ban-client get sshd banned`
- [ ] Backup configs: `cp -r /etc/fail2ban /backup/`

---

## 📞 Contacts

| Issue | Action |
|-------|--------|
| SSL cert issue | `certbot renew` or contact Let's Encrypt |
| DDoS | Contact DigitalOcean support |
| Data breach | Follow incident response procedure |

---

## 🔗 Useful Links

- SSL Labs Test: https://www.ssllabs.com/ssltest/
- Security Headers Test: https://securityheaders.com/
- Let's Encrypt Status: https://letsencrypt.status.io/
- OWASP Top 10: https://owasp.org/Top10/

---

*Keep this card handy for quick security operations!*
