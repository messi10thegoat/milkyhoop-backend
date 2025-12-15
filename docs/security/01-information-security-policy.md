# Information Security Policy
## MilkyHoop Platform

| Document ID | ISMS-POL-001 |
|-------------|--------------|
| Version | 1.0 |
| Effective Date | 2025-12-15 |
| Review Date | 2026-12-15 |
| Classification | Internal |
| ISO Control | A.5.1 Policies for Information Security |

---

## 1. Purpose

This Information Security Policy establishes the framework for protecting MilkyHoop's information assets, ensuring confidentiality, integrity, and availability of data for our multi-tenant SaaS platform serving Indonesian UMKM businesses.

## 2. Scope

This policy applies to:
- All MilkyHoop employees, contractors, and third-party service providers
- All information systems, networks, and data
- All locations where MilkyHoop data is processed or stored
- All tenant data processed on behalf of customers

## 3. Policy Statement

MilkyHoop is committed to:
- Protecting customer (tenant) data as our highest priority
- Maintaining ISO 27001:2022 compliance
- Implementing defense-in-depth security architecture
- Continuous improvement of security controls

## 4. Information Security Objectives

| Objective | Target | Metric |
|-----------|--------|--------|
| Availability | 99.9% uptime | Monthly SLA report |
| Incident Response | < 4 hours detection | Mean Time to Detect (MTTD) |
| Vulnerability Remediation | Critical: 24h, High: 7d | Patch compliance rate |
| Security Training | 100% staff trained | Annual completion rate |
| Backup Recovery | RTO: 4h, RPO: 24h | Quarterly DR test |

## 5. Roles and Responsibilities

### 5.1 Management
- Approve security policies and budgets
- Ensure adequate resources for security
- Review security incidents quarterly

### 5.2 Security Team
- Implement and maintain security controls
- Monitor for security threats
- Conduct security assessments
- Respond to security incidents

### 5.3 Development Team
- Follow secure coding practices (OWASP guidelines)
- Perform code reviews with security focus
- Address security vulnerabilities promptly

### 5.4 All Personnel
- Complete security awareness training
- Report security incidents immediately
- Protect credentials and access tokens
- Follow acceptable use policies

## 6. Security Controls Framework

### 6.1 Access Control (A.5.15-18)
- Role-Based Access Control (RBAC) implemented
- Multi-factor authentication for administrative access
- Principle of least privilege enforced
- Access reviews conducted quarterly

### 6.2 Cryptography (A.8.24)
- TLS 1.2+ for all data in transit
- AES-256 encryption for data at rest
- Field-level encryption for PII (FLE)
- SOPS + age for secret management

### 6.3 Operations Security (A.8.1-31)
- Change management process required
- Capacity monitoring implemented
- Malware protection active (WAF)
- Logging and monitoring enabled

### 6.4 Secure Development (A.8.25-34)
- Security requirements in design phase
- Static analysis (Semgrep) in CI/CD
- Dependency scanning (Trivy)
- Secret detection (Gitleaks)

## 7. Multi-Tenant Data Isolation

As a multi-tenant SaaS platform:
- Each tenant's data is logically separated by tenant_id
- Cross-tenant access is technically prevented
- Tenant data is encrypted with tenant-specific context
- Regular tenant isolation testing performed

## 8. Compliance Requirements

| Regulation | Applicability | Status |
|------------|---------------|--------|
| ISO 27001:2022 | Full | In Progress |
| Indonesian Data Protection Law (UU PDP) | Full | Compliant |
| PCI-DSS | Payment data | N/A (using Midtrans) |

## 9. Policy Violations

Violations of this policy may result in:
- Immediate access revocation
- Disciplinary action
- Termination of employment/contract
- Legal action where applicable

## 10. Policy Review

This policy shall be reviewed:
- Annually at minimum
- After significant security incidents
- When major changes occur to systems or regulations
- Upon request by management

## 11. Related Documents

| Document | Reference |
|----------|-----------|
| Incident Response Plan | ISMS-PRC-001 |
| Business Continuity Plan | ISMS-PRC-002 |
| Risk Assessment Report | ISMS-DOC-001 |
| Acceptable Use Policy | ISMS-POL-002 |

---

## Approval

| Role | Name | Signature | Date |
|------|------|-----------|------|
| CEO | _________________ | _________________ | ____/____/____ |
| CTO | _________________ | _________________ | ____/____/____ |
| Security Lead | _________________ | _________________ | ____/____/____ |

---

*Document Control: This document is controlled. Unauthorized copies are not valid.*
