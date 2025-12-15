#!/bin/bash
# ==================================================
# MilkyHoop Restic Encrypted Backup Script
# Local encrypted backup with Restic (AES-256)
# ISO 27001:2022 Compliant (A.8.13)
# ==================================================
# Usage: ./restic_offsite.sh
# Cron:  30 2 * * * /root/milkyhoop-dev/backups/restic_offsite.sh >> /var/log/milkyhoop/restic.log 2>&1
# ==================================================

set -euo pipefail

# Load credentials from secure file
CREDS_FILE="/root/.config/restic/credentials.env"
if [ ! -f "$CREDS_FILE" ]; then
    echo "ERROR: Credentials file not found: $CREDS_FILE"
    exit 1
fi

# shellcheck source=/dev/null
source "$CREDS_FILE"

# Configuration
BACKUP_DIR="/root/milkyhoop-dev/backups"
LOG_DIR="/var/log/milkyhoop"
TEMP_DIR="/tmp/restic_backup_$$"
DATE=$(date +%Y%m%d_%H%M%S)
CONTAINER="milkyhoop-dev-postgres-1"
DB_NAME="milkydb"
DB_USER="postgres"

# Ensure directories exist
mkdir -p "$LOG_DIR" "$TEMP_DIR"
trap "rm -rf $TEMP_DIR" EXIT

echo "=========================================="
echo "MilkyHoop Restic Offsite Backup"
echo "Started: $(date)"
echo "=========================================="

# Step 1: Create database dump
echo "[1/5] Creating PostgreSQL dump..."
DB_DUMP="$TEMP_DIR/milkydb_$DATE.sql"
docker exec "$CONTAINER" pg_dump -U "$DB_USER" -d "$DB_NAME" --no-owner --no-acl > "$DB_DUMP"

if [ ! -s "$DB_DUMP" ]; then
    echo "ERROR: Database dump failed or empty!"
    exit 1
fi
DB_SIZE=$(du -h "$DB_DUMP" | cut -f1)
echo "    Database dump: $DB_SIZE"

# Step 2: Collect critical configs
echo "[2/5] Collecting critical configs..."
CONFIG_DIR="$TEMP_DIR/configs"
mkdir -p "$CONFIG_DIR"

# Copy critical configuration files
cp /root/milkyhoop-dev/.env "$CONFIG_DIR/root.env" 2>/dev/null || true
cp /root/milkyhoop-dev/docker-compose.yml "$CONFIG_DIR/" 2>/dev/null || true
cp /etc/nginx/sites-available/milkyhoop.conf "$CONFIG_DIR/" 2>/dev/null || true

# Export current crontab
crontab -l > "$CONFIG_DIR/crontab.txt" 2>/dev/null || true

# Count configs
CONFIG_COUNT=$(find "$CONFIG_DIR" -type f | wc -l)
echo "    Collected $CONFIG_COUNT config files"

# Step 3: Check/Initialize repository
echo "[3/5] Checking Restic repository..."
if ! restic snapshots --json > /dev/null 2>&1; then
    echo "    Initializing new repository..."
    restic init
    echo "    Repository initialized"
else
    SNAPSHOT_COUNT=$(restic snapshots --json | jq length)
    echo "    Repository OK ($SNAPSHOT_COUNT existing snapshots)"
fi

# Step 4: Create backup
echo "[4/5] Creating encrypted backup..."
restic backup \
    --tag "milkyhoop" \
    --tag "database" \
    --tag "automated" \
    --host "$(hostname)" \
    "$TEMP_DIR"

LATEST_SNAPSHOT=$(restic snapshots --json --latest 1 | jq -r '.[0].short_id')
echo "    Backup created: $LATEST_SNAPSHOT"

# Step 5: Apply retention policy (7 daily, 4 weekly, 12 monthly, 2 yearly)
echo "[5/5] Applying retention policy..."
restic forget \
    --keep-daily 7 \
    --keep-weekly 4 \
    --keep-monthly 12 \
    --keep-yearly 2 \
    --prune

# Final stats
echo ""
echo "=========================================="
echo "Backup Complete!"
echo "=========================================="
restic stats --mode raw-data
echo ""
echo "Latest snapshots:"
restic snapshots --latest 5

echo ""
echo "Completed: $(date)"
echo "=========================================="
