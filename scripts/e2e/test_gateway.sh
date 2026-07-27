#!/bin/bash
# =============================================================================
# test_gateway.sh — isolated api_gateway serving THIS worktree, for pre-deploy testing.
#   up   : start `mh-test-gw` on $PORT (default 8002), same image + network as the live gateway,
#          bind-mounting the worktree code (ro). Lets run_all.sh (B=http://localhost:$PORT/api)
#          exercise a fix WITHOUT deploying to live master (which the live gw serves, ro + no reload).
#   down : remove it.
#
# ⚠️ HAZARD (WRITTEN, do not remove): the test gateway connects to the ONE LIVE milkydb — there is
# no separate test DB. run_all.sh RESTORES/DRIVES milkydb. Safe ONLY pre-launch (no real tenants).
# After early adopters exist this is DESTRUCTIVE. GUARD below refuses to start unless milkydb holds
# at most the harness tenant.
# =============================================================================
set -uo pipefail
CMD=${1:-up}
NAME=mh-test-gw
PORT=${PORT:-8002}
LIVE=milkyhoop-dev-api_gateway
NET=milkyhoop_dev_network
PG=milkyhoop-dev-postgres-1
HARNESS_SLUG=${HARNESS_SLUG:-kaos-biru-konveksi}
WORKTREE="$(cd "$(dirname "$0")/../.." && pwd)"   # scripts/e2e/test_gateway.sh -> repo root

if [ "$CMD" = "down" ]; then
  docker rm -f "$NAME" >/dev/null 2>&1 && echo "$NAME removed" || echo "$NAME not running"; exit 0
fi
[ "$CMD" = "up" ] || { echo "usage: test_gateway.sh [up|down]"; exit 2; }

# --- GUARD: refuse if milkydb has any non-harness / >1 tenant (test gw writes to LIVE milkydb) ---
Q(){ docker exec -i "$PG" psql -U postgres -d milkydb -tAc "$1" | tr -d '[:space:]'; }
TN=$(Q "SELECT count(*) FROM \"Tenant\";" 2>/dev/null || echo ERR)
BAD=$(Q "SELECT count(*) FROM \"Tenant\" WHERE id <> '$HARNESS_SLUG';" 2>/dev/null || echo ERR)
if [ "$TN" = "ERR" ] || [ "$BAD" = "ERR" ]; then echo "!!! cannot read milkydb tenants — REFUSING"; exit 1; fi
if [ "${TN:-0}" -gt 1 ] || [ "${BAD:-0}" -gt 0 ]; then
  echo "!!! REFUSING: milkydb has $TN tenant(s), $BAD outside harness slug '$HARNESS_SLUG'."
  echo "    The test gateway writes to LIVE milkydb — unsafe with real/early-adopter data."
  echo "    Restore preharness (0 tenants) or provision only the harness tenant, then retry."
  exit 1
fi

IMG=$(docker inspect "$LIVE" --format '{{.Config.Image}}')
docker inspect "$LIVE" --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -vE '^PATH=' > /tmp/mh_test_gw.env
docker rm -f "$NAME" >/dev/null 2>&1
docker run -d --name "$NAME" --network "$NET" --env-file /tmp/mh_test_gw.env \
  -v "$WORKTREE/backend/api_gateway:/app/backend/api_gateway:ro" \
  -v "$WORKTREE/backend/services/accounting_kernel:/app/backend/services/accounting_kernel:ro" \
  -v /root/milkyhoop-dev/data/logos:/app/backend/api_gateway/app/static/logos:rw \
  -p "$PORT:8000" "$IMG" >/dev/null
for i in $(seq 1 30); do
  c=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:$PORT/api/health" 2>/dev/null)
  if [ "$c" = "200" ] || [ "$c" = "401" ]; then
    # Pre-install pdfminer.six so pdf_text.sh can extract rendered PDF text fast (no per-call pip).
    docker exec "$NAME" python -m pip install -q --disable-pip-version-check pdfminer.six >/dev/null 2>&1 \
      && echo "  pdfminer.six ready in $NAME" || echo "  (pdfminer install skipped — pdf_text.sh will fall back)"
    echo "$NAME up on :$PORT ($c), serving $WORKTREE"; exit 0
  fi
  sleep 1
done
echo "!!! $NAME did not become healthy on :$PORT"; docker logs --tail 20 "$NAME" 2>&1; exit 1
