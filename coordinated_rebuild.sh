#!/bin/bash
# Coordinated Rebuild Strategy

SERVICE_TO_REBUILD=${1:-"api_gateway"}
echo "=== COORDINATED REBUILD: ${SERVICE_TO_REBUILD} ==="

# 1. Pre-build validation
echo "Running pre-build validation..."
./validate_pre_build.sh

# 2. Identify dependent services
case ${SERVICE_TO_REBUILD} in
    "api_gateway")
        DEPENDENT_SERVICES="ragcrud_service ragllm_service tenant_parser auth_service"
        ;;
    "ragcrud_service")
        DEPENDENT_SERVICES="api_gateway"
        ;;
    *)
        DEPENDENT_SERVICES=""
        ;;
esac

echo "Services that may need restart: ${DEPENDENT_SERVICES}"

# 3. Stop target service
echo "Stopping ${SERVICE_TO_REBUILD}..."
docker compose stop ${SERVICE_TO_REBUILD}

# 4. Clean build
echo "Clean building ${SERVICE_TO_REBUILD}..."
docker compose build --no-cache ${SERVICE_TO_REBUILD}

# 5. Start with validation
echo "Starting ${SERVICE_TO_REBUILD}..."
docker compose up -d ${SERVICE_TO_REBUILD}

# 6. Wait and test
echo "Waiting for startup..."
sleep 15

# 7. Connectivity test
echo "Testing connectivity..."
if [[ "${SERVICE_TO_REBUILD}" == "api_gateway" ]]; then
    curl -s http://localhost:8001/healthz
    curl -s -X POST http://localhost:8001/tenant/bca/chat \
      -H "Content-Type: application/json" \
      -d '{"message": "test rebuild", "session_id": "validation"}' | head -c 100
fi

echo "=== REBUILD COMPLETE ==="
