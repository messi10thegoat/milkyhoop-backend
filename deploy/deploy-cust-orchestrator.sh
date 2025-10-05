#!/bin/bash

# Cust_Orchestrator Deployment Script
# Usage: ./deploy-cust-orchestrator.sh [development|production]

set -e

ENVIRONMENT=${1:-development}
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/environments/$ENVIRONMENT.env"

if [ ! -f "$ENV_FILE" ]; then
    echo "Error: Environment file not found: $ENV_FILE"
    exit 1
fi

# Load environment variables
set -a
source "$ENV_FILE"
set +a

echo "=================================="
echo "Deploying Cust_Orchestrator to: $ENVIRONMENT"
echo "Environment file: $ENV_FILE"
echo "=================================="

# Stop existing container
echo "Stopping existing container..."
docker stop milkyhoop${ENVIRONMENT:+-$ENVIRONMENT}-cust_orchestrator-1 2>/dev/null || true
docker rm milkyhoop${ENVIRONMENT:+-$ENVIRONMENT}-cust_orchestrator-1 2>/dev/null || true

# Build image
echo "Building image..."
if [ "$ENVIRONMENT" = "development" ]; then
    IMAGE_NAME="milkyhoop-dev-cust_orchestrator"
    CONTAINER_NAME="milkyhoop-dev-cust_orchestrator-1"
else
    IMAGE_NAME="milkyhoop-cust_orchestrator"
    CONTAINER_NAME="milkyhoop-cust_orchestrator-1"
fi

docker build -t "$IMAGE_NAME" -f backend/services/cust_orchestrator/Dockerfile .

# Deploy container
echo "Deploying container..."
docker run -d \
  --name "$CONTAINER_NAME" \
  --restart always \
  --network "$DOCKER_NETWORK" \
  -p "${EXTERNAL_GRPC_PORT}:${INTERNAL_GRPC_PORT}" \
  -p "${EXTERNAL_METRICS_PORT}:${INTERNAL_METRICS_PORT}" \
  -e RAGLLM_SERVICE_HOST="$RAGLLM_SERVICE_HOST" \
  -e RAGLLM_SERVICE_PORT="$RAGLLM_SERVICE_PORT" \
  -e RAGCRUD_SERVICE_HOST="$RAGCRUD_SERVICE_HOST" \
  -e RAGCRUD_SERVICE_PORT="$RAGCRUD_SERVICE_PORT" \
  -e TENANT_PARSER_HOST="$TENANT_PARSER_HOST" \
  -e TENANT_PARSER_PORT="$TENANT_PARSER_PORT" \
  -e REDIS_HOST="$REDIS_HOST" \
  -e REDIS_PORT="$REDIS_PORT" \
  -e ENVIRONMENT="$ENVIRONMENT" \
  "$IMAGE_NAME"

echo "Waiting for container startup..."
sleep 30

echo "Container status:"
docker ps --filter "name=$CONTAINER_NAME" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"

echo "Health check:"
docker exec "$CONTAINER_NAME" grpc_health_probe -addr=localhost:${INTERNAL_GRPC_PORT} || echo "Health check pending..."

echo "=================================="
echo "Deployment completed for: $ENVIRONMENT"
echo "=================================="
