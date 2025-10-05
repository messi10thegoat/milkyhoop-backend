# Pre-Build Assessment Template

## Module: [SERVICE_NAME]
Date: [DATE]
Assessor: [NAME]

### 1. Dependency Analysis
- [ ] Database dependencies identified
- [ ] External service dependencies mapped
- [ ] Environment variables documented
- [ ] Required ports available

### 2. Configuration Readiness
- [ ] pyproject.toml present and valid
- [ ] requirements.txt aligned with pyproject.toml
- [ ] Dockerfile present and buildable
- [ ] Environment variables defined

### 3. Build Prerequisites
- [ ] Source code synchronized (host = container)
- [ ] Generated files (protobuf, prisma) available
- [ ] Build dependencies installed on host
- [ ] Docker build context clean

### 4. Testing Strategy
- [ ] Unit tests available
- [ ] Integration test plan defined
- [ ] Health check endpoint working
- [ ] Rollback procedure documented

### 5. Risk Assessment
**Risk Level:** [LOW/MEDIUM/HIGH]
**Critical Dependencies:** [LIST]
**Potential Issues:** [DESCRIBE]
**Mitigation Plan:** [DESCRIBE]

### 6. Success Criteria
- [ ] Service starts without errors
- [ ] Health check returns 200
- [ ] Core functionality verified
- [ ] No regression in dependent services

### 7. Rollback Plan
**Trigger Conditions:** [WHEN TO ROLLBACK]
**Rollback Commands:** [SPECIFIC COMMANDS]
**Recovery Time:** [ESTIMATED DURATION]

### 8. Assessment Decision
- [ ] READY for clean build
- [ ] NEEDS PREPARATION - specific actions required
- [ ] HIGH RISK - postpone until issues resolved

### Notes:
[ADDITIONAL OBSERVATIONS]
