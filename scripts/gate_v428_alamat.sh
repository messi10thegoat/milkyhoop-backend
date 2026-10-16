#!/usr/bin/env bash
# Gerbang V428 (alamat pelanggan terstruktur): DB scratch milkydb_v428 = skema milkydb + baris "Tenant" + pelanggan/SO
# SINTETIS (TIDAK menyalin data pelanggan, TIDAK menulis milkydb). Menerapkan V428, menjalankan harness
# scripts/ops/uji-v428-alamat.py (kode LAMA = pohon utama, kode BARU = pohon ini), ROLLBACK, terapkan ulang, hapus DB.
# Pemakaian: bash scripts/gate_v428_alamat.sh [--tanpa-migrasi]   (--tanpa-migrasi = KONTROL MERAH -> harus GAGAL)
set -uo pipefail
P=milkyhoop-dev-postgres-1; DB=milkydb_v428; G=milkyhoop-dev-api_gateway
AKAR="$(cd "$(dirname "$0")/.." && pwd)"; LAMA=${MH_MAIN:-/root/milkyhoop-dev}
q() { docker exec -i "$P" psql -U postgres "$@"; }
q -d postgres -qc "DROP DATABASE IF EXISTS $DB" -c "CREATE DATABASE $DB" || exit 2
docker exec "$P" sh -c "pg_dump -U postgres -s milkydb | psql -U postgres -d $DB -q -o /dev/null" 2>/dev/null
docker exec "$P" sh -c "pg_dump -U postgres --data-only -t 'public.\"Tenant\"' milkydb | psql -U postgres -d $DB -q -o /dev/null" || exit 2
q -d "$DB" -v ON_ERROR_STOP=1 -q < "$AKAR/scripts/ops/uji-v428-benih.sql" || { echo "benih GAGAL"; exit 2; }
q -d "$DB" -At -c "SELECT id, alamat, city, province, postal_code FROM customers ORDER BY id" > /tmp/v428_datar_sebelum.txt
if [ "${1:-}" != "--tanpa-migrasi" ]; then
  q -d "$DB" -v ON_ERROR_STOP=1 -q -1 < "$AKAR/backend/migrations/V428__customer_addresses.sql" || { echo "migrasi GAGAL"; exit 2; }
fi
E=$(mktemp /root/.v428-env-XXXX); chmod 600 "$E"
docker exec "$G" printenv DATABASE_URL | sed -E "s#/[A-Za-z0-9_]+(\?|$)#/$DB\1#; s/^/DATABASE_URL=/" > "$E"
IMG=$(docker inspect "$G" --format '{{.Config.Image}}')
jalan() { docker run --rm --network milkyhoop_dev_network --env-file "$E" -v "$1/backend:/app/backend:ro" \
  -v "$AKAR/scripts/ops/uji-v428-alamat.py:/h.py:ro" -v /tmp:/out --entrypoint python "$IMG" -I /h.py "$2"; }
jalan "$LAMA" lama > /tmp/v428_lama.log 2>&1; rcl=$?
jalan "$AKAR" baru > /tmp/v428_baru.log 2>&1; rc=$?
rm -f "$E"
grep -E "^(PASS|FAIL)|RINGKAS|Traceback|Error" /tmp/v428_lama.log | head -5
grep -E "^(PASS|FAIL)|RINGKAS|Traceback|Error" /tmp/v428_baru.log
if [ "${1:-}" != "--tanpa-migrasi" ]; then
  q -d "$DB" -At -c "SELECT id, alamat, city, province, postal_code FROM customers ORDER BY id" > /tmp/v428_datar_pra_rb.txt
  q -d "$DB" -v ON_ERROR_STOP=1 -q -1 < "$AKAR/backend/migrations/V428__customer_addresses_ROLLBACK.sql" || { echo "FAIL rollback"; rc=1; }
  q -d "$DB" -At -c "SELECT id, alamat, city, province, postal_code FROM customers ORDER BY id" > /tmp/v428_datar_pasca_rb.txt
  if cmp -s /tmp/v428_datar_pra_rb.txt /tmp/v428_datar_pasca_rb.txt && [ "$(q -d "$DB" -At -c "SELECT to_regclass('customer_addresses') IS NULL")" = t ]; then
    echo "PASS rollback: tabel hilang, kolom datar pelanggan tak berubah"; else echo "FAIL rollback"; rc=1; fi
  q -d "$DB" -v ON_ERROR_STOP=1 -q -1 < "$AKAR/backend/migrations/V428__customer_addresses.sql" 2>&1 | grep -v NOTICE \
    && echo "FAIL terapkan ulang" || echo "PASS terapkan ulang sesudah rollback"
fi
q -d postgres -qc "DROP DATABASE IF EXISTS $DB"
rm -f /tmp/v428_*.txt
[ $rc = 0 ] && [ $rcl = 0 ] && grep -q "RINGKAS gagal=0" /tmp/v428_baru.log && { echo "LULUS"; exit 0; }
echo "GAGAL"; exit 1
