#!/bin/bash

# Enhanced Snapshot Script with Volume & Pruning
SERVICE=${1:-"milkyhoop"}
DESCRIPTION=${2:-"manual_snapshot"}
TIMESTAMP=$(date +%Y%m%d_%H%M)
SNAPSHOT_NAME="working_${TIMESTAMP}_${DESCRIPTION}"

echo "📸 Creating snapshot: $SNAPSHOT_NAME"

# 1. Docker commit all containers
echo "--- Committing containers ---"
for container in $(docker ps --filter name=$SERVICE --format "{{.Names}}"); do
    echo "Snapshotting: $container"
    docker commit $container ${container}:$SNAPSHOT_NAME
done

# 2. Backup host configs
echo "--- Backing up host configs ---"
BACKUP_DIR="snapshots/configs/$SNAPSHOT_NAME"
mkdir -p $BACKUP_DIR
cp -r backend/ $BACKUP_DIR/ 2>/dev/null || true
cp -r infra/ $BACKUP_DIR/ 2>/dev/null || true
cp -r database/ $BACKUP_DIR/ 2>/dev/null || true
cp docker-compose*.yml $BACKUP_DIR/ 2>/dev/null || true
cp .env* $BACKUP_DIR/ 2>/dev/null || true

# 3. Volume snapshots (for persistent data)
echo "--- Creating volume snapshots ---"
VOLUME_BACKUP_DIR="snapshots/volumes/$SNAPSHOT_NAME"
mkdir -p $VOLUME_BACKUP_DIR
for volume in $(docker volume ls --filter name=$SERVICE --format "{{.Name}}"); do
    echo "Backing up volume: $volume"
    docker run --rm -v $volume:/source -v $(pwd)/$VOLUME_BACKUP_DIR:/backup alpine tar czf /backup/${volume}.tar.gz -C /source .
done

# 4. Create restore metadata
cat > snapshots/$SNAPSHOT_NAME.json << METADATA_EOF
{
  "timestamp": "$TIMESTAMP",
  "description": "$DESCRIPTION",
  "containers": [$(docker ps --filter name=$SERVICE --format '"{{.Names}}"' | paste -sd',')],
  "volumes": [$(docker volume ls --filter name=$SERVICE --format '"{{.Name}}"' | paste -sd',')],
  "config_path": "snapshots/configs/$SNAPSHOT_NAME",
  "volume_path": "snapshots/volumes/$SNAPSHOT_NAME"
}
METADATA_EOF

echo "✅ Snapshot created: $SNAPSHOT_NAME"

# 5. Auto-pruning (keep last 5 snapshots)
echo "--- Auto-pruning old snapshots ---"
SNAPSHOTS_TO_KEEP=5
cd snapshots
ls -t *.json | tail -n +$((SNAPSHOTS_TO_KEEP + 1)) | while read old_snapshot; do
    SNAPSHOT_ID=$(basename "$old_snapshot" .json)
    echo "Pruning old snapshot: $SNAPSHOT_ID"
    
    # Remove docker images
    docker images --filter reference="*:$SNAPSHOT_ID" --format "{{.Repository}}:{{.Tag}}" | xargs -r docker rmi -f
    
    # Remove config backups
    rm -rf configs/$SNAPSHOT_ID
    
    # Remove volume backups
    rm -rf volumes/$SNAPSHOT_ID
    
    # Remove metadata
    rm -f $old_snapshot
done
cd ..

echo "📋 Current snapshots:"
ls -la snapshots/*.json 2>/dev/null | tail -5 || echo "No snapshots yet"
