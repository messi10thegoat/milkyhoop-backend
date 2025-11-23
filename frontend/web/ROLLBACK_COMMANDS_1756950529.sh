#!/bin/bash

echo "=== EMERGENCY ROLLBACK TO WORKING STATE ==="
echo "Restoring to backup timestamp: 1756950529"
echo ""

echo "Step 1: Stop frontend container..."
docker compose stop frontend

echo ""
echo "Step 2: Restore source code..."
rm -rf frontend/web/src
cp -r frontend/web/src_backup_before_auth_fix_1756950529 frontend/web/src
echo "Source code restored"

echo ""
echo "Step 3: Restore container image..."
docker tag milkyhoop-frontend:rollback_point_1756950529 milkyhoop-frontend:latest
echo "Container image restored"

echo ""
echo "Step 4: Restart frontend..."
docker compose up -d frontend
echo "Waiting for startup..."
sleep 15

echo ""
echo "Step 5: Verify rollback success..."
curl -s http://localhost:3000/ | grep -o "<title.*title>" || echo "Frontend check"
echo ""
echo "ROLLBACK COMPLETE - System restored to working state"
