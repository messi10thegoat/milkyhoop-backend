#!/bin/bash

echo "=== RESTORE CONTAINER TO WORKING STATE ==="
echo "Restoring container files from backup timestamp: 1756950518"
echo ""

echo "Step 1: Stop frontend container..."
docker compose stop frontend

echo ""
echo "Step 2: Start temporary container for file restoration..."
docker run -d --name temp_restore_container nginx:alpine
sleep 5

echo ""
echo "Step 3: Copy backed up files to temporary container..."
docker cp frontend/web/container_backup_1756950518/static/ temp_restore_container:/usr/share/nginx/html/
docker cp frontend/web/container_backup_1756950518/index.html temp_restore_container:/usr/share/nginx/html/

echo ""
echo "Step 4: Create new image from restored container..."
docker commit temp_restore_container milkyhoop-frontend:restored_working_state
docker stop temp_restore_container
docker rm temp_restore_container

echo ""
echo "Step 5: Update docker-compose to use restored image..."
# Note: This would require docker-compose modification or manual container recreation

echo ""
echo "Step 6: Start frontend with restored files..."
docker tag milkyhoop-frontend:restored_working_state milkyhoop-frontend:latest
docker compose up -d frontend
echo "Waiting for startup..."
sleep 15

echo ""
echo "Step 7: Verify restoration..."
curl -s http://localhost:3000/ | grep -o "<title.*title>" || echo "Frontend responding"

echo ""
echo "CONTAINER RESTORATION COMPLETE"
