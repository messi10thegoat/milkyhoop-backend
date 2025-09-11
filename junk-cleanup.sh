#!/bin/bash

echo "=== SURGICAL JUNK CLEANUP ==="
echo "Based on successful audit findings"
echo "Generated: $(date)"
echo ""

# Known junk file patterns from successful audit
containers=$(docker ps --format "{{.Names}}" | grep "milkyhoop-dev-" | sort)

total_cleaned=0

echo "--- CLEANUP EXECUTION ---"
for container in $containers; do
    echo "Cleaning: $container"
    
    # Count before cleanup
    before=$(docker exec "$container" find /app -name '__pycache__' -type d 2>/dev/null | wc -l || echo "0")
    
    # Execute cleanup commands
    docker exec "$container" find /app -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
    docker exec "$container" find /app -name '*.pyc' -delete 2>/dev/null || true
    docker exec "$container" find /app -name '*.backup*' -delete 2>/dev/null || true
    docker exec "$container" find /app -name '*.phase*' -delete 2>/dev/null || true
    docker exec "$container" find /app -name '*.before_*' -delete 2>/dev/null || true
    docker exec "$container" find /app -name '*.old' -delete 2>/dev/null || true
    docker exec "$container" find /app -name '*.bak' -delete 2>/dev/null || true
    docker exec "$container" find /app -name '*.minimal' -delete 2>/dev/null || true
    docker exec "$container" find /app -name '*.emergency' -delete 2>/dev/null || true
    docker exec "$container" find /app -name '*.test_*' -delete 2>/dev/null || true
    
    # Count after cleanup
    after=$(docker exec "$container" find /app -name '__pycache__' -type d 2>/dev/null | wc -l || echo "0")
    cleaned=$((before - after))
    total_cleaned=$((total_cleaned + cleaned))
    
    echo "  - Cleaned $cleaned __pycache__ directories"
done

echo ""
echo "=== CLEANUP SUMMARY ==="
echo "Total __pycache__ directories removed: $total_cleaned"
echo "Plus: *.pyc, *.backup*, *.phase*, *.before_*, *.old files"
echo ""

echo "--- VERIFICATION ---"
echo "Running drift checker to verify cleanup..."
./check-container-drift.sh

echo ""
echo "=== CLEANUP COMPLETE ==="
echo "Junk files removed from all containers"
echo "Containers should now show clean or minimal drift"
