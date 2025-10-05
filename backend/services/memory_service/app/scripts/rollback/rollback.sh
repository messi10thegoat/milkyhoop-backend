#!/bin/bash

# One Command Rollback System
SNAPSHOT_ID=${1}

if [ -z "$SNAPSHOT_ID" ]; then
    echo "📋 Available snapshots:"
    ls snapshots/*.json 2>/dev/null | sed 's/snapshots\///g' | sed 's/.json//g' | sort -r
    echo ""
    echo "Usage: ./rollback.sh <snapshot_id>"
    echo "Example: ./rollback.sh working_20250904_1400_feature_fix"
    exit 1
fi

if [ ! -f "snapshots/$SNAPSHOT_ID.json" ]; then
    echo "❌ Snapshot not found: $SNAPSHOT_ID"
    exit 1
fi

echo "⏪ Rolling back to: $SNAPSHOT_ID"
start_time=$(date +%s)

# Read snapshot metadata
CONTAINERS=$(cat snapshots/$SNAPSHOT_ID.json | grep -o '"containers":\[[^]]*\]' | sed 's/"containers":\[//g' | sed 's/\]//g' | tr -d '"')
VOLUMES=$(cat snapshots/$SNAPSHOT_ID.json | grep -o '"volumes":\[[^]]*\]' | sed 's/"volumes":\[//g' | sed 's/\]//g' | tr -d '"')

# 1. Stop current containers
echo "--- Stopping current containers ---"
docker compose down

# 2. Restore containers from snapshots
echo "--- Restoring containers ---"
IFS=',' read -ra CONTAINER_ARRAY <<< "$CONTAINERS"
for container in "${CONTAINER_ARRAY[@]}"; do
    if [ ! -z "$container" ]; then
        echo "Restoring: $container"
        # Tag snapshot as latest for the service
        docker tag ${container}:$SNAPSHOT_ID ${container}:latest
    fi
done

# 3. Restore host configs
echo "--- Restoring host configurations ---"
if [ -d "snapshots/configs/$SNAPSHOT_ID" ]; then
    cp -r snapshots/configs/$SNAPSHOT_ID/* . 2>/dev/null || true
fi

# 4. Restore volumes
echo "--- Restoring volumes ---"
if [ -d "snapshots/volumes/$SNAPSHOT_ID" ]; then
    IFS=',' read -ra VOLUME_ARRAY <<< "$VOLUMES"
    for volume in "${VOLUME_ARRAY[@]}"; do
        if [ ! -z "$volume" ] && [ -f "snapshots/volumes/$SNAPSHOT_ID/${volume}.tar.gz" ]; then
            echo "Restoring volume: $volume"
            docker volume rm $volume 2>/dev/null || true
            docker volume create $volume
            docker run --rm -v $volume:/target -v $(pwd)/snapshots/volumes/$SNAPSHOT_ID:/backup alpine sh -c "cd /target && tar xzf /backup/${volume}.tar.gz"
        fi
    done
fi

# 5. Start services
echo "--- Starting services ---"
docker compose up -d

# 6. Wait for health check
echo "--- Waiting for services to be ready ---"
sleep 10

# 7. Verify rollback
echo "--- Verifying rollback ---"
docker ps --filter name=milkyhoop --format "table {{.Names}}\t{{.Status}}"

end_time=$(date +%s)
duration=$((end_time - start_time))

echo "✅ Rollback completed in ${duration} seconds"
echo "🎯 System restored to snapshot: $SNAPSHOT_ID"
