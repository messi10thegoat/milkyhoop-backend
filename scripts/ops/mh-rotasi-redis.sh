#!/bin/bash
# Rotate REDIS_PASSWORD (#26 c). Owner's standing go; run ONLY in a window MASTER opens, after the
# URL-encoding release is live. NEVER prints a password: values move via files/stdin only.
# DIUJI DUA ARAH: 2026-09-23 (scratch compose project /tmp/rotdry, gateway STOPPED: forward rotation OK 08:44:48Z -- keys 3->3, SessionManager connected, consumer reconnected; forced failure -> rollback OK -- old .env restored, gateway healthz 200 in 2s, keys 3->3). Caveat: mh-recreate probe for api_gateway is hardcoded :8001.
#
# Order (why): stop the gateway FIRST -- a running gateway whose Redis password is wrong answers
# 401 SESSION_REPLACED + force_logout (session_manager fails CLOSED); a stopped one answers 502.
# The gateway is started with `docker compose up -d --no-deps` (mh-recreate could not resolve a
# STOPPED service before mh-lib used `ps -a`; 23 Sep 08:37 this left the API down ~2 min).
# Every step's exit status is CHECKED (no blind `| tail -1`).
#
# Overridable for the scratch dry-run (defaults = production):
ROT_DIR=${ROT_DIR:-/root/milkyhoop-dev}
R=${ROT_REDIS_CTR:-milkyhoop-dev-redis-1}
GW=${ROT_GW_CTR:-milkyhoop-dev-api_gateway}
HEALTH=${ROT_HEALTH:-http://localhost:8001/healthz}
CONSUMERS=${ROT_CONSUMERS:-"auth_service chatbot_service ragcrud_service"}
BK=${ROT_BACKUPS:-/root/backups}
set -uo pipefail
cd "$ROT_DIR" || exit 3
export TREE="$ROT_DIR"
TS=$(date -u +%Y%m%dT%H%M%S); OPS=${ROT_OPS:-/root/milkyhoop-dev/scripts/ops}
umask 077
redis_cmd() {  # redis_cmd <pwfile> <args...>  -- password on stdin, never on argv
  local f=$1; shift
  docker exec -i $R sh -c 'read -r P; REDISCLI_AUTH="$P" redis-cli --no-auth-warning '"$*" < "$f"
}
recreate() {  # recreate <service> -- checked
  local out rc
  out=$(bash $OPS/mh-recreate.sh "$1" 2>&1); rc=$?
  echo "   recreate $1: rc=$rc | $(echo "$out" | grep -E '^HASIL|GAGAL|PERINGATAN' | tail -1)"
  # mh-recreate: 0 = proven healthy, 3 = running but no probe (NOT a failure), 1 = unhealthy /
  # not recreated, 2 = could not resolve. Found by the 23 Sep dry-run (rc 3 read as failure).
  [ $rc = 0 ] || [ $rc = 3 ]
}
start_gw() {  # gateway is STOPPED at this point
  docker logs "$GW" > /root/logs/$(basename "$GW")-$(date +%s).log 2>&1 || true
  docker compose up -d --no-deps api_gateway >/dev/null 2>&1 || { echo "   compose up api_gateway FAILED"; return 1; }
  for i in $(seq 1 60); do [ "$(curl -s -o /dev/null -w '%{http_code}' $HEALTH)" = 200 ] && { echo "   gateway healthz 200 after ${i}s"; return 0; }; sleep 1; done
  echo "   gateway NOT healthy after 60s"; return 1
}
OLDF=$BK/redis-pw-old-$TS; NEWF=$BK/redis-pw-new-$TS; ENVB=$BK/env-pre-redis-rotation-$TS
grep -E '^REDIS_PASSWORD=' .env | head -1 | cut -d= -f2- > $OLDF
[ -s $OLDF ] || { echo "no REDIS_PASSWORD in .env"; exit 3; }
cp .env $ENVB; chmod 600 $ENVB $OLDF
if [ -z "${ROT_SKIP_CODECHECK:-}" ]; then
  docker exec $GW grep -q "quote(self.REDIS_PASSWORD" /app/backend/api_gateway/app/config.py || { echo "REFUSE: URL-encoding release not live"; exit 3; }
fi

echo "== 4a session keys + SAVE + dump copy"
N=$(redis_cmd $OLDF --scan --pattern "'session:*'" | wc -l); echo "   session keys N=$N  dbsize=$(redis_cmd $OLDF DBSIZE)"
[ "$(redis_cmd $OLDF SAVE)" = "OK" ] || { echo "SAVE failed -- stop before touching anything"; exit 4; }
docker cp $R:/data/dump.rdb $BK/redis-dump-pre-rotation-$TS.rdb && chmod 600 $BK/redis-dump-pre-rotation-$TS.rdb || exit 4
echo "   dump copy $(du -h $BK/redis-dump-pre-rotation-$TS.rdb | cut -f1)"

echo "== 4b stop gateway (clients get 502, NOT a 401 force_logout)"
docker compose stop api_gateway >/dev/null 2>&1; echo "   gateway state: $(docker inspect -f '{{.State.Status}}' $GW)"

echo "== 4c new password -> .env; recreate redis"
openssl rand -hex 32 > $NEWF; chmod 600 $NEWF
python3 - "$NEWF" "$ROT_DIR/.env" <<'PY'
import sys, re
new = open(sys.argv[1]).read().strip(); p = sys.argv[2]
s = open(p).read()
s2, n = re.subn(r"(?m)^REDIS_PASSWORD=.*$", "REDIS_PASSWORD=" + new, s, count=1)
assert n == 1
open(p, "w").write(s2)
print("   .env updated (value not shown)")
PY
recreate redis || ROLLBACK=1
ok=0; for i in $(seq 1 30); do [ "$(redis_cmd $NEWF PING 2>/dev/null)" = "PONG" ] && { ok=1; break; }; sleep 1; done
[ $ok = 1 ] || { echo "   redis does not accept the NEW password -> ROLLBACK"; ROLLBACK=1; }
N2=$(redis_cmd $NEWF --scan --pattern "'session:*'" 2>/dev/null | wc -l); echo "   session keys after redis recreate: $N2 (before $N)"
[ "$N2" = "$N" ] || { echo "   session key count changed -> ROLLBACK"; ROLLBACK=1; }

if [ "${ROLLBACK:-0}" = 0 ]; then
  echo "== 4d start gateway + recreate consumers"
  start_gw || ROLLBACK=1
  for s in $CONSUMERS; do recreate $s || ROLLBACK=1; done
fi
if [ "${ROLLBACK:-0}" = 0 ]; then
  echo "== 4e verify"
  sleep 3
  SM=$(docker logs --since 3m $GW 2>&1 | grep -c "SessionManager connected to Redis")
  FAIL=$(docker logs --since 3m $GW 2>&1 | grep -ciE "Redis (rate limiter |cache )?connection failed")
  HZ=$(curl -s -o /dev/null -w "%{http_code}" $HEALTH)
  N3=$(redis_cmd $NEWF --scan --pattern "'session:*'" | wc -l)
  echo "   SessionManager connected lines=$SM | redis failures=$FAIL | healthz=$HZ | session keys=$N3 (before $N)"
  [ -n "${ROT_FORCE_FAIL:-}" ] && { echo "   ROT_FORCE_FAIL set -> treating verification as FAILED (dry-run of the rollback path)"; SM=0; }
  [ "$SM" -ge 1 ] && [ "$FAIL" = 0 ] && [ "$HZ" = 200 ] && [ "$N3" = "$N" ] || ROLLBACK=1
fi

if [ "${ROLLBACK:-0}" = 1 ]; then
  echo "== ROLLBACK: gateway stopped FIRST, old .env back, redis + consumers, then gateway"
  docker compose stop api_gateway >/dev/null 2>&1
  cp $ENVB .env
  recreate redis
  for s in $CONSUMERS; do recreate $s; done
  start_gw || echo "   !!! gateway still down -- start by hand: cd $ROT_DIR && docker compose up -d --no-deps api_gateway"
  sleep 3
  echo "   after rollback: SessionManager=$(docker logs --since 1m $GW 2>&1 | grep -c 'SessionManager connected to Redis') healthz=$(curl -s -o /dev/null -w '%{http_code}' $HEALTH) session keys=$(redis_cmd $OLDF --scan --pattern "'session:*'" | wc -l) (before $N)"
  shred -u $NEWF 2>/dev/null || rm -f $NEWF
  exit 1
fi
shred -u $NEWF 2>/dev/null || rm -f $NEWF
echo "ROTATED at $(date -u +%FT%TZ). Old value kept root-only in $OLDF and $ENVB"
