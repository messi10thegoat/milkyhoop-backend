# Dependency Fix Testing Plan

## Files Modified
1. `backend/services/cust_orchestrator/requirements.txt` - Added redis, prometheus_client
2. `backend/services/ragcrud_service/requirements.txt` - Added redis
3. `backend/services/ragllm_service/requirements.txt` - Updated OpenAI, gRPC versions

## Testing Steps

### 1. Functional Testing
```bash
# Test FAQ context functionality (critical)
curl -X POST http://localhost:8001/bca/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "saya mahasiswa, butuh tabungan", "session_id": "dep_test"}'

# Expected: Proper BCA product recommendations
# Expected: RAG LLM logs show FAQ context usage
```

### 2. Service Health Testing
```bash
# Check all services start without dependency errors
docker compose logs cust_orchestrator | grep -i error
docker compose logs ragllm_service | grep -i error  
docker compose logs ragcrud_service | grep -i error
```

### 3. Clean Build Testing
```bash
# Test with updated requirements
docker compose build cust_orchestrator
docker compose build ragllm_service
docker compose build ragcrud_service

# Check for missing dependency errors during build
```

## Rollback Plan
If issues occur:
```bash
# Restore from backup
cp dependency_fixes_backup_*/cust_orchestrator_requirements.txt.backup backend/services/cust_orchestrator/requirements.txt
cp dependency_fixes_backup_*/ragllm_service_requirements.txt.backup backend/services/ragllm_service/requirements.txt
cp dependency_fixes_backup_*/ragcrud_service_requirements.txt.backup backend/services/ragcrud_service/requirements.txt

# Restart services
docker compose restart cust_orchestrator ragllm_service ragcrud_service
```
