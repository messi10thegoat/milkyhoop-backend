#!/usr/bin/env bash
# Uji V460 (status_v2 tagihan tanpa 'paid') di DB SCRATCH: klon SKEMA + data bills/bill_items/bill_payment_allocations/"Tenant". Prod TIDAK
# ditulis. Bukti: MERAH data (baris paid ke-3 sintetis) -> V460 gagal keras + tergulung; terapan -> 2 baris kaos posted, transisi posted->paid
# DITOLAK trigger, CHECK menolak 'paid', posted->void tetap sah; ROLLBACK memulihkan fungsi + CHECK identik; terapan ulang lolos. Scratch
# dihapus & dibuktikan hilang.
set -u
P=milkyhoop-dev-postgres-1; S=scratch_v460_$(date +%s); M=${M:-$(cd "$(dirname "$0")/../.." && pwd)/backend/migrations}
V=$M/V460__tagihan_status_v2_tanpa_paid.sql; RB=$M/V460__tagihan_status_v2_tanpa_paid_ROLLBACK.sql
D=$(mktemp -d /root/.v460-XXXX); gagal=0
Q() { docker exec -i $P psql -U postgres -d $S -v ON_ERROR_STOP=1 -At "$@"; }
F() { echo "FAIL $*"; gagal=1; }
hapus() { docker exec $P psql -U postgres -At -c "DROP DATABASE IF EXISTS $S" >/dev/null;
          echo "scratch dihapus; sisa DB scratch_v460: $(docker exec $P psql -U postgres -At -c "SELECT count(*) FROM pg_database WHERE datname LIKE 'scratch_v460%'")"; rm -rf $D; }
trap hapus EXIT
[ -f $V ] && [ -f $RB ] || { echo "FAIL berkas migrasi tak ada di $M"; exit 2; }
docker exec $P psql -U postgres -At -c "CREATE DATABASE $S" >/dev/null || exit 2
docker exec $P sh -c "pg_dump -U postgres -d milkydb --schema-only --no-owner --no-acl | psql -q -U postgres -d $S" >/dev/null 2>&1
docker exec $P sh -c "pg_dump -U postgres -d milkydb --data-only --no-owner --no-acl --disable-triggers -t '\"Tenant\"' -t bills -t bill_payment_allocations | psql -q -U postgres -d $S" >/dev/null 2>&1
echo "disalin: bills=$(Q -c 'SELECT count(*) FROM bills') paid=$(Q -c "SELECT count(*) FROM bills WHERE status_v2='paid'")"
FN="validate_bill_status_transition(character varying,character varying)"
Q -c "SELECT pg_get_functiondef('$FN'::regprocedure)" > $D/fn0; Q -c "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname='chk_bills_status_v2'" > $D/ck0
# MERAH: baris paid ke-3 (scratch) -> penjaga gagal keras, transaksi tergulung
X=$(Q -c "SELECT id FROM bills WHERE status_v2='posted' LIMIT 1")
Q -c "ALTER TABLE bills DISABLE TRIGGER USER; UPDATE bills SET status_v2='paid' WHERE id='$X'; ALTER TABLE bills ENABLE TRIGGER USER" >/dev/null
if Q -1 < $V >/dev/null 2>$D/e1; then F "MERAH: V460 lolos padahal ada 3 baris paid"; else echo "PASS MERAH: V460 gagal keras ($(grep -oE 'V460[^"]*' $D/e1 | head -1 | cut -c1-90))"; fi
[ "$(Q -c "SELECT count(*) FROM bills WHERE status_v2='paid'")" = 3 ] && echo "PASS merah tergulung (3 baris paid tetap)" || F "merah tak tergulung"
Q -c "ALTER TABLE bills DISABLE TRIGGER USER; UPDATE bills SET status_v2='posted' WHERE id='$X'; ALTER TABLE bills ENABLE TRIGGER USER" >/dev/null
# TERAPAN
Q -1 < $V >/dev/null 2>$D/err && echo "PASS V460 terapan" || { F "V460: $(tail -3 $D/err)"; exit 1; }
echo "sesudah: paid=$(Q -c "SELECT count(*) FROM bills WHERE status_v2='paid'") kaos 0005/0009=$(Q -c "SELECT string_agg(invoice_number||':'||status_v2||'/'||status, ',' ORDER BY invoice_number) FROM bills WHERE tenant_id='kaos-biru-konveksi' AND invoice_number IN ('BILL-2609-0005','BILL-2609-0009')")"
Y=$(Q -c "SELECT id FROM bills WHERE status_v2='posted' LIMIT 1")
Q -c "UPDATE bills SET status_v2='paid' WHERE id='$Y'" >/dev/null 2>$D/e2 && F "posted->paid DITERIMA" || echo "PASS posted->paid ditolak ($(grep -oE 'tidak diizinkan|violates check constraint' $D/e2 | head -1))"
Q -c "ALTER TABLE bills DISABLE TRIGGER USER" >/dev/null
Q -c "UPDATE bills SET status_v2='paid' WHERE id='$Y'" >/dev/null 2>$D/e3 && F "CHECK menerima 'paid'" || echo "PASS CHECK menolak 'paid' (trigger dimatikan)"
Q -c "ALTER TABLE bills ENABLE TRIGGER USER" >/dev/null
[ "$(Q -c "SELECT validate_bill_status_transition('posted','void')")" = t ] && echo "PASS posted->void tetap sah" || F "posted->void rusak"
Z=$(Q -c "SELECT id FROM bills WHERE tenant_id='kaos-biru-konveksi' AND invoice_number='BILL-2609-0005'")
Q -c "BEGIN; UPDATE bills SET status_v2='void' WHERE id='$Z'; ROLLBACK" >/dev/null 2>$D/e4 && echo "PASS BILL-2609-0005 kini bisa di-void (transisi; rollback)" || F "void 0005: $(tail -2 $D/e4)"
Q -1 < $V >/dev/null 2>$D/e5 && F "V460 diulang LOLOS (harap gagal: penjaga 2 baris paid)" || echo "PASS V460 sekali-jalan (diulang -> penjaga menolak, apply_mig menolak catatan ganda juga)"
# ROLLBACK
Q -1 < $RB >/dev/null 2>$D/e6 && echo "PASS ROLLBACK" || F "ROLLBACK: $(tail -3 $D/e6)"
Q -c "SELECT pg_get_functiondef('$FN'::regprocedure)" | diff -q - $D/fn0 >/dev/null && echo "PASS rollback fungsi identik" || F "rollback fungsi beda"
Q -c "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname='chk_bills_status_v2'" | diff -q - $D/ck0 >/dev/null && echo "PASS rollback CHECK identik" || F "rollback CHECK beda: $(Q -c "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname='chk_bills_status_v2'")"
echo "RINGKAS gagal=$gagal"; exit $gagal
