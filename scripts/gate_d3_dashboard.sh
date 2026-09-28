#!/usr/bin/env bash
# Gerbang D3 dashboard v2 == laporan (READ-ONLY: sesi DB default_transaction_read_only). Kontainer SEKALI-PAKAI,
# worktree :ro — bukan docker exec ke gateway prod. URL DB dibaca dari env kontainer gateway, TIDAK dicetak.
# Pakai: scripts/gate_d3_dashboard.sh <worktree> [tenant ...] [--kontrol-merah]
set -euo pipefail
POHON="${1:?worktree}"; shift
GW=milkyhoop-dev-api_gateway
URL="$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$GW" | sed -n 's/^DATABASE_URL=//p')"
[ -n "$URL" ] || { echo "DATABASE_URL gateway tak terbaca"; exit 2; }
NET="$(docker inspect -f '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' "$GW" | awk '{print $1}')"
exec docker run --rm --network "$NET" -e DATABASE_URL="$URL" \
  -v "$POHON/backend/api_gateway:/app/backend/api_gateway:ro" \
  -v "$POHON/scripts:/app/scripts:ro" -w /app/backend/api_gateway \
  --entrypoint python "$(docker inspect -f '{{.Config.Image}}' "$GW")" \
  /app/scripts/gate_d3_dashboard.py "$@"
