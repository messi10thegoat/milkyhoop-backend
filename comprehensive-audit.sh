#!/bin/bash

# =============================================================================
# COMPREHENSIVE CONTAINER AUDIT SCRIPT v2.0
# Based on successful simple audit approach with enhanced analysis
# =============================================================================

set -euo pipefail

AUDIT_TIMESTAMP=$(date +%Y%m%d_%H%M%S)
AUDIT_DIR="./comprehensive_audit_${AUDIT_TIMESTAMP}"

echo "=== COMPREHENSIVE CONTAINER AUDIT v2.0 ==="
echo "Enhanced analysis with proven working approach"
echo "Timestamp: $AUDIT_TIMESTAMP"
echo "Output Directory: $AUDIT_DIR"
echo ""

# Create audit directory
mkdir -p "$AUDIT_DIR"

# Define comprehensive patterns
declare -a JUNK_PATTERNS=(
    "\.pyc$"
    "__pycache__"
    "\.backup"
    "\.bak"
    "\.old"
    "\.phase"
    "\.before_"
    "\.pre_"
    "\.emergency"
    "\.test_"
    "\.minimal"
    "\.current"
    "\.enhanced"
    "\.extended"
    "\.complete"
    "/tmp/"
    "/var/log"
    "/var/cache"
)

declare -a DEV_PATTERNS=(
    "libs/milkyhoop_prisma"
    "site-packages/"
    "binaries/"
    "generator/"
    "_vendor/"
)

declare -a INFRA_PATTERNS=(
    "/etc/kafka"
    "/etc/zookeeper"
    "kafka\.properties"
    "zookeeper\.properties"
    "log4j\.properties"
)

echo "--- PHASE 1: SYSTEM BASELINE ---"
echo "Documenting system state..."

# System baseline
echo "=== SYSTEM BASELINE ===" > "$AUDIT_DIR/system_baseline.txt"
echo "Generated: $(date)" >> "$AUDIT_DIR/system_baseline.txt"
echo "" >> "$AUDIT_DIR/system_baseline.txt"
uname -a >> "$AUDIT_DIR/system_baseline.txt"
docker --version >> "$AUDIT_DIR/system_baseline.txt"
docker-compose --version >> "$AUDIT_DIR/system_baseline.txt"
echo "" >> "$AUDIT_DIR/system_baseline.txt"
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" >> "$AUDIT_DIR/system_baseline.txt"

echo "--- PHASE 2: CONTAINER ANALYSIS ---"
echo "Analyzing each container with categorization..."

containers=$(docker ps --format "{{.Names}}" | grep "milkyhoop-dev-" | sort)

# Main analysis loop
echo "=== COMPREHENSIVE ANALYSIS SUMMARY ===" > "$AUDIT_DIR/analysis_summary.txt"
echo "Generated: $(date)" >> "$AUDIT_DIR/analysis_summary.txt"
echo "" >> "$AUDIT_DIR/analysis_summary.txt"

total_junk=0
total_dev=0
total_infra=0
total_critical=0

for container in $containers; do
    container_short=${container#milkyhoop-dev-}
    container_short=${container_short%-1}
    
    echo "Analyzing: $container"
    
    # Get all changes with timeout
    all_changes=$(timeout 60s docker diff "$container" 2>/dev/null || echo "")
    
    if [[ -z "$all_changes" ]]; then
        total_changes=0
        junk_count=0
        dev_count=0
        infra_count=0
        critical_count=0
    else
        total_changes=$(echo "$all_changes" | wc -l)
        
        # Sequential filtering to prevent overlap
        remaining_after_junk="$all_changes"
        
        # Filter junk
        for pattern in "${JUNK_PATTERNS[@]}"; do
            remaining_after_junk=$(echo "$remaining_after_junk" | grep -vE "$pattern" 2>/dev/null || echo "$remaining_after_junk")
        done
        junk_count=$((total_changes - $(echo "$remaining_after_junk" | grep -c . 2>/dev/null || echo "0")))
        
        # Filter dev artifacts from remaining
        remaining_after_dev="$remaining_after_junk"
        for pattern in "${DEV_PATTERNS[@]}"; do
            remaining_after_dev=$(echo "$remaining_after_dev" | grep -vE "$pattern" 2>/dev/null || echo "$remaining_after_dev")
        done
        dev_count=$(($(echo "$remaining_after_junk" | grep -c . 2>/dev/null || echo "0") - $(echo "$remaining_after_dev" | grep -c . 2>/dev/null || echo "0")))
        
        # Filter infrastructure from remaining
        remaining_final="$remaining_after_dev"
        for pattern in "${INFRA_PATTERNS[@]}"; do
            remaining_final=$(echo "$remaining_final" | grep -vE "$pattern" 2>/dev/null || echo "$remaining_final")
        done
        infra_count=$(($(echo "$remaining_after_dev" | grep -c . 2>/dev/null || echo "0") - $(echo "$remaining_final" | grep -c . 2>/dev/null || echo "0")))
        
        # Critical = what remains
        critical_count=$(echo "$remaining_final" | grep -c . 2>/dev/null || echo "0")
    fi
    
    # Write to summary
    echo "$container:" >> "$AUDIT_DIR/analysis_summary.txt"
    echo "  Total changes: $total_changes" >> "$AUDIT_DIR/analysis_summary.txt"
    echo "  Junk files: $junk_count" >> "$AUDIT_DIR/analysis_summary.txt"
    echo "  Dev artifacts: $dev_count" >> "$AUDIT_DIR/analysis_summary.txt"
    echo "  Infrastructure: $infra_count" >> "$AUDIT_DIR/analysis_summary.txt"
    echo "  Critical changes: $critical_count" >> "$AUDIT_DIR/analysis_summary.txt"
    echo "" >> "$AUDIT_DIR/analysis_summary.txt"
    
    # Accumulate totals
    total_junk=$((total_junk + junk_count))
    total_dev=$((total_dev + dev_count))
    total_infra=$((total_infra + infra_count))
    total_critical=$((total_critical + critical_count))
    
    echo "  - Junk: $junk_count, Dev: $dev_count, Infra: $infra_count, Critical: $critical_count"
    
    # Create detailed container analysis
    container_file="$AUDIT_DIR/container_${container_short}_detailed.txt"
    echo "=== DETAILED ANALYSIS: $container ===" > "$container_file"
    echo "Generated: $(date)" >> "$container_file"
    echo "" >> "$container_file"
    
    if [[ $critical_count -gt 0 && -n "$remaining_final" ]]; then
        echo "CRITICAL FILES REQUIRING REVIEW:" >> "$container_file"
        echo "$remaining_final" | head -20 >> "$container_file"
        echo "" >> "$container_file"
    fi
    
    if [[ $junk_count -gt 10 ]]; then
        echo "SAMPLE JUNK FILES:" >> "$container_file"
        echo "$all_changes" | grep -E "$(IFS='|'; echo "${JUNK_PATTERNS[*]}")" | head -10 >> "$container_file"
        echo "" >> "$container_file"
    fi
done

echo "--- PHASE 3: SUMMARY ANALYSIS ---"

# Overall summary
echo "" >> "$AUDIT_DIR/analysis_summary.txt"
echo "=== OVERALL TOTALS ===" >> "$AUDIT_DIR/analysis_summary.txt"
echo "Total junk files: $total_junk" >> "$AUDIT_DIR/analysis_summary.txt"
echo "Total dev artifacts: $total_dev" >> "$AUDIT_DIR/analysis_summary.txt"
echo "Total infrastructure: $total_infra" >> "$AUDIT_DIR/analysis_summary.txt"
echo "Total critical changes: $total_critical" >> "$AUDIT_DIR/analysis_summary.txt"

echo "--- PHASE 4: CLEANUP RECOMMENDATIONS ---"

# Generate cleanup strategy
echo "=== CLEANUP STRATEGY ===" > "$AUDIT_DIR/cleanup_strategy.txt"
echo "Generated: $(date)" >> "$AUDIT_DIR/cleanup_strategy.txt"
echo "" >> "$AUDIT_DIR/cleanup_strategy.txt"

if [[ $total_critical -eq 0 ]]; then
    echo "STATUS: SAFE TO CLEAN" >> "$AUDIT_DIR/cleanup_strategy.txt"
    echo "No critical source code changes detected" >> "$AUDIT_DIR/cleanup_strategy.txt"
    echo "Recommended approach: Automated junk removal" >> "$AUDIT_DIR/cleanup_strategy.txt"
elif [[ $total_critical -le 10 ]]; then
    echo "STATUS: REVIEW REQUIRED" >> "$AUDIT_DIR/cleanup_strategy.txt"
    echo "Minor critical changes detected" >> "$AUDIT_DIR/cleanup_strategy.txt"
    echo "Recommended approach: Manual review + selective cleanup" >> "$AUDIT_DIR/cleanup_strategy.txt"
else
    echo "STATUS: MANUAL INTERVENTION REQUIRED" >> "$AUDIT_DIR/cleanup_strategy.txt"
    echo "Significant critical changes detected" >> "$AUDIT_DIR/cleanup_strategy.txt"
    echo "Recommended approach: Preserve critical changes + clean junk" >> "$AUDIT_DIR/cleanup_strategy.txt"
fi

echo "" >> "$AUDIT_DIR/cleanup_strategy.txt"

# Generate cleanup commands
echo "=== SAFE CLEANUP COMMANDS ===" > "$AUDIT_DIR/cleanup_commands.txt"
echo "# Generated: $(date)" >> "$AUDIT_DIR/cleanup_commands.txt"
echo "# Review before executing" >> "$AUDIT_DIR/cleanup_commands.txt"
echo "" >> "$AUDIT_DIR/cleanup_commands.txt"

for container in $containers; do
    echo "# Cleanup $container" >> "$AUDIT_DIR/cleanup_commands.txt"
    echo "echo \"Cleaning $container...\"" >> "$AUDIT_DIR/cleanup_commands.txt"
    echo "docker exec $container find /app -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true" >> "$AUDIT_DIR/cleanup_commands.txt"
    echo "docker exec $container find /app -name '*.pyc' -delete 2>/dev/null || true" >> "$AUDIT_DIR/cleanup_commands.txt"
    echo "docker exec $container find /app -name '*.backup*' -delete 2>/dev/null || true" >> "$AUDIT_DIR/cleanup_commands.txt"
    echo "docker exec $container find /app -name '*.phase*' -delete 2>/dev/null || true" >> "$AUDIT_DIR/cleanup_commands.txt"
    echo "docker exec $container find /app -name '*.before_*' -delete 2>/dev/null || true" >> "$AUDIT_DIR/cleanup_commands.txt"
    echo "docker exec $container find /app -name '*.old' -delete 2>/dev/null || true" >> "$AUDIT_DIR/cleanup_commands.txt"
    echo "" >> "$AUDIT_DIR/cleanup_commands.txt"
done

echo "=== VERIFICATION ===" >> "$AUDIT_DIR/cleanup_commands.txt"
echo "./check-container-drift.sh" >> "$AUDIT_DIR/cleanup_commands.txt"

echo ""
echo "=== COMPREHENSIVE AUDIT COMPLETE ==="
echo "Results in: $AUDIT_DIR"
echo ""
echo "Key findings:"
echo "- Junk files: $total_junk"
echo "- Dev artifacts: $total_dev" 
echo "- Critical changes: $total_critical"
echo ""
echo "Next steps:"
echo "1. Review: cat $AUDIT_DIR/analysis_summary.txt"
echo "2. Strategy: cat $AUDIT_DIR/cleanup_strategy.txt"
echo "3. Execute: bash $AUDIT_DIR/cleanup_commands.txt"
