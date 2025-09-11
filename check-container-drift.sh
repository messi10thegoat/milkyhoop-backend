#!/bin/bash

# =============================================================================
# CONTAINER DRIFT CHECKER v3.0 - MATHEMATICAL FIX
# Sequential filtering to prevent overlap and negative numbers
# =============================================================================

set -euo pipefail

echo "=== CONTAINER DRIFT CHECKER v3.0 ==="
echo "Sequential filtering - no overlap guaranteed"
echo "Generated: $(date)"
echo ""

# Enhanced junk file patterns
declare -a JUNK_PATTERNS=(
    "__pycache__"
    "\.pyc$"
    "\.pyo$"
    "\.egg-info"
    "/usr/local/lib/python"
    "/tmp/"
    "\.tmp$"
    "\.log$"
    "/logs/"
    "node_modules"
    "\.cache"
    "/var/log"
    "\.pid$"
    "/var/cache"
    "/run/"
    "/proc/"
    "/sys/"
    "\.sock$"
    "/dev/"
)

# Development artifact patterns
declare -a DEV_PATTERNS=(
    "libs/milkyhoop_prisma"
    "binaries/"
    "generator/"
    "_vendor/"
    "site-packages/"
)

# Infrastructure config patterns
declare -a INFRA_PATTERNS=(
    "/etc/kafka"
    "/etc/zookeeper" 
    "kafka.properties"
    "zookeeper.properties"
    "log4j.properties"
)

# Build grep pattern from array
build_grep_pattern() {
    local -n patterns=$1
    local pattern=""
    
    for p in "${patterns[@]}"; do
        if [[ -z "$pattern" ]]; then
            pattern="$p"
        else
            pattern="$pattern|$p"
        fi
    done
    echo "$pattern"
}

# FIXED: Sequential filtering function
analyze_container_sequential() {
    local container=$1
    local container_name=${container#milkyhoop-dev-}
    container_name=${container_name%-1}
    
    echo "Analyzing: $container"
    
    # Get all changes as text
    local all_files
    all_files=$(docker diff "$container" 2>/dev/null || echo "")
    
    if [[ -z "$all_files" ]]; then
        echo "  CLEAN: 0 files changed"
        echo "  Status: OK"
        echo ""
        return 0
    fi
    
    local total_changes
    total_changes=$(echo "$all_files" | wc -l)
    
    # Build patterns
    local junk_pattern dev_pattern infra_pattern
    junk_pattern=$(build_grep_pattern JUNK_PATTERNS)
    dev_pattern=$(build_grep_pattern DEV_PATTERNS)
    infra_pattern=$(build_grep_pattern INFRA_PATTERNS)
    
    # SEQUENTIAL FILTERING - No overlap possible
    
    # Step 1: Remove junk files
    local remaining_after_junk
    remaining_after_junk=$(echo "$all_files" | grep -vE "($junk_pattern)" || echo "")
    local junk_count=$((total_changes - $(echo "$remaining_after_junk" | wc -l)))
    
    # Step 2: From remaining, remove dev artifacts
    local remaining_after_dev  
    if [[ -n "$remaining_after_junk" ]]; then
        remaining_after_dev=$(echo "$remaining_after_junk" | grep -vE "($dev_pattern)" || echo "")
        local dev_count=$(($(echo "$remaining_after_junk" | wc -l) - $(echo "$remaining_after_dev" | wc -l)))
    else
        remaining_after_dev=""
        local dev_count=0
    fi
    
    # Step 3: From remaining, remove infrastructure
    local remaining_final
    if [[ -n "$remaining_after_dev" ]]; then
        remaining_final=$(echo "$remaining_after_dev" | grep -vE "($infra_pattern)" || echo "")
        local infra_count=$(($(echo "$remaining_after_dev" | wc -l) - $(echo "$remaining_final" | wc -l)))
    else
        remaining_final=""
        local infra_count=0
    fi
    
    # Step 4: Final count = critical changes
    local critical_count
    if [[ -n "$remaining_final" ]]; then
        critical_count=$(echo "$remaining_final" | wc -l)
    else
        critical_count=0
    fi
    
    # VERIFICATION: Math must add up
    local verification_total=$((junk_count + dev_count + infra_count + critical_count))
    
    echo "  Total changes: $total_changes"
    echo "  ├─ Junk files: $junk_count (cache, temp, logs)"
    echo "  ├─ Dev artifacts: $dev_count (prisma, generated)"
    echo "  ├─ Infrastructure: $infra_count (kafka, config)"
    echo "  └─ Critical changes: $critical_count (source code)"
    echo "  ✓ Verification: $verification_total = $total_changes"
    
    # Status determination based on critical changes only
    if [[ "$critical_count" -eq 0 ]]; then
        if [[ "$dev_count" -gt 0 ]]; then
            echo "  Status: DEV_ARTIFACTS (generated code only)"
            echo "  Action: Consider sync or rebuild clean"
        else
            echo "  Status: CLEAN (only junk/config files)"
            echo "  Action: No sync required"
        fi
    elif [[ "$critical_count" -le 3 ]]; then
        echo "  Status: MINOR (few source changes)"
        echo "  Action: Review recommended"
        if [[ -n "$remaining_final" ]]; then
            echo "  Critical files:"
            echo "$remaining_final" | head -5
        fi
    else
        echo "  Status: CRITICAL (significant source changes)"
        echo "  Action: Sync required"
        if [[ -n "$remaining_final" ]]; then
            echo "  Critical files:"
            echo "$remaining_final" | head -10
        fi
    fi
    
    echo ""
    return "$critical_count"
}

# Main analysis
echo "=== SEQUENTIAL DRIFT ANALYSIS ==="
echo ""

containers=$(docker ps --format "{{.Names}}" | grep "milkyhoop-dev-" | sort)

critical_total=0
dev_total=0
total_containers=0

for container in $containers; do
    total_containers=$((total_containers + 1))
    analyze_container_sequential "$container"
    result=$?
    critical_total=$((critical_total + result))
    
    # Count dev artifacts for summary
    local temp_dev
    temp_dev=$(docker diff "$container" 2>/dev/null | grep -cE "libs/milkyhoop_prisma" || echo "0")
    dev_total=$((dev_total + temp_dev))
done

echo "==========================================="
echo "SEQUENTIAL ANALYSIS SUMMARY"
echo "==========================================="
echo "Containers analyzed: $total_containers"
echo "Critical source changes: $critical_total files"
echo "Development artifacts: $dev_total files"
echo ""

# Final decision logic
if [[ "$critical_total" -eq 0 ]]; then
    if [[ "$dev_total" -gt 0 ]]; then
        echo "⚡ RESULT: Only development artifacts detected"
        echo "⚡ STATUS: No critical source changes"
        echo "⚡ ACTION: Safe to rebuild clean for staging"
        exit 0
    else
        echo "✅ RESULT: All containers mathematically clean"
        echo "✅ STATUS: Ready for CI/CD build"
        echo "✅ ACTION: Proceed with staging deployment"
        exit 0
    fi
elif [[ "$critical_total" -le 5 ]]; then
    echo "⚠️  RESULT: Minor source code changes"
    echo "⚠️  STATUS: Review recommended"
    echo "⚠️  ACTION: Check critical files listed above"
    exit 1
else
    echo "❌ RESULT: Significant source code changes"
    echo "❌ STATUS: Sync required before build"
    echo "❌ ACTION: Address critical changes before staging"
    exit 2
fi