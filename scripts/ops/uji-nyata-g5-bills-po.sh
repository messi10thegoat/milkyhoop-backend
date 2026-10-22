#!/usr/bin/env bash
# Pelari G5 (bill hapus/void -> qty tertagih PO pulih): DB SALINAN PENUH milkydb_g5 (pg_dump milkydb) -> harness handler nyata -> DB salinan dihapus.
# Produksi TIDAK disentuh (baca dump saja). DSN diturunkan dari env gateway lewat berkas 600 (tak dicetak). MERAH=1 = kontrol merah.
set -uo pipefail
AKAR="$(cd "$(dirname "$0")/../.." && pwd)"; P=milkyhoop-dev-postgres-1; G=milkyhoop-dev-api_gateway; DB=milkydb_g5
q() { docker exec -i "$P" psql -U postgres "$@"; }
q -d postgres -qc "DROP DATABASE IF EXISTS $DB" -c "CREATE DATABASE $DB" >/dev/null 2>&1 || { echo "GAGAL buat DB salinan"; exit 2; }
trap 'q -d postgres -qc "DROP DATABASE IF EXISTS $DB" >/dev/null 2>&1; rm -f "${E:-/nonexistent}"' EXIT
docker exec "$P" sh -c "pg_dump -U postgres milkydb | psql -U postgres -d $DB -q -o /dev/null" 2>/dev/null
echo "salinan: $(q -d $DB -At -c "SELECT count(*) FROM bills")" tagihan
E=$(mktemp /root/.g5-env-XXXX); chmod 600 "$E"
docker exec "$G" printenv DATABASE_URL | sed -E "s#/milkydb(\?|$)#/$DB\1#; s/^/DATABASE_URL=/" > "$E"
grep -q "/$DB" "$E" || { echo "GAGAL: DSN tak menunjuk salinan -- BATAL (jangan sentuh produksi)"; exit 2; }
IMG=$(docker inspect "$G" --format '{{.Config.Image}}')
docker run --rm --network milkyhoop_dev_network --env-file "$E" -e MERAH="${MERAH:-0}" -v "$AKAR/backend:/app/backend:ro" \
  -v "$AKAR/scripts/ops/uji-nyata-g5-bills-po.py:/h.py:ro" -w /app/backend/api_gateway --entrypoint python "$IMG" -I /h.py 2>&1 \
  | grep -v -E "^(INFO|DEBUG|WARNING)"
