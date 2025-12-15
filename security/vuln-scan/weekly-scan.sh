#!/bin/bash
# ============================================
# MilkyHoop Weekly Vulnerability Scan
# ISO 27001:2022 - A.8.8 Technical Vulnerability Management
# ============================================
# Cron: 0 1 * * 0  (Every Sunday at 1 AM)
# ============================================

set -euo pipefail

SCAN_DIR="/root/milkyhoop-dev/security/vuln-scan"
REPORT_DIR="$SCAN_DIR/reports"
DATE=$(date +%Y%m%d_%H%M%S)
REPORT_FILE="$REPORT_DIR/vuln-report-$DATE.md"
WEBHOOK_URL="${DISCORD_WEBHOOK_URL:-}"

# Target configuration
TARGET_URL="https://milkyhoop.com"
DOCKER_IMAGES=(
    "milkyhoop-dev-api_gateway-1"
    "milkyhoop-dev-auth_service-1"
    "milkyhoop-dev-transaction_service-1"
    "milkyhoop-dev-inventory_service-1"
    "milkyhoop-dev-postgres-1"
)

mkdir -p "$REPORT_DIR"

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
            critical) color="15158332" ;;  # Red
            high)     color="15105570" ;;  # Orange
            medium)   color="16776960" ;;  # Yellow
            *)        color="3447003" ;;   # Blue
        esac

        curl -s -H "Content-Type: application/json" \
            -d "{\"embeds\":[{\"title\":\"🔍 $title\",\"description\":\"$message\",\"color\":$color}]}" \
            "$WEBHOOK_URL" > /dev/null 2>&1 || true
    fi
}

# Initialize report
cat > "$REPORT_FILE" << EOF
# Vulnerability Scan Report
## MilkyHoop Platform

| Generated | $(date '+%Y-%m-%d %H:%M:%S') |
|-----------|---------------------------|
| Target | $TARGET_URL |
| Scan Type | Weekly Comprehensive |
| ISO Control | A.8.8 Technical Vulnerability Management |

---

EOF

log "Starting weekly vulnerability scan..."

# ============================================
# SECTION 1: Nuclei Web Application Scan
# ============================================
log "Running Nuclei web scan..."

echo "## 1. Web Application Vulnerabilities (Nuclei)" >> "$REPORT_FILE"
echo "" >> "$REPORT_FILE"

NUCLEI_OUTPUT=$(nuclei -u "$TARGET_URL" -severity critical,high,medium \
    -exclude-templates dos,fuzzing \
    -silent -nc 2>&1) || true

if [ -n "$NUCLEI_OUTPUT" ]; then
    CRIT_COUNT=$(echo "$NUCLEI_OUTPUT" | grep -c "\[critical\]" || echo "0")
    HIGH_COUNT=$(echo "$NUCLEI_OUTPUT" | grep -c "\[high\]" || echo "0")
    MED_COUNT=$(echo "$NUCLEI_OUTPUT" | grep -c "\[medium\]" || echo "0")

    echo "| Severity | Count |" >> "$REPORT_FILE"
    echo "|----------|-------|" >> "$REPORT_FILE"
    echo "| Critical | $CRIT_COUNT |" >> "$REPORT_FILE"
    echo "| High | $HIGH_COUNT |" >> "$REPORT_FILE"
    echo "| Medium | $MED_COUNT |" >> "$REPORT_FILE"
    echo "" >> "$REPORT_FILE"

    echo "### Findings" >> "$REPORT_FILE"
    echo '```' >> "$REPORT_FILE"
    echo "$NUCLEI_OUTPUT" >> "$REPORT_FILE"
    echo '```' >> "$REPORT_FILE"

    if [ "$CRIT_COUNT" -gt 0 ] || [ "$HIGH_COUNT" -gt 0 ]; then
        send_alert "Critical/High Vulnerabilities Found" \
            "Nuclei found $CRIT_COUNT critical and $HIGH_COUNT high severity issues" \
            "critical"
    fi
else
    echo "✅ No vulnerabilities found" >> "$REPORT_FILE"
fi
echo "" >> "$REPORT_FILE"

# ============================================
# SECTION 2: Trivy Container Scan
# ============================================
log "Running Trivy container scans..."

echo "## 2. Container Vulnerabilities (Trivy)" >> "$REPORT_FILE"
echo "" >> "$REPORT_FILE"

TOTAL_CRIT=0
TOTAL_HIGH=0

for IMAGE in "${DOCKER_IMAGES[@]}"; do
    log "Scanning $IMAGE..."

    # Get image ID from running container
    IMAGE_ID=$(docker inspect --format='{{.Image}}' "$IMAGE" 2>/dev/null || echo "")

    if [ -n "$IMAGE_ID" ]; then
        echo "### $IMAGE" >> "$REPORT_FILE"

        TRIVY_JSON=$(trivy image --format json --severity HIGH,CRITICAL \
            --quiet "$IMAGE_ID" 2>/dev/null || echo "{}")

        # Parse results
        CRIT=$(echo "$TRIVY_JSON" | jq '[.Results[]?.Vulnerabilities[]? | select(.Severity=="CRITICAL")] | length' 2>/dev/null || echo "0")
        HIGH=$(echo "$TRIVY_JSON" | jq '[.Results[]?.Vulnerabilities[]? | select(.Severity=="HIGH")] | length' 2>/dev/null || echo "0")

        TOTAL_CRIT=$((TOTAL_CRIT + CRIT))
        TOTAL_HIGH=$((TOTAL_HIGH + HIGH))

        echo "- Critical: $CRIT" >> "$REPORT_FILE"
        echo "- High: $HIGH" >> "$REPORT_FILE"
        echo "" >> "$REPORT_FILE"
    else
        echo "### $IMAGE" >> "$REPORT_FILE"
        echo "⚠️ Container not running or not found" >> "$REPORT_FILE"
        echo "" >> "$REPORT_FILE"
    fi
done

echo "### Summary" >> "$REPORT_FILE"
echo "- Total Critical: $TOTAL_CRIT" >> "$REPORT_FILE"
echo "- Total High: $TOTAL_HIGH" >> "$REPORT_FILE"
echo "" >> "$REPORT_FILE"

if [ "$TOTAL_CRIT" -gt 0 ]; then
    send_alert "Container Vulnerabilities Detected" \
        "Found $TOTAL_CRIT critical and $TOTAL_HIGH high severity container vulnerabilities" \
        "high"
fi

# ============================================
# SECTION 3: Dependency Scan
# ============================================
log "Running dependency scans..."

echo "## 3. Dependency Vulnerabilities" >> "$REPORT_FILE"
echo "" >> "$REPORT_FILE"

# Python dependencies
if [ -f "/root/milkyhoop-dev/backend/api_gateway/requirements.txt" ]; then
    echo "### Python Dependencies (api_gateway)" >> "$REPORT_FILE"

    PY_VULNS=$(trivy fs --scanners vuln --severity HIGH,CRITICAL \
        --format json /root/milkyhoop-dev/backend/api_gateway/requirements.txt 2>/dev/null || echo "{}")

    PY_CRIT=$(echo "$PY_VULNS" | jq '[.Results[]?.Vulnerabilities[]? | select(.Severity=="CRITICAL")] | length' 2>/dev/null || echo "0")
    PY_HIGH=$(echo "$PY_VULNS" | jq '[.Results[]?.Vulnerabilities[]? | select(.Severity=="HIGH")] | length' 2>/dev/null || echo "0")

    echo "- Critical: $PY_CRIT" >> "$REPORT_FILE"
    echo "- High: $PY_HIGH" >> "$REPORT_FILE"
    echo "" >> "$REPORT_FILE"
fi

# Node.js dependencies
if [ -f "/root/milkyhoop-dev/frontend/web/package-lock.json" ]; then
    echo "### Node.js Dependencies (frontend)" >> "$REPORT_FILE"

    NODE_VULNS=$(trivy fs --scanners vuln --severity HIGH,CRITICAL \
        --format json /root/milkyhoop-dev/frontend/web/package-lock.json 2>/dev/null || echo "{}")

    NODE_CRIT=$(echo "$NODE_VULNS" | jq '[.Results[]?.Vulnerabilities[]? | select(.Severity=="CRITICAL")] | length' 2>/dev/null || echo "0")
    NODE_HIGH=$(echo "$NODE_VULNS" | jq '[.Results[]?.Vulnerabilities[]? | select(.Severity=="HIGH")] | length' 2>/dev/null || echo "0")

    echo "- Critical: $NODE_CRIT" >> "$REPORT_FILE"
    echo "- High: $NODE_HIGH" >> "$REPORT_FILE"
    echo "" >> "$REPORT_FILE"
fi

# ============================================
# SECTION 4: Summary & Recommendations
# ============================================
echo "## 4. Remediation Requirements" >> "$REPORT_FILE"
echo "" >> "$REPORT_FILE"
echo "| Priority | Timeline | Action Required |" >> "$REPORT_FILE"
echo "|----------|----------|-----------------|" >> "$REPORT_FILE"
echo "| Critical | 24 hours | Immediate patching |" >> "$REPORT_FILE"
echo "| High | 7 days | Prioritized remediation |" >> "$REPORT_FILE"
echo "| Medium | 30 days | Scheduled fix |" >> "$REPORT_FILE"
echo "" >> "$REPORT_FILE"

echo "---" >> "$REPORT_FILE"
echo "" >> "$REPORT_FILE"
echo "*Report generated by MilkyHoop Security Scanner*" >> "$REPORT_FILE"
echo "*ISO 27001:2022 Compliance - A.8.8 Technical Vulnerability Management*" >> "$REPORT_FILE"

log "Scan complete. Report saved to: $REPORT_FILE"

# Cleanup old reports (keep 90 days)
find "$REPORT_DIR" -name "vuln-report-*.md" -mtime +90 -delete 2>/dev/null || true

# Summary notification
send_alert "Weekly Scan Complete" \
    "Vulnerability scan completed. Report: $REPORT_FILE" \
    "info"

log "Weekly vulnerability scan completed"
