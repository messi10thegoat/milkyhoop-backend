#!/bin/bash
# Uji Check 22 (proforma terbit bernominal pecahan rupiah) di monitoring/accounting_health_check.sh -- dijalankan di SERVER BE.
# (a) nyata read-only semua tenant, (b) kontrol MERAH: satu proforma terbit kaos dibuat pecahan di BEGIN..ROLLBACK,
# (c) logika dgn keluaran tiruan (0 / 2 / __GAGAL__ / galat). Tidak menulis DB (b selalu ROLLBACK; cek nol jejak dicetak).
F="$(cd "$(dirname "$0")/../.." && pwd)/monitoring/accounting_health_check.sh"
LOG_FILE=/tmp/hc22.log; : > $LOG_FILE
CONTAINER=milkyhoop-dev-postgres-1
detail() { echo "  $1" >> "$LOG_FILE"; }
sed -n '/^check_22_proforma_pecahan() {/,/^}/p' $F > /tmp/c22.sh
bash -n /tmp/c22.sh && echo "potongan sintaks OK ($(wc -l < /tmp/c22.sh) baris)"
. /tmp/c22.sh
psql_nyata() { docker exec -e PGOPTIONS='-c default_transaction_read_only=on' $CONTAINER psql -U postgres -d milkydb -v ON_ERROR_STOP=1 -t -c "$1" 2>/dev/null | sed 's/^ *//;s/ *$//' | grep -v '^$'; }
echo "== (a) NYATA read-only, semua tenant"
psql_cmd() { psql_nyata "$1"; }
for T in $(docker exec $CONTAINER psql -U postgres -d milkydb -At -c 'SELECT id FROM "Tenant" ORDER BY 1'); do
  check_22_proforma_pecahan "$T"; echo "$T: CHK_PASS=$CHK_PASS DETAIL=[$CHK_DETAIL]"
done
echo "== (b) KONTROL MERAH: proforma terbit kaos +0,50 (BEGIN..ROLLBACK)"
h0=$(psql_nyata "SELECT md5(string_agg(id::text||amount::text, ',' ORDER BY id)) FROM proformas WHERE tenant_id='kaos-biru-konveksi'")
psql_cmd() {
  printf "BEGIN;\nUPDATE proformas SET amount = amount + 0.50 WHERE id = (SELECT id FROM proformas WHERE tenant_id='kaos-biru-konveksi' AND status='issued' ORDER BY id LIMIT 1);\n%s\nROLLBACK;\n" "$1" > /tmp/c22k.sql
  docker cp /tmp/c22k.sql $CONTAINER:/tmp/c22k.sql >/dev/null
  docker exec $CONTAINER psql -U postgres -d milkydb -At -f /tmp/c22k.sql | grep -vE '^(BEGIN|ROLLBACK|UPDATE [0-9]+)$' | head -1; }
check_22_proforma_pecahan kaos-biru-konveksi; echo "kaos dgn baris uji: CHK_PASS=$CHK_PASS DETAIL=[$CHK_DETAIL]"
h1=$(psql_nyata "SELECT md5(string_agg(id::text||amount::text, ',' ORDER BY id)) FROM proformas WHERE tenant_id='kaos-biru-konveksi'")
echo "NOL JEJAK: proforma kaos $( [ "$h0" = "$h1" ] && echo identik || echo BERUBAH )"
echo "== (c) logika dgn keluaran tiruan"
for out in 0 2 __GAGAL__ "ERROR: x"; do psql_cmd() { echo "$out"; }; check_22_proforma_pecahan t; echo "keluaran=[$out] -> CHK_PASS=$CHK_PASS DETAIL=[$CHK_DETAIL]"; done
