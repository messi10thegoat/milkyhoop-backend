#\!/bin/bash
# MilkyHoop Infrastructure Monitor
# Cron: */5 * * * * /root/milkyhoop-dev/scripts/monitor.sh
LOG="/var/log/milkyhoop-monitor.log"
DATE=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
REDIS_PASS="MilkyRedis2025Secure"
MEM=$(free -m | awk "/Mem:/{printf \"%d/%d MB (%.0f%%)\", \$3, \$2, \$3/\$2*100}")
SWAP=$(free -m | awk "/Swap:/{print \$3}")
TOP_CONTAINERS=$(docker stats --no-stream --format "{{.Name}}:{{.MemUsage}}" 2>/dev/null | sort -t: -k2 -h -r | head -5 | tr "\n" ", ")
HEALTH=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 http://localhost:8001/api/health 2>/dev/null || echo "FAIL")
RATE_LIMITED=$(grep -c " 429 " /var/log/nginx/access.log 2>/dev/null || echo 0)
REDIS_HITS=$(docker exec milkyhoop-dev-redis-1 redis-cli -a "$REDIS_PASS" INFO stats 2>/dev/null | grep keyspace_hits | cut -d: -f2 | tr -d "\r" || echo "N/A")
REDIS_MISSES=$(docker exec milkyhoop-dev-redis-1 redis-cli -a "$REDIS_PASS" INFO stats 2>/dev/null | grep keyspace_misses | cut -d: -f2 | tr -d "\r" || echo "N/A")
echo "$DATE | mem=$MEM swap=${SWAP}MB | health=$HEALTH | 429s=$RATE_LIMITED | redis_hits=$REDIS_HITS misses=$REDIS_MISSES | top=$TOP_CONTAINERS" >> $LOG
if [ "$HEALTH" \!= "200" ] || [ "$SWAP" -gt 500 ] || [ "$RATE_LIMITED" -gt 50 ]; then
    echo "ALERT: $DATE health=$HEALTH swap=${SWAP}MB 429s=$RATE_LIMITED" >> $LOG
fi
