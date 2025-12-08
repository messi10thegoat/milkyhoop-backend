#!/bin/bash
# ==================================================
# MilkyHoop Encrypted Backup Restore Script
# Restore database from age-encrypted backup
# ==================================================
# Usage: ./restore_encrypted.sh <backup_file.sql.gz.age>
# ==================================================

set -e

# Configuration
CONTAINER="milkyhoop-dev-postgres-1"
DB_NAME="milkydb"
DB_USER="postgres"
AGE_KEY_FILE="/root/.config/sops/age/keys.txt"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

usage() {
    echo "Usage: $0 <backup_file.sql.gz.age>"
    echo ""
    echo "Example: $0 milkydb_backup_20251207_020001.sql.gz.age"
    echo ""
    echo "Available backups:"
    ls -lht /root/milkyhoop-dev/backups/*.sql.gz.age 2>/dev/null | head -10 || echo "  No encrypted backups found"
    exit 1
}

if [ -z "$1" ]; then
    usage
fi

BACKUP_FILE="$1"

# Check if file exists
if [ ! -f "$BACKUP_FILE" ]; then
    # Try with full path
    BACKUP_FILE="/root/milkyhoop-dev/backups/$1"
    if [ ! -f "$BACKUP_FILE" ]; then
        echo -e "${RED}Error: Backup file not found: $1${NC}"
        exit 1
    fi
fi

# Check age key
if [ ! -f "$AGE_KEY_FILE" ]; then
    echo -e "${RED}Error: Age key file not found: $AGE_KEY_FILE${NC}"
    exit 1
fi

echo -e "${YELLOW}=== MilkyHoop Encrypted Backup Restore ===${NC}"
echo "Backup file: $BACKUP_FILE"
echo ""

# Confirm restore
read -p "This will REPLACE the current database. Continue? (yes/no): " CONFIRM
if [ "$CONFIRM" != "yes" ]; then
    echo "Aborted."
    exit 0
fi

echo ""
echo "[1/3] Decrypting backup..."
TEMP_FILE=$(mktemp)
age --decrypt -i "$AGE_KEY_FILE" "$BACKUP_FILE" > "$TEMP_FILE"
echo "      Decrypted to temporary file"

echo "[2/3] Restoring database..."
# Drop existing connections
docker exec $CONTAINER psql -U $DB_USER -d postgres -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '$DB_NAME' AND pid <> pg_backend_pid();" 2>/dev/null || true

# Drop and recreate database
docker exec $CONTAINER psql -U $DB_USER -d postgres -c "DROP DATABASE IF EXISTS $DB_NAME;"
docker exec $CONTAINER psql -U $DB_USER -d postgres -c "CREATE DATABASE $DB_NAME;"

# Restore
gunzip -c "$TEMP_FILE" | docker exec -i $CONTAINER psql -U $DB_USER -d $DB_NAME

echo "[3/3] Cleaning up..."
rm -f "$TEMP_FILE"

echo ""
echo -e "${GREEN}=== Restore Completed Successfully ===${NC}"
echo ""
echo "Verify with: docker exec $CONTAINER psql -U $DB_USER -d $DB_NAME -c '\\dt'"
