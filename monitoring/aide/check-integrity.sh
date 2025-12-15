#!/bin/bash
# ============================================
# MilkyHoop File Integrity Check
# ISO 27001:2022 - A.8.9 Configuration Management
# ============================================
# Cron: 0 3 * * * /root/milkyhoop-dev/monitoring/aide/check-integrity.sh
# ============================================

set -euo pipefail

AIDE_CONFIG="/root/milkyhoop-dev/monitoring/aide/milkyhoop.conf"
AIDE_DB="/var/lib/aide/aide.db"
LOG_DIR="/var/log/aide"
REPORT_FILE="$LOG_DIR/aide-$(date +%Y%m%d_%H%M%S).log"
WEBHOOK_URL="${DISCORD_WEBHOOK_URL:-}"

mkdir -p "$LOG_DIR" /var/lib/aide

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
}

send_alert() {
    local title="$1"
    local message="$2"
    local severity="${3:-warning}"

    log "ALERT: $title - $message"

    if [ -n "$WEBHOOK_URL" ]; then
        local color
        case "$severity" in
            critical) color="15158332" ;;
            warning)  color="15105570" ;;
            *)        color="3447003" ;;
        esac

        curl -s -H "Content-Type: application/json" \
            -d "{\"embeds\":[{\"title\":\"🔒 $title\",\"description\":\"$message\",\"color\":$color}]}" \
            "$WEBHOOK_URL" > /dev/null 2>&1 || true
    fi
}

# Initialize database if not exists
if [ ! -f "$AIDE_DB" ]; then
    log "Initializing AIDE database..."
    aide --config="$AIDE_CONFIG" --init 2>&1 || {
        log "Failed to initialize AIDE database"
        exit 1
    }
    mv /var/lib/aide/aide.db.new "$AIDE_DB"
    log "AIDE database initialized"
    exit 0
fi

# Run integrity check
log "Running file integrity check..."

CHANGES=$(aide --config="$AIDE_CONFIG" --check 2>&1 || true)

# Save report
echo "$CHANGES" > "$REPORT_FILE"

# Parse results
ADDED=$(echo "$CHANGES" | grep -c "^added:" || true)
REMOVED=$(echo "$CHANGES" | grep -c "^removed:" || true)
CHANGED=$(echo "$CHANGES" | grep -c "^changed:" || true)

log "Results: Added=$ADDED, Removed=$REMOVED, Changed=$CHANGED"

# Alert on changes
if [ "$ADDED" -gt 0 ] || [ "$REMOVED" -gt 0 ] || [ "$CHANGED" -gt 0 ]; then
    SUMMARY="Files: +$ADDED added, -$REMOVED removed, ~$CHANGED changed"

    # Check for critical changes
    if echo "$CHANGES" | grep -qE "(sshd_config|sudoers|passwd|shadow)"; then
        send_alert "CRITICAL: Security File Modified" "$SUMMARY\n\nCritical security files were modified!" "critical"
    elif [ "$CHANGED" -gt 10 ]; then
        send_alert "High File Change Activity" "$SUMMARY\n\nMultiple files changed - investigate." "warning"
    else
        send_alert "File Integrity Changes Detected" "$SUMMARY" "info"
    fi

    # Update database for next run
    log "Updating AIDE database..."
    aide --config="$AIDE_CONFIG" --update 2>&1 || true
    mv /var/lib/aide/aide.db.new "$AIDE_DB" 2>/dev/null || true
else
    log "No changes detected - system integrity OK"
fi

# Cleanup old reports (keep 30 days)
find "$LOG_DIR" -name "aide-*.log" -mtime +30 -delete 2>/dev/null || true

log "Integrity check completed"
