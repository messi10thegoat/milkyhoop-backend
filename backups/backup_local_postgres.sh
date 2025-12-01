#!/bin/bash
# ==================================================
# MilkyHoop Local PostgreSQL Backup Script
# ==================================================
# Usage: ./backup_local_postgres.sh
# Cron:  0 2 * * * /root/milkyhoop-dev/backups/backup_local_postgres.sh
# ==================================================

set -e

# Configuration
BACKUP_DIR="/root/milkyhoop-dev/backups"
DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="$BACKUP_DIR/milkydb_backup_$DATE.sql.gz"
CONTAINER="milkyhoop-dev-postgres-1"
DB_NAME="milkydb"
DB_USER="postgres"

# Retention (keep last 7 backups)
MAX_BACKUPS=7

echo "=== MilkyHoop Backup Started: $(date) ==="

# Create backup using docker exec + pg_dump
echo "📦 Creating backup..."
docker exec $CONTAINER pg_dump -U $DB_USER -d $DB_NAME --no-owner --no-acl | gzip > "$BACKUP_FILE"

# Verify backup
if [ -f "$BACKUP_FILE" ]; then
    SIZE=$(ls -lh "$BACKUP_FILE" | awk '{print $5}')
    echo "✅ Backup created: $BACKUP_FILE ($SIZE)"
else
    echo "❌ Backup failed!"
    exit 1
fi

# Cleanup old backups (keep last 7)
echo "🧹 Cleaning old backups..."
cd $BACKUP_DIR
ls -t milkydb_backup_*.sql.gz 2>/dev/null | tail -n +$((MAX_BACKUPS + 1)) | xargs -r rm -f
REMAINING=$(ls milkydb_backup_*.sql.gz 2>/dev/null | wc -l)
echo "📊 Backups remaining: $REMAINING"

echo "=== Backup Completed: $(date) ==="
