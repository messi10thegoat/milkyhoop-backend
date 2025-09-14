#!/bin/bash
# Pre-Build Validation Script

echo "=== PRE-BUILD VALIDATION ==="

# 1. Check running services
echo "1. Service health matrix:"
docker ps --filter "name=milkyhoop-dev" --format "table {{.Names}}\t{{.Status}}" | grep healthy

# 2. Protobuf version consistency
echo "2. Protobuf version check:"
for service in api_gateway ragcrud_service chatbot_service; do
    if docker ps --filter "name=milkyhoop-dev-${service}-1" --format "{{.Names}}" | grep -q "${service}"; then
        echo "  ${service}: $(docker exec milkyhoop-dev-${service}-1 pip show protobuf 2>/dev/null | grep Version || echo 'Not accessible')"
    fi
done

# 3. Cross-service connectivity
echo "3. gRPC connectivity test:"
docker exec milkyhoop-dev-api_gateway-1 ping -c 1 milkyhoop-dev-ragcrud_service-1 >/dev/null 2>&1 && echo "  ✅ ragcrud reachable" || echo "  ❌ ragcrud unreachable"

# 4. API endpoint verification
echo "4. API endpoint verification:"
curl -s -o /dev/null -w "  api_gateway health: %{http_code}\n" http://localhost:8001/healthz

echo "=== VALIDATION COMPLETE ==="
