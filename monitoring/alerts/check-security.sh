#!/bin/bash
# ============================================
# MilkyHoop Security Alert Checker
# ISO 27001:2022 - A.8.16 Monitoring Activities
# ============================================
# Run via cron every 5 minutes:
# */5 * * * * /root/milkyhoop-dev/monitoring/alerts/check-security.sh
# ============================================

set -euo pipefail

LOKI_URL="http://localhost:3100"
LOG_FILE="/var/log/milkyhoop/security-alerts.log"
ALERT_FILE="/tmp/milkyhoop-alerts.txt"
WEBHOOK_URL="${DISCORD_WEBHOOK_URL:-}"  # Set in environment

# Ensure log directory exists
mkdir -p "$(dirname "$LOG_FILE")"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

send_alert() {
    local severity="$1"
    local title="$2"
    local message="$3"

    log "ALERT [$severity]: $title - $message"

    # Send to Discord if webhook configured
    if [ -n "$WEBHOOK_URL" ]; then
        local color
        case "$severity" in
            critical) color="15158332" ;;  # Red
            warning)  color="15105570" ;;  # Orange
            info)     color="3447003" ;;   # Blue
            *)        color="8421504" ;;   # Gray
        esac

        curl -s -H "Content-Type: application/json" \
            -d "{\"embeds\":[{\"title\":\"🚨 $title\",\"description\":\"$message\",\"color\":$color,\"footer\":{\"text\":\"MilkyHoop Security Alert\"}}]}" \
            "$WEBHOOK_URL" > /dev/null 2>&1 || true
    fi
}

check_loki() {
    local query="$1"
    local description="$2"

    # Query Loki for last 5 minutes
    local end=$(date +%s)000000000
    local start=$(( end - 300000000000 ))  # 5 minutes ago

    local result=$(curl -s -G "$LOKI_URL/loki/api/v1/query_range" \
        --data-urlencode "query=$query" \
        --data-urlencode "start=$start" \
        --data-urlencode "end=$end" \
        --data-urlencode "limit=100" 2>/dev/null || echo '{"data":{"result":[]}}')

    # Count results
    local count=$(echo "$result" | jq -r '.data.result | length' 2>/dev/null || echo "0")

    echo "$count"
}

# ============================================
# Security Checks
# ============================================

log "Starting security check..."

# 1. Failed Login Attempts
FAILED_LOGINS=$(check_loki '{job="api_gateway"} |= "Invalid email or password"' "Failed logins")
if [ "$FAILED_LOGINS" -gt 10 ]; then
    send_alert "warning" "High Failed Login Rate" "Detected $FAILED_LOGINS failed login attempts in 5 minutes"
fi

# 2. WAF Blocks
WAF_BLOCKS=$(check_loki '{job="api_gateway"} |= "Blocked:"' "WAF blocks")
if [ "$WAF_BLOCKS" -gt 20 ]; then
    send_alert "warning" "High WAF Block Rate" "WAF blocked $WAF_BLOCKS requests in 5 minutes"
fi

# 3. Rate Limit Triggers
RATE_LIMITS=$(check_loki '{job="api_gateway"} |= "429"' "Rate limits")
if [ "$RATE_LIMITS" -gt 50 ]; then
    send_alert "warning" "Rate Limit Exceeded" "$RATE_LIMITS requests rate-limited in 5 minutes"
fi

# 4. Account Lockouts
LOCKOUTS=$(check_loki '{job="api_gateway"} |= "locked"' "Lockouts")
if [ "$LOCKOUTS" -gt 3 ]; then
    send_alert "warning" "Multiple Account Lockouts" "$LOCKOUTS accounts locked in 5 minutes"
fi

# 5. Error Rate
ERRORS=$(check_loki '{job="api_gateway"} |= "ERROR"' "Errors")
if [ "$ERRORS" -gt 100 ]; then
    send_alert "critical" "High Error Rate" "$ERRORS errors in 5 minutes - investigate immediately"
fi

# 6. Unauthorized Access Attempts
UNAUTHORIZED=$(check_loki '{job="api_gateway"} |= "Unauthorized" |= "tenant"' "Unauthorized")
if [ "$UNAUTHORIZED" -gt 0 ]; then
    send_alert "critical" "Unauthorized Access Attempt" "Detected $UNAUTHORIZED cross-tenant access attempts"
fi

# 7. MFA Failures
MFA_FAILS=$(check_loki '{job="api_gateway"} |= "MFA" |= "Invalid"' "MFA failures")
if [ "$MFA_FAILS" -gt 5 ]; then
    send_alert "warning" "MFA Verification Failures" "$MFA_FAILS failed MFA attempts in 5 minutes"
fi

log "Security check completed"

# ============================================
# Summary Report (daily at midnight)
# ============================================
if [ "$(date +%H%M)" == "0000" ]; then
    log "Generating daily security summary..."

    # Get 24h stats
    DAILY_FAILED=$(check_loki '{job="api_gateway"} |= "login" |= "failed"' "Daily failed logins" | head -1)
    DAILY_WAF=$(check_loki '{job="api_gateway"} |= "Blocked"' "Daily WAF blocks" | head -1)
    DAILY_ERRORS=$(check_loki '{job="api_gateway"} |= "ERROR"' "Daily errors" | head -1)

    send_alert "info" "Daily Security Summary" "Failed logins: $DAILY_FAILED | WAF blocks: $DAILY_WAF | Errors: $DAILY_ERRORS"
fi
