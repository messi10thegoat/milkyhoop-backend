# Dependency Synchronization Plan

## Issues Identified
1. **Container vs Host Mismatch**: Containers have packages not documented in host requirements
2. **Manual Installations**: Some packages installed directly in containers
3. **Version Drift**: Different versions of same packages across services
4. **Missing Documentation**: Critical packages not in requirements.txt/pyproject.toml

## Recommended Actions

### Immediate (Before Clean Build)
1. **Update Requirements Files**: Use corrected_requirements.txt generated from containers
2. **Version Alignment**: Ensure same package versions across services
3. **Document Critical Dependencies**: Add all OpenAI, Prisma, gRPC dependencies

### For Clean Build
1. **Use Corrected Requirements**: Copy corrected requirements to host
2. **Multi-stage Dockerfile**: Separate build and runtime dependencies
3. **Lock File Strategy**: Use poetry.lock or pip freeze for exact versions
4. **Validation Testing**: Test all critical functionality after clean build

### Long-term
1. **Dependency Management Policy**: No manual pip install in containers
2. **CI/CD Integration**: Automated dependency drift detection
3. **Development Workflow**: Volume mounts to prevent container-only changes
4. **Regular Audits**: Monthly dependency state validation

## Files Generated
- `corrected_requirements/`: Accurate requirements.txt from containers
- `discrepancy_analysis.txt`: Detailed comparison of declared vs actual
- `undocumented_packages.txt`: Packages missing from documentation
- `version_audit.txt`: Version consistency analysis across services
