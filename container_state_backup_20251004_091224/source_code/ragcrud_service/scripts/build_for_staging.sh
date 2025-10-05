#!/bin/bash

echo "============================================="
echo "=== STAGING BUILD SCRIPT ==="
echo "============================================="

# Get Git information
GIT_COMMIT=$(git rev-parse HEAD 2>/dev/null || echo "no-git")
GIT_SHORT=$(git rev-parse --short HEAD 2>/dev/null || echo "no-git")
BUILD_TIME=$(date -u +%Y-%m-%dT%H:%M:%SZ)
BRANCH=$(git branch --show-current 2>/dev/null || echo "no-branch")

echo "📝 Build Information:"
echo "   Git Commit: $GIT_COMMIT"
echo "   Short Hash: $GIT_SHORT"
echo "   Branch: $BRANCH"
echo "   Build Time: $BUILD_TIME"
echo ""

# Clean environment
echo "🧹 Cleaning development environment..."
docker-compose down
docker builder prune -f

# Build with metadata
echo "🔨 Building with metadata..."
docker-compose build \
    --build-arg GIT_COMMIT="$GIT_COMMIT" \
    --build-arg BUILD_TIME="$BUILD_TIME"

# Tag for staging
echo "🏷️  Tagging for staging deployment..."
services=("tenant_parser" "ragcrud_service" "auth_service")

for service in "${services[@]}"; do
    if docker images | grep -q "milkyhoop-dev-${service}"; then
        docker tag "milkyhoop-dev-${service}:latest" "milkyhoop-staging-${service}:${GIT_SHORT}"
        docker tag "milkyhoop-dev-${service}:latest" "milkyhoop-staging-${service}:latest"
        echo "✅ Tagged ${service} for staging"
    fi
done

echo ""
echo "🎯 Build complete! Ready for staging deployment."
echo "   Staging tags: milkyhoop-staging-*:${GIT_SHORT}"
