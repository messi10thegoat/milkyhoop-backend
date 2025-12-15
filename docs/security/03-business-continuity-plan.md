# Business Continuity Plan
## MilkyHoop Platform

| Document ID | ISMS-PRC-002 |
|-------------|--------------|
| Version | 1.0 |
| Effective Date | 2025-12-15 |
| Review Date | 2026-06-15 |
| Classification | Confidential |
| ISO Control | A.5.29-30 Business Continuity |

---

## 1. Purpose

This Business Continuity Plan (BCP) ensures MilkyHoop can maintain critical business operations during and after a disaster or significant disruption.

## 2. Scope

This plan covers:
- Platform infrastructure (DigitalOcean)
- Application services (Docker containers)
- Database systems (PostgreSQL)
- External dependencies (OpenAI API)
- Support operations

## 3. Business Impact Analysis

### 3.1 Critical Business Functions

| Function | RTO | RPO | Priority | Dependencies |
|----------|-----|-----|----------|--------------|
| API Gateway | 1h | 1h | P1 | PostgreSQL, Redis |
| Authentication | 1h | 1h | P1 | PostgreSQL, Redis |
| Transaction Processing | 2h | 4h | P1 | PostgreSQL, LLM |
| Tenant Chat | 4h | 24h | P2 | All services |
| Reporting | 8h | 24h | P3 | PostgreSQL |

**Legend:**
- RTO: Recovery Time Objective (max downtime)
- RPO: Recovery Point Objective (max data loss)

### 3.2 Impact Assessment

| Downtime | Financial Impact | Reputational Impact | Operational Impact |
|----------|------------------|---------------------|-------------------|
| < 1 hour | Low | Low | Minimal |
| 1-4 hours | Medium | Medium | Significant |
| 4-24 hours | High | High | Severe |
| > 24 hours | Critical | Critical | Business threatening |

## 4. Risk Scenarios

### Scenario 1: Infrastructure Failure
- **Cause:** DigitalOcean datacenter outage
- **Impact:** Complete service unavailability
- **Probability:** Low
- **Recovery Strategy:** Failover to backup region (if configured)

### Scenario 2: Database Corruption
- **Cause:** Software bug, hardware failure, cyber attack
- **Impact:** Data loss, service unavailability
- **Probability:** Medium
- **Recovery Strategy:** Restore from Restic backup

### Scenario 3: Cyber Attack (Ransomware)
- **Cause:** Malware infection
- **Impact:** Data encryption, potential data theft
- **Probability:** Medium
- **Recovery Strategy:** Isolate, restore from clean backup

### Scenario 4: Third-Party Service Failure
- **Cause:** OpenAI API outage
- **Impact:** LLM features unavailable
- **Probability:** Medium
- **Recovery Strategy:** Graceful degradation, queue requests

### Scenario 5: Key Personnel Unavailable
- **Cause:** Illness, resignation
- **Impact:** Delayed response
- **Probability:** Medium
- **Recovery Strategy:** Cross-training, documentation

## 5. Recovery Strategies

### 5.1 Infrastructure Recovery

```
┌─────────────────────────────────────────────────────────┐
│                  RECOVERY PRIORITY                       │
├─────────────────────────────────────────────────────────┤
│ Priority 1 (0-1h):                                      │
│   - PostgreSQL Database                                 │
│   - Redis Cache                                         │
│   - Auth Service                                        │
│   - API Gateway                                         │
├─────────────────────────────────────────────────────────┤
│ Priority 2 (1-4h):                                      │
│   - Transaction Service                                 │
│   - Inventory Service                                   │
│   - Accounting Service                                  │
├─────────────────────────────────────────────────────────┤
│ Priority 3 (4-8h):                                      │
│   - Tenant Orchestrator                                 │
│   - Chatbot Service                                     │
│   - Reporting Service                                   │
└─────────────────────────────────────────────────────────┘
```

### 5.2 Database Recovery Procedure

```bash
# Step 1: Assess damage
docker logs milkyhoop-dev-postgres-1 --tail 100

# Step 2: Stop dependent services
docker compose stop api_gateway auth_service

# Step 3: Restore from backup
source /root/.config/restic/credentials.env
restic snapshots --latest 5  # List recent backups
./backups/restic_restore.sh <snapshot_id>

# Step 4: Verify data integrity
docker exec milkyhoop-dev-postgres-1 psql -U postgres -d milkydb \
  -c "SELECT COUNT(*) FROM transactions;"

# Step 5: Restart services
docker compose up -d
```

### 5.3 Full System Recovery (Bare Metal)

```bash
# On new DigitalOcean droplet:

# 1. Install prerequisites
apt-get update && apt-get install -y docker.io docker-compose restic

# 2. Restore configuration
# Copy age keys from secure backup
mkdir -p /root/.config/sops/age
# [Restore keys.txt from secure location]

# 3. Clone repository
git clone https://github.com/[org]/milkyhoop.git /root/milkyhoop-dev

# 4. Restore secrets
source /root/.config/restic/credentials.env
restic restore latest --target /tmp/restore

# 5. Restore database
# [Follow database recovery procedure]

# 6. Start services
cd /root/milkyhoop-dev
docker compose up -d

# 7. Verify
curl https://milkyhoop.com/healthz
```

## 6. Communication Plan

### 6.1 Internal Notification

| Event | Notify | Method | Within |
|-------|--------|--------|--------|
| Outage detected | Technical team | Slack/Phone | 5 min |
| P1 incident | Management | Phone call | 15 min |
| Extended outage (>1h) | All staff | Email | 1 hour |

### 6.2 External Notification

| Event | Notify | Method | Template |
|-------|--------|--------|----------|
| Service disruption | Affected tenants | In-app + Email | Template A |
| Extended outage (>4h) | All tenants | Email + Status page | Template B |
| Data incident | Regulatory bodies | Official letter | Template C |

### 6.3 Status Page Updates

Maintain status updates at: `status.milkyhoop.com` (or equivalent)

Status levels:
- 🟢 Operational
- 🟡 Degraded Performance
- 🟠 Partial Outage
- 🔴 Major Outage

## 7. Backup Strategy

### 7.1 Current Backup Schedule

| Type | Schedule | Retention | Location |
|------|----------|-----------|----------|
| Local (age encrypted) | Daily 02:00 | 30 days | /root/milkyhoop-dev/backups |
| Restic (AES-256) | Daily 02:30 | 7D/4W/12M/2Y | /mnt/backups/milkyhoop-encrypted |
| DO Managed PostgreSQL | Continuous | 7 days | DigitalOcean |

### 7.2 Backup Verification

| Test | Frequency | Owner | Last Tested |
|------|-----------|-------|-------------|
| Backup completion check | Daily (automated) | System | Continuous |
| Restore test (sample) | Monthly | DevOps | ____/____/____ |
| Full DR test | Quarterly | Security + DevOps | ____/____/____ |

## 8. Roles and Responsibilities

### 8.1 BCP Team

| Role | Responsibilities | Primary | Backup |
|------|------------------|---------|--------|
| BCP Coordinator | Overall coordination | [Name] | [Name] |
| Technical Lead | System recovery | [Name] | [Name] |
| Communications | Stakeholder updates | [Name] | [Name] |
| Operations | Business operations | [Name] | [Name] |

### 8.2 Decision Authority

| Decision | Authority |
|----------|-----------|
| Invoke BCP | BCP Coordinator or Management |
| Failover to backup | Technical Lead |
| External communication | Communications Lead + Management |
| Return to normal | BCP Coordinator + Technical Lead |

## 9. Testing Schedule

| Test Type | Frequency | Scope | Duration |
|-----------|-----------|-------|----------|
| Tabletop exercise | Quarterly | All scenarios | 2 hours |
| Component recovery | Monthly | Single service | 1 hour |
| Full DR test | Annually | Complete failover | 4 hours |

## 10. Plan Maintenance

### 10.1 Review Triggers
- After any BCP activation
- After significant infrastructure changes
- After organizational changes
- Annually at minimum

### 10.2 Update Process
1. Identify required changes
2. Update documentation
3. Communicate changes to team
4. Update training materials
5. Re-test affected procedures

## 11. Appendices

### Appendix A: Emergency Contact List

| Role | Name | Phone | Email |
|------|------|-------|-------|
| BCP Coordinator | __________ | __________ | __________ |
| Technical Lead | __________ | __________ | __________ |
| CEO | __________ | __________ | __________ |
| DigitalOcean Support | N/A | N/A | support@digitalocean.com |

### Appendix B: Vendor Contacts

| Vendor | Service | Support Contact |
|--------|---------|-----------------|
| DigitalOcean | Infrastructure | support@digitalocean.com |
| OpenAI | LLM API | support@openai.com |
| Cloudflare | CDN/Security | (if applicable) |

### Appendix C: Recovery Checklist

```
□ Incident identified and classified
□ BCP team notified
□ Initial assessment completed
□ Recovery priority determined
□ Communication sent to stakeholders
□ Recovery procedures initiated
□ Systems restored and verified
□ Data integrity confirmed
□ Services brought online
□ Monitoring enhanced
□ All-clear communicated
□ Post-incident review scheduled
```

---

## Approval

| Role | Name | Signature | Date |
|------|------|-----------|------|
| CEO | _________________ | _________________ | ____/____/____ |
| BCP Coordinator | _________________ | _________________ | ____/____/____ |

---

*Document Control: This plan must be tested and reviewed at least annually.*
