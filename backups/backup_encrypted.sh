#!/bin/bash
# ==================================================
# MilkyHoop Encrypted Backup Script
# Database backup with age encryption
# ==================================================
# Usage: ./backup_encrypted.sh
# Cron:  0 2 * * * /root/milkyhoop-dev/backups/backup_encrypted.sh >> /var/log/milkyhoop/backup.log 2>&1
# ==================================================

set -e

# Configuration
BACKUP_DIR="/root/milkyhoop-dev/backups"
LOG_DIR="/var/log/milkyhoop"
DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="$BACKUP_DIR/milkydb_backup_$DATE.sql.gz"
ENCRYPTED_FILE="$BACKUP_DIR/milkydb_backup_$DATE.sql.gz.age"
CONTAINER="milkyhoop-dev-postgres-1"
DB_NAME="milkydb"
DB_USER="postgres"

# Age public key for encryption
AGE_PUBLIC_KEY="age1ha44p0556qhxmhqt46c6jj8xat0n0kwulesr4a3dngulv9vsmq6qnkwvgn"

# Retention settings
MAX_BACKUPS=7
MAX_ENCRYPTED_BACKUPS=30

# Ensure directories exist
mkdir -p "$BACKUP_DIR" "$LOG_DIR"

echo "=== MilkyHoop Encrypted Backup Started: $(date) ==="

# Create backup using docker exec + pg_dump
echo "[1/4] Creating database dump..."
docker exec $CONTAINER pg_dump -U $DB_USER -d $DB_NAME --no-owner --no-acl | gzip > "$BACKUP_FILE"

if [ ! -f "$BACKUP_FILE" ] || [ ! -s "$BACKUP_FILE" ]; then
    echo "ERROR: Backup file not created or empty!"
    exit 1
fi

UNENCRYPTED_SIZE=$(ls -lh "$BACKUP_FILE" | awk '{print $5}')
echo "    Unencrypted backup: $BACKUP_FILE ($UNENCRYPTED_SIZE)"

# Encrypt with age
echo "[2/4] Encrypting backup with age..."
age -r "$AGE_PUBLIC_KEY" -o "$ENCRYPTED_FILE" "$BACKUP_FILE"

if [ -f "$ENCRYPTED_FILE" ]; then
    ENCRYPTED_SIZE=$(ls -lh "$ENCRYPTED_FILE" | awk '{print $5}')
    echo "    Encrypted backup: $ENCRYPTED_FILE ($ENCRYPTED_SIZE)"

    # Remove unencrypted backup
    rm -f "$BACKUP_FILE"
    echo "    Removed unencrypted backup"
else
    echo "ERROR: Encryption failed!"
    exit 1
fi

# Verify encrypted file can be decrypted (dry run)
echo "[3/4] Verifying encryption..."
if age --decrypt -i /root/.config/sops/age/keys.txt "$ENCRYPTED_FILE" | gunzip | head -1 > /dev/null 2>&1; then
    echo "    Encryption verified OK"
else
    echo "WARNING: Could not verify encryption (key may not be available)"
fi

# Cleanup old backups
echo "[4/4] Cleaning old backups..."

# Remove old unencrypted backups (keep last $MAX_BACKUPS)
cd $BACKUP_DIR
if ls milkydb_backup_*.sql.gz 2>/dev/null | grep -v '.age$' > /dev/null; then
    ls -t milkydb_backup_*.sql.gz 2>/dev/null | grep -v '.age$' | tail -n +$((MAX_BACKUPS + 1)) | xargs -r rm -f
fi

# Remove old encrypted backups (keep last $MAX_ENCRYPTED_BACKUPS)
if ls milkydb_backup_*.sql.gz.age 2>/dev/null > /dev/null; then
    ls -t milkydb_backup_*.sql.gz.age 2>/dev/null | tail -n +$((MAX_ENCRYPTED_BACKUPS + 1)) | xargs -r rm -f
fi

# Count remaining
UNENCRYPTED_COUNT=$(ls milkydb_backup_*.sql.gz 2>/dev/null | grep -v '.age$' | wc -l || echo "0")
ENCRYPTED_COUNT=$(ls milkydb_backup_*.sql.gz.age 2>/dev/null | wc -l || echo "0")
echo "    Backups remaining: $UNENCRYPTED_COUNT unencrypted, $ENCRYPTED_COUNT encrypted"

echo "=== Backup Completed Successfully: $(date) ==="
echo ""
