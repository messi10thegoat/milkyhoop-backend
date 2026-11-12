#!/usr/bin/env bash
# BUG-185 uji TANPA DB di kontainer sekali-pakai (handler 422 bersama + rute berbentuk lampiran biaya). rc= dari skrip uji. Pakai: POHON=/root/<worktree> scripts/ops/uji-validasi422.sh
set -u
D="$(cd "$(dirname "$0")" && pwd)"
O=$(mktemp)
timeout 300 docker run --rm --network milkyhoop_dev_network --env-file /root/milkyhoop-dev/.env -v "${POHON:-/root/mh-v422}/backend:/app/backend:ro" -v "$D/uji-validasi422.py:/uji.py:ro" \
  -w /app/backend/api_gateway --entrypoint python milkyhoop-dev-api_gateway:latest /uji.py > "$O" 2>&1; RC=$?
grep -vE "^\s*$|warn|Warn|INFO|notice" "$O" | tail -40
rm -f "$O"
echo "rc=$RC"
exit $RC
