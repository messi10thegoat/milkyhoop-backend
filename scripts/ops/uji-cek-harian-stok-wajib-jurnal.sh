#!/bin/bash
# Uji Check 21 (kartu stok wajib-jurnal) di monitoring/accounting_health_check.sh -- dijalankan di SERVER BE (butuh docker + kontainer postgres).
# (a) nyata read-only semua tenant, (b1) kontrol MERAH: baris OB tanpa jurnal dalam BEGIN..ROLLBACK, (b2) tanpa daftar kecualian,
# (c) logika dgn keluaran tiruan (0 / 3 / __GAGAL__ / galat). Tidak menulis DB (b1 selalu ROLLBACK; cek nol jejak dicetak).
F="$(cd "$(dirname "$0")/../.." && pwd)/monitoring/accounting_health_check.sh"
LOG_FILE=/tmp/hc21.log; : > $LOG_FILE
CONTAINER=milkyhoop-dev-postgres-1
detail() { echo "  $1" >> "$LOG_FILE"; }
# potong blok konfigurasi + fungsi persis seperti di berkas
sed -n '/^STOK_WAJIB_JURNAL=/,/^check_12_negative_balance() {/p' $F | sed '$d' > /tmp/c21.sh
bash -n /tmp/c21.sh && echo "potongan sintaks OK ($(wc -l < /tmp/c21.sh) baris)"
. /tmp/c21.sh
psql_nyata() { docker exec -e PGOPTIONS='-c default_transaction_read_only=on' $CONTAINER psql -U postgres -d milkydb -v ON_ERROR_STOP=1 -t -c "$1" 2>/dev/null | tr -d ' '; }
echo "== (a) NYATA read-only, semua tenant"
psql_cmd() { psql_nyata "$1"; }
for T in $(docker exec $CONTAINER psql -U postgres -d milkydb -At -c 'SELECT id FROM "Tenant" ORDER BY 1'); do
  check_21_stok_wajib_jurnal "$T"; echo "$T: CHK_PASS=$CHK_PASS DETAIL=[$CHK_DETAIL]"
done
echo "-- log detail (kecualian terlihat):"; cat $LOG_FILE
echo "== (b1) KONTROL MERAH: sisipkan baris OB tanpa jurnal (BEGIN..ROLLBACK), kaos"
n0=$(psql_nyata "SELECT count(*) FROM inventory_ledger WHERE tenant_id='kaos-biru-konveksi'")
psql_cmd() {  # tangkap SQL pertama (hitungan utama) lalu jalankan di dalam transaksi + baris uji
  if [ -z "$CAPT" ]; then CAPT="$1"; printf "BEGIN;\nINSERT INTO inventory_ledger (id,tenant_id,product_id,product_code,product_name,movement_type,movement_date,source_type,source_id,source_number,quantity_in,quantity_out,quantity_balance,unit_cost,total_cost,average_cost,warehouse_id,notes,created_at) SELECT gen_random_uuid(),tenant_id,product_id,product_code,'UJI-MERAH',movement_type,movement_date,'OPENING_BALANCE',gen_random_uuid(),'UJI',quantity_in,0,quantity_in,unit_cost,total_cost,average_cost,warehouse_id,'uji merah',now() FROM inventory_ledger WHERE source_type='OPENING_BALANCE' LIMIT 1;\n%s\nROLLBACK;\n" "$1" > /tmp/c21k.sql
    docker cp /tmp/c21k.sql $CONTAINER:/tmp/c21k.sql >/dev/null; docker exec $CONTAINER psql -U postgres -d milkydb -At -f /tmp/c21k.sql | grep -E '^[0-9]+$' | head -1
  else echo 0; fi; }
CAPT=""; check_21_stok_wajib_jurnal kaos-biru-konveksi; echo "kaos dgn baris uji: CHK_PASS=$CHK_PASS DETAIL=[$CHK_DETAIL]"
n1=$(psql_nyata "SELECT count(*) FROM inventory_ledger WHERE tenant_id='kaos-biru-konveksi'"); echo "NOL JEJAK: ledger kaos $n0 -> $n1 ($( [ "$n0" = "$n1" ] && echo ya || echo TIDAK))"
echo "== (b2) tanpa daftar kecualian -> baris kaos yang dikenal HARUS merah (kecualian = satu-satunya penyebab hijau)"
psql_cmd() { psql_nyata "$1"; }
STOK_TANPA_JURNAL_DIKECUALIKAN=(); check_21_stok_wajib_jurnal kaos-biru-konveksi; echo "kaos tanpa kecualian: CHK_PASS=$CHK_PASS DETAIL=[$CHK_DETAIL]"
echo "== (c) logika dgn keluaran tiruan"
for out in 0 3 __GAGAL__ "ERROR: x"; do psql_cmd() { echo "$out"; }; check_21_stok_wajib_jurnal t; echo "keluaran=[$out] -> CHK_PASS=$CHK_PASS DETAIL=[$CHK_DETAIL]"; done
