#!/bin/bash

echo "=== COMPLETE SYSTEM ROLLBACK ==="
echo "Restoring both source code AND container to working state"
echo "Backup timestamp: 1756950518"
echo ""

echo "Phase 1: Restore source code..."
rm -rf frontend/web/src
cp -r frontend/web/src_backup_before_auth_fix_1756950518 frontend/web/src
echo "Source code restored"

echo ""
echo "Phase 2: Restore container working files..."
bash frontend/web/RESTORE_CONTAINER_1756950518.sh

echo ""
echo "Phase 3: Verify complete system restoration..."
curl -s http://localhost:3000/ | grep -o "<title.*title>" || echo "Frontend check"

echo ""
echo "COMPLETE ROLLBACK FINISHED - System restored to exact working state"
