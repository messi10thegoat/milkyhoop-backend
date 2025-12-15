#!/bin/bash
# ==================================================
# MilkyHoop Restic Restore Script
# Disaster Recovery from DigitalOcean Spaces
# ISO 27001:2022 Compliant (A.8.13)
# ==================================================
# Usage:
#   ./restic_restore.sh                    # Restore latest
#   ./restic_restore.sh <snapshot_id>      # Restore specific
#   ./restic_restore.sh --list             # List snapshots
# ==================================================

set -euo pipefail

# Load credentials
CREDS_FILE="/root/.config/restic/credentials.env"
if [ ! -f "$CREDS_FILE" ]; then
    echo "ERROR: Credentials file not found: $CREDS_FILE"
    exit 1
fi
source "$CREDS_FILE"

# Configuration
RESTORE_DIR="/tmp/restic_restore_$$"
CONTAINER="milkyhoop-dev-postgres-1"
DB_NAME="milkydb"
DB_USER="postgres"

# Show help
if [ "${1:-}" == "--help" ] || [ "${1:-}" == "-h" ]; then
    echo "MilkyHoop Restic Restore Script"
    echo ""
    echo "Usage:"
    echo "  $0                    Restore latest snapshot"
    echo "  $0 <snapshot_id>      Restore specific snapshot"
    echo "  $0 --list             List available snapshots"
    echo "  $0 --verify           Verify repository integrity"
    echo ""
    exit 0
fi

# List snapshots
if [ "${1:-}" == "--list" ]; then
    echo "Available snapshots:"
    restic snapshots
    exit 0
fi

# Verify repository
if [ "${1:-}" == "--verify" ]; then
    echo "Verifying repository integrity..."
    restic check
    echo "Repository OK"
    exit 0
fi

# Determine snapshot to restore
SNAPSHOT="${1:-latest}"

echo "=========================================="
echo "MilkyHoop Disaster Recovery"
echo "Snapshot: $SNAPSHOT"
echo "=========================================="
echo ""
echo "WARNING: This will restore the database!"
echo "Current data in the database will be REPLACED."
echo ""
read -p "Are you sure you want to continue? (yes/no): " CONFIRM
if [ "$CONFIRM" != "yes" ]; then
    echo "Aborted."
    exit 1
fi

# Create restore directory
mkdir -p "$RESTORE_DIR"
trap "rm -rf $RESTORE_DIR" EXIT

# Step 1: Restore from Restic
echo "[1/3] Restoring from Restic..."
restic restore "$SNAPSHOT" --target "$RESTORE_DIR"
echo "    Files restored to $RESTORE_DIR"

# Step 2: Find database dump
echo "[2/3] Looking for database dump..."
DB_DUMP=$(find "$RESTORE_DIR" -name "milkydb_*.sql" | head -1)
if [ -z "$DB_DUMP" ]; then
    echo "ERROR: No database dump found in backup!"
    exit 1
fi
echo "    Found: $DB_DUMP"

# Step 3: Restore database
echo "[3/3] Restoring database..."
echo "    Stopping application containers..."
docker compose -f /root/milkyhoop-dev/docker-compose.yml stop api_gateway conversation_service || true

echo "    Restoring database..."
cat "$DB_DUMP" | docker exec -i "$CONTAINER" psql -U "$DB_USER" -d "$DB_NAME"

echo "    Restarting application containers..."
docker compose -f /root/milkyhoop-dev/docker-compose.yml start api_gateway conversation_service || true

echo ""
echo "=========================================="
echo "Restore Complete!"
echo "=========================================="
echo ""
echo "Restored configs are available at: $RESTORE_DIR/configs/"
echo "You may want to review and apply them manually."
echo ""
echo "Next steps:"
echo "1. Verify the application is working"
echo "2. Check database integrity"
echo "3. Review and apply config changes if needed"
echo ""
