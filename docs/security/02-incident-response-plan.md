# Incident Response Plan
## MilkyHoop Platform

| Document ID | ISMS-PRC-001 |
|-------------|--------------|
| Version | 1.0 |
| Effective Date | 2025-12-15 |
| Review Date | 2026-06-15 |
| Classification | Confidential |
| ISO Control | A.5.24-28 Information Security Incident Management |

---

## 1. Purpose

This Incident Response Plan (IRP) establishes procedures for detecting, responding to, and recovering from information security incidents affecting the MilkyHoop platform and its tenants.

## 2. Scope

This plan covers:
- Security breaches and data leaks
- System compromises and malware
- Denial of service attacks
- Unauthorized access attempts
- Data integrity incidents
- Availability incidents

## 3. Incident Classification

### 3.1 Severity Levels

| Level | Name | Description | Response Time | Examples |
|-------|------|-------------|---------------|----------|
| P1 | Critical | Service down, data breach confirmed | 15 minutes | Data exfiltration, ransomware |
| P2 | High | Partial service impact, breach suspected | 1 hour | DDoS, unauthorized admin access |
| P3 | Medium | Contained threat, no data impact | 4 hours | Failed intrusion, malware blocked |
| P4 | Low | Minor security event | 24 hours | Policy violation, phishing attempt |

### 3.2 Incident Categories

| Category | Code | Examples |
|----------|------|----------|
| Data Breach | DB | Unauthorized data access/exfiltration |
| System Compromise | SC | Malware, backdoor, rootkit |
| Denial of Service | DOS | DDoS, resource exhaustion |
| Unauthorized Access | UA | Credential theft, privilege escalation |
| Insider Threat | IT | Malicious employee, data theft |
| Physical Security | PS | Unauthorized facility access |

## 4. Incident Response Team (IRT)

### 4.1 Team Structure

| Role | Responsibilities | Contact |
|------|------------------|---------|
| Incident Commander | Overall coordination, decisions | [Primary Contact] |
| Security Lead | Technical investigation, containment | [Security Contact] |
| Communications Lead | Internal/external communications | [Comms Contact] |
| Technical Lead | System recovery, remediation | [Tech Contact] |
| Legal Advisor | Regulatory compliance, legal matters | [Legal Contact] |

### 4.2 Escalation Matrix

```
P4 (Low)     → Security Lead → 24h assessment
P3 (Medium)  → Security Lead + Technical Lead → 4h assessment
P2 (High)    → Full IRT activation → 1h response
P1 (Critical) → Full IRT + Management + Legal → 15m response
```

## 5. Incident Response Phases

### Phase 1: Detection & Identification (DETECT)

**Objective:** Identify and confirm security incident

**Actions:**
1. Monitor security alerts (Loki, WAF logs)
2. Analyze alert context and indicators
3. Confirm incident vs false positive
4. Classify severity level
5. Document initial findings

**Detection Sources:**
- WAF alerts (blocked attacks)
- Rate limiting triggers (HTTP 429)
- Authentication failures (account lockout)
- Log anomalies (unusual patterns)
- User reports

**Tools:**
```bash
# Check recent WAF blocks
docker logs milkyhoop-dev-api_gateway-1 --since 1h | grep -i "blocked"

# Check rate limit triggers
docker logs milkyhoop-dev-api_gateway-1 --since 1h | grep -i "429"

# Check auth failures
docker logs milkyhoop-dev-auth_service-1 --since 1h | grep -i "failed"
```

### Phase 2: Containment (CONTAIN)

**Objective:** Limit incident impact and prevent spread

**Immediate Actions (P1/P2):**
1. Isolate affected systems
2. Block malicious IPs/actors
3. Disable compromised accounts
4. Preserve evidence

**Containment Commands:**
```bash
# Block IP at firewall
ufw deny from <malicious_ip>

# Disable user account
# Via API or direct DB update

# Isolate container
docker stop <container_name>

# Rotate compromised credentials
# Use SOPS to update secrets
```

**Evidence Preservation:**
```bash
# Snapshot current state
docker logs milkyhoop-dev-api_gateway-1 > /tmp/incident_$(date +%Y%m%d_%H%M%S)_api.log
docker logs milkyhoop-dev-auth_service-1 > /tmp/incident_$(date +%Y%m%d_%H%M%S)_auth.log

# Database snapshot
docker exec milkyhoop-dev-postgres-1 pg_dump -U postgres milkydb > /tmp/incident_db_snapshot.sql
```

### Phase 3: Eradication (ERADICATE)

**Objective:** Remove threat and fix vulnerabilities

**Actions:**
1. Identify root cause
2. Remove malware/backdoors
3. Patch vulnerabilities
4. Update security controls
5. Rotate all potentially compromised credentials

**Root Cause Analysis:**
```bash
# Timeline analysis
grep -r "suspicious_pattern" /var/log/milkyhoop/*.log | sort -t: -k1

# Check for unauthorized changes
git log --oneline -20
docker images --format "{{.Repository}}:{{.Tag}} {{.CreatedAt}}"
```

### Phase 4: Recovery (RECOVER)

**Objective:** Restore normal operations

**Actions:**
1. Restore systems from clean backups
2. Verify system integrity
3. Monitor for re-infection
4. Gradual service restoration

**Recovery Procedures:**
```bash
# Restore from Restic backup
source /root/.config/restic/credentials.env
restic snapshots  # List available
./backups/restic_restore.sh <snapshot_id>

# Verify services
docker compose -f docker-compose.yml up -d
curl -s https://milkyhoop.com/healthz

# Enhanced monitoring period (48h)
```

### Phase 5: Lessons Learned (LEARN)

**Objective:** Improve security posture

**Actions (within 7 days of incident):**
1. Conduct post-incident review
2. Document timeline and actions
3. Identify improvements
4. Update procedures
5. Implement preventive measures

**Post-Incident Report Template:**
```markdown
## Incident Report: [INCIDENT_ID]

### Summary
- Date/Time:
- Duration:
- Severity:
- Category:

### Timeline
- [Time] Detection
- [Time] Containment
- [Time] Eradication
- [Time] Recovery

### Impact
- Systems affected:
- Data affected:
- Tenants affected:
- Financial impact:

### Root Cause

### Corrective Actions

### Preventive Measures

### Lessons Learned
```

## 6. Communication Plan

### 6.1 Internal Communication

| Audience | When | Method | Content |
|----------|------|--------|---------|
| IRT | Immediately | Secure channel | Full details |
| Management | Within 1h (P1/P2) | Direct call | Summary + impact |
| All staff | After containment | Email | Need-to-know basis |

### 6.2 External Communication (if required)

| Audience | When | Method | Approval |
|----------|------|--------|----------|
| Affected tenants | Within 72h | Email | Management |
| Regulators (KOMINFO) | As required by law | Official letter | Legal |
| Media | Only if necessary | Press release | CEO |

### 6.3 Communication Templates

**Tenant Notification (Data Breach):**
```
Subject: Security Incident Notification - MilkyHoop

Dear [Tenant Name],

We are writing to inform you of a security incident that may have affected your data on the MilkyHoop platform.

What happened: [Brief description]
When: [Date/time]
What data was involved: [Types of data]
What we are doing: [Actions taken]
What you should do: [Recommendations]

We sincerely apologize for this incident. If you have questions, contact us at security@milkyhoop.com.

Regards,
MilkyHoop Security Team
```

## 7. Regulatory Requirements

### 7.1 Indonesian Data Protection (UU PDP)

- Report serious breaches to KOMINFO within 72 hours
- Notify affected individuals "without undue delay"
- Document all incidents and responses

### 7.2 Record Retention

- Incident records: 5 years minimum
- Evidence: Until legal proceedings complete
- Audit logs: 1 year minimum

## 8. Testing and Training

| Activity | Frequency | Owner |
|----------|-----------|-------|
| Tabletop exercise | Quarterly | Security Lead |
| Technical drill | Semi-annually | Technical Lead |
| Plan review | Annually | IRT |
| Team training | Upon joining + annually | HR + Security |

## 9. Appendices

### Appendix A: Quick Reference Card

```
┌─────────────────────────────────────────────────────────┐
│           INCIDENT RESPONSE QUICK REFERENCE             │
├─────────────────────────────────────────────────────────┤
│ 1. DETECT    - Confirm incident, classify severity      │
│ 2. CONTAIN   - Isolate, block, preserve evidence        │
│ 3. ERADICATE - Remove threat, patch, rotate creds       │
│ 4. RECOVER   - Restore from backup, verify, monitor     │
│ 5. LEARN     - Post-incident review within 7 days       │
├─────────────────────────────────────────────────────────┤
│ ESCALATION:                                             │
│ P1 Critical → 15 min → Full IRT + Management            │
│ P2 High     → 1 hour → Full IRT                         │
│ P3 Medium   → 4 hours → Security + Tech Lead            │
│ P4 Low      → 24 hours → Security Lead                  │
└─────────────────────────────────────────────────────────┘
```

### Appendix B: Emergency Contacts

| Role | Primary | Backup |
|------|---------|--------|
| Incident Commander | [Name/Phone] | [Name/Phone] |
| Security Lead | [Name/Phone] | [Name/Phone] |
| Technical Lead | [Name/Phone] | [Name/Phone] |
| Legal Advisor | [Name/Phone] | [Name/Phone] |
| DigitalOcean Support | [Support URL] | - |

---

## Approval

| Role | Name | Signature | Date |
|------|------|-----------|------|
| CEO | _________________ | _________________ | ____/____/____ |
| Security Lead | _________________ | _________________ | ____/____/____ |

---

*Document Control: Review after each significant incident or annually, whichever is sooner.*
