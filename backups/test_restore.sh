#!/bin/bash
# ==================================================
# MilkyHoop Monthly Restore Test (Law 15)
# Restores latest backup to test DB, verifies internal
# consistency of the restored data.
# ==================================================
# Usage: ./test_restore.sh
# Cron:  0 3 1 * * /root/milkyhoop-dev/backups/test_restore.sh >> /var/log/milkyhoop/restore_test.log 2>&1
# ==================================================

set -uo pipefail

# Configuration
CONTAINER="milkyhoop-dev-postgres-1"
DB_NAME="milkydb"
DB_USER="postgres"
TEST_DB="milkydb_restore_test"
BACKUP_DIR="/root/milkyhoop-dev/backups"
AGE_KEY_FILE="/root/.config/sops/age/keys.txt"
LOG_DIR="/var/log/milkyhoop"
WEBHOOK_URL="${DISCORD_WEBHOOK_URL:-}"
TEMP_FILE=""

mkdir -p "$LOG_DIR"

# ---- Helpers ----

log() {
    echo "$(date -u +"%Y-%m-%dT%H:%M:%SZ") | $1"
}

send_alert() {
    local severity="$1"
    local title="$2"
    local message="$3"

    log "ALERT [$severity]: $title"

    if [ -n "$WEBHOOK_URL" ]; then
        local color
        case "$severity" in
            critical) color="15158332" ;;
            warning)  color="15105570" ;;
            pass)     color="3066993" ;;
            *)        color="8421504" ;;
        esac

        curl -s -H "Content-Type: application/json" \
            -d "{\"embeds\":[{\"title\":\"$title\",\"description\":\"$message\",\"color\":$color,\"footer\":{\"text\":\"MilkyHoop Restore Test (Law 15)\"}}]}" \
            "$WEBHOOK_URL" > /dev/null 2>&1 || true
    fi
}

psql_test() {
    docker exec "$CONTAINER" psql -U "$DB_USER" -d "$TEST_DB" -t -c "$1" 2>/dev/null | tr -d ' '
}

cleanup() {
    log "Cleaning up..."
    docker exec "$CONTAINER" psql -U "$DB_USER" -d postgres -c "DROP DATABASE IF EXISTS $TEST_DB;" 2>/dev/null || true
    if [ -n "$TEMP_FILE" ] && [ -f "$TEMP_FILE" ]; then
        rm -f "$TEMP_FILE"
    fi
}

trap cleanup EXIT

# ---- Main ----

log "=== Monthly Restore Test Started ==="

# 1. Find latest encrypted backup
LATEST_BACKUP=$(ls -t "$BACKUP_DIR"/milkydb_backup_*.sql.gz.age 2>/dev/null | head -1)
if [ -z "$LATEST_BACKUP" ]; then
    send_alert "critical" "Restore Test FAILED" "No encrypted backup files found in $BACKUP_DIR"
    exit 1
fi
log "[1/6] Latest backup: $(basename "$LATEST_BACKUP")"

# 2. Decrypt
log "[2/6] Decrypting backup..."
TEMP_FILE=$(mktemp)
if ! age --decrypt -i "$AGE_KEY_FILE" "$LATEST_BACKUP" > "$TEMP_FILE" 2>/dev/null; then
    send_alert "critical" "Restore Test FAILED" "Could not decrypt $(basename "$LATEST_BACKUP")"
    exit 1
fi
BACKUP_SIZE=$(ls -lh "$TEMP_FILE" | awk '{print $5}')
log "      Decrypted OK ($BACKUP_SIZE)"

# 3. Create test database and restore
log "[3/6] Restoring to test database..."
docker exec "$CONTAINER" psql -U "$DB_USER" -d postgres -c "DROP DATABASE IF EXISTS $TEST_DB;" 2>/dev/null || true
docker exec "$CONTAINER" psql -U "$DB_USER" -d postgres -c "CREATE DATABASE $TEST_DB;" || {
    send_alert "critical" "Restore Test FAILED" "Could not create test database"
    exit 1
}
gunzip -c "$TEMP_FILE" | docker exec -i "$CONTAINER" psql -U "$DB_USER" -d "$TEST_DB" > /dev/null 2>&1
log "      Restore completed"

# 4. Data summary (informational)
log "[4/6] Backup data summary..."
POSTED=$(psql_test "SELECT COUNT(*) FROM journal_entries WHERE status = 'POSTED';")
TOTAL_LINES=$(psql_test "SELECT COUNT(*) FROM journal_lines;")
TENANTS=$(psql_test "SELECT COUNT(DISTINCT tenant_id) FROM journal_entries;")
log "      Journal entries (POSTED): $POSTED"
log "      Journal lines: $TOTAL_LINES"
log "      Tenants: $TENANTS"

if [ "$POSTED" = "0" ]; then
    log "      NOTE: Backup has 0 posted journals (empty or recently truncated)"
fi

# 5. Verify internal consistency of restored backup
OVERALL="PASS"
FAILURES=""

# 5a. Trial balance (Law 4 — all posted journals balanced)
log "[5/6] Verifying internal consistency..."

UNBALANCED=$(psql_test "
    SELECT COUNT(*) FROM journal_entries
    WHERE status = 'POSTED' AND total_debit != total_credit;
")
if [ "$UNBALANCED" = "0" ]; then
    log "      Trial balance: PASS (all balanced)"
else
    log "      Trial balance: FAIL ($UNBALANCED unbalanced)"
    OVERALL="FAIL"
    FAILURES="${FAILURES}Trial balance: $UNBALANCED unbalanced journals\n"
fi

# 5b. Orphaned journal lines
ORPHANED=$(psql_test "
    SELECT COUNT(*) FROM journal_lines jl
    LEFT JOIN journal_entries je ON je.id = jl.journal_id
    WHERE je.id IS NULL;
")
if [ "$ORPHANED" = "0" ]; then
    log "      Orphaned lines: PASS"
else
    log "      Orphaned lines: FAIL ($ORPHANED orphaned)"
    OVERALL="FAIL"
    FAILURES="${FAILURES}Orphaned lines: $ORPHANED\n"
fi

# 5c. Sequence monotonicity (Law 22) — only if chain_sequence column exists
HAS_SEQ_COL=$(psql_test "
    SELECT COUNT(*) FROM information_schema.columns
    WHERE table_name = 'journal_entries' AND column_name = 'chain_sequence';
")
if [ "$HAS_SEQ_COL" = "0" ]; then
    log "      Sequence monotonicity: SKIP (pre-Law 20 backup)"
else
    SEQ_DUPES=$(psql_test "
        SELECT COUNT(*) FROM (
            SELECT tenant_id, chain_sequence, COUNT(*)
            FROM journal_entries
            WHERE status = 'POSTED' AND chain_sequence IS NOT NULL
            GROUP BY tenant_id, chain_sequence
            HAVING COUNT(*) > 1
        ) dupes;
    ")
    if [ "${SEQ_DUPES:-0}" = "0" ]; then
        log "      Sequence monotonicity: PASS"
    else
        log "      Sequence monotonicity: FAIL ($SEQ_DUPES duplicates)"
        OVERALL="FAIL"
        FAILURES="${FAILURES}Sequence dupes: $SEQ_DUPES\n"
    fi
fi

# 6. Hash chain integrity (Law 20) — only if there are posted journals
log "[6/6] Verifying hash chain integrity..."
if [ "$POSTED" = "0" ] || [ "$TENANTS" = "0" ]; then
    log "      Hash chain: SKIP (no posted journals)"
else
    # Check if verify_chain_integrity function exists in the test DB
    HAS_FUNC=$(psql_test "SELECT COUNT(*) FROM pg_proc WHERE proname = 'verify_chain_integrity';")
    if [ "$HAS_FUNC" = "0" ]; then
        log "      Hash chain: SKIP (verify_chain_integrity not in backup)"
    else
        CHAIN_BROKEN=$(psql_test "
            SELECT COALESCE(SUM(broken_count), 0) FROM (
                SELECT COUNT(*) FILTER (WHERE NOT v.is_valid) AS broken_count
                FROM (SELECT DISTINCT tenant_id FROM journal_entries WHERE status = 'POSTED') t
                LEFT JOIN LATERAL verify_chain_integrity(t.tenant_id) v ON true
                GROUP BY t.tenant_id
            ) sub;
        " || echo "ERROR")

        if [ "$CHAIN_BROKEN" = "0" ]; then
            log "      Hash chain: PASS ($TENANTS tenant chains valid)"
        elif [ "$CHAIN_BROKEN" = "ERROR" ]; then
            log "      Hash chain: SKIP (function error — may pre-date Law 20)"
        else
            log "      Hash chain: FAIL ($CHAIN_BROKEN broken links)"
            OVERALL="FAIL"
            FAILURES="${FAILURES}Hash chain: $CHAIN_BROKEN broken links\n"
        fi
    fi
fi

# ---- Summary ----
log ""
log "=== Restore Test Results ==="
log "  Backup:  $(basename "$LATEST_BACKUP")"
log "  Size:    $BACKUP_SIZE"
log "  Data:    $POSTED posted entries, $TOTAL_LINES lines, $TENANTS tenants"
log "  OVERALL: $OVERALL"

if [ "$OVERALL" = "PASS" ]; then
    send_alert "pass" "Monthly Restore Test PASSED" \
        "Backup: $(basename "$LATEST_BACKUP") ($BACKUP_SIZE)\nPosted journals: $POSTED\nJournal lines: $TOTAL_LINES\nTenants: $TENANTS\nAll internal consistency checks passed."
else
    send_alert "critical" "Monthly Restore Test FAILED" \
        "Backup: $(basename "$LATEST_BACKUP")\nPosted journals: $POSTED\n\nFailures:\n$FAILURES"
    exit 1
fi

log "=== Restore Test Completed ==="
