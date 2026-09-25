#!/usr/bin/env bash
# Gerbang V309: backfill status faktur ke satu turunan. DB scratch milkydb_v309 = salinan PENUH milkydb
# (skema + data; TIDAK menulis ke milkydb). Mengukur SEBELUM (harus 9 beda) -> terapkan migrasi ->
# SESUDAH (harus 0 beda, 9 baris posted->partial, amount_paid & jurnal tak berubah).
# --kontrol-gagal-keras: migrasi dengan daftar harapan diubah -> harus EXCEPTION dan 0 baris berubah.
set -uo pipefail
P=milkyhoop-dev-postgres-1; DB=milkydb_v309; MIG="$1"; MODE="${2:-}"
q() { docker exec "$P" psql -U postgres "$@"; }
q -d postgres -qc "DROP DATABASE IF EXISTS $DB" -c "CREATE DATABASE $DB" || exit 2
docker exec "$P" sh -c "pg_dump -U postgres milkydb | psql -U postgres -d $DB -q -o /dev/null" 2>/dev/null
BEDA="WITH t AS (SELECT DISTINCT tenant_id FROM sales_invoices WHERE status IN ('posted','partial','paid')),
o AS (SELECT t.tenant_id, x.invoice_id, SUM(x.outstanding) sisa FROM t CROSS JOIN LATERAL compute_ar_outstanding(t.tenant_id) x GROUP BY 1,2)
SELECT count(*) FROM sales_invoices si LEFT JOIN o ON o.tenant_id=si.tenant_id AND o.invoice_id=si.id WHERE si.status IN ('posted','partial','paid')
AND si.status <> CASE WHEN GREATEST(0,COALESCE(o.sisa,0)) < 0.01 THEN 'paid' WHEN si.total_amount-GREATEST(0,COALESCE(o.sisa,0)) > 0.005 THEN 'partial' ELSE 'posted' END"
SIDIK="SELECT md5(string_agg(id::text||':'||coalesce(amount_paid::text,'')||':'||status, ',' ORDER BY id)) FROM sales_invoices"
JURNAL="SELECT count(*)||'/'||md5(string_agg(id::text||status, ',' ORDER BY id)) FROM journal_entries"
b0=$(q -d $DB -Atc "$BEDA"); j0=$(q -d $DB -Atc "$JURNAL"); s0=$(q -d $DB -Atc "SELECT md5(string_agg(id::text||':'||coalesce(amount_paid::text,''), ',' ORDER BY id)) FROM sales_invoices")
echo "SEBELUM: beda=$b0"
docker cp "$MIG" "$P:/tmp/v309_mig.sql"
if [ "$MODE" = "--kontrol-gagal-keras" ]; then
  docker exec "$P" sed -i "s#'kaos-biru-konveksi/INV-2609-0078'#'kaos-biru-konveksi/INV-2609-9999'#" /tmp/v309_mig.sql
fi
q -d $DB -v ON_ERROR_STOP=1 -q -1 -f /tmp/v309_mig.sql > /tmp/gate_v309.log 2>&1; mrc=$?
grep -E "V309" /tmp/gate_v309.log | sed 's/^psql:[^:]*:[0-9]*: //'
b1=$(q -d $DB -Atc "$BEDA"); j1=$(q -d $DB -Atc "$JURNAL"); s1=$(q -d $DB -Atc "SELECT md5(string_agg(id::text||':'||coalesce(amount_paid::text,''), ',' ORDER BY id)) FROM sales_invoices")
echo "SESUDAH: migrasi rc=$mrc beda=$b1 jurnal_sama=$([ "$j0" = "$j1" ] && echo ya || echo TIDAK) amount_paid_sama=$([ "$s0" = "$s1" ] && echo ya || echo TIDAK)"
q -d postgres -qc "DROP DATABASE IF EXISTS $DB"
if [ "$MODE" = "--kontrol-gagal-keras" ]; then
  [ $mrc -ne 0 ] && [ "$b1" = "$b0" ] && { echo "LULUS kontrol: migrasi MENOLAK & 0 baris berubah"; exit 0; }
  echo "GAGAL kontrol: migrasi tak menolak"; exit 1
fi
[ $mrc -eq 0 ] && [ "$b0" = "9" ] && [ "$b1" = "0" ] && [ "$j0" = "$j1" ] && [ "$s0" = "$s1" ] && { echo "LULUS: V309"; exit 0; }
echo "GAGAL: V309"; exit 1
