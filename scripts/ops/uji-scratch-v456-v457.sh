#!/usr/bin/env bash
# Uji V456 (AR refund NK) + V457 (AP saldo awal + bayar yatim) di DB SCRATCH: klon SKEMA milkydb + DATA tabel yang dibaca compute_ar/ap +
# is_effective_journal (semua tenant). Bukti: GL == subledger per tenant SESUDAH (kaos Δ -> 0), baris tenant lain IDENTIK sebelum/sesudah,
# kontrol merah (anchor rusak -> gagal keras; tanpa tambalan GL != sub di kaos), ROLLBACK memulihkan badan fungsi identik, scratch dihapus.
set -u
P=milkyhoop-dev-postgres-1; S=scratch_v456_$(date +%s); M=${M:-/root/mh-v456/backend/migrations}; D=$(mktemp -d /root/.v456-XXXX)
Q() { docker exec -i $P psql -U postgres -d $S -v ON_ERROR_STOP=1 -At -F'|' "$@"; }
hapus() { docker exec $P psql -U postgres -At -c "DROP DATABASE IF EXISTS $S" >/dev/null; rm -rf $D
          echo "scratch dihapus; sisa DB scratch_v456: $(docker exec $P psql -U postgres -At -c "SELECT count(*) FROM pg_database WHERE datname LIKE 'scratch_v456%'")"; }
trap hapus EXIT
docker exec $P psql -U postgres -At -c "CREATE DATABASE $S" >/dev/null || exit 2
docker exec $P sh -c "pg_dump -U postgres -d milkydb --schema-only --no-owner --no-acl | psql -q -U postgres -d $S" >/dev/null 2>&1
docker exec $P sh -c "pg_dump -U postgres -d milkydb --data-only --no-owner --no-acl --disable-triggers -t '\"Tenant\"' -t chart_of_accounts -t journal_source_types -t journal_entries -t journal_lines -t sales_invoices -t receive_payments -t receive_payment_allocations -t credit_notes -t customer_deposit_applications -t customer_deposits -t bills -t bill_payments_v2 -t bill_payment_allocations -t vendor_credits -t vendor_deposit_applications -t vendors | psql -q -U postgres -d $S" >/dev/null 2>&1
echo "disalin: jurnal=$(Q -c 'SELECT count(*) FROM journal_entries') tagihan=$(Q -c 'SELECT count(*) FROM bills') faktur=$(Q -c 'SELECT count(*) FROM sales_invoices')"
SELISIH="SELECT t.id,
  (SELECT COALESCE(SUM(jl.debit - jl.credit), 0) FROM journal_lines jl JOIN journal_entries je ON je.id = jl.journal_id JOIN chart_of_accounts c ON c.id = jl.account_id
    WHERE je.tenant_id = t.id AND je.status = 'POSTED' AND is_effective_journal(je.id) AND c.account_type = 'RECEIVABLE') - (SELECT COALESCE(SUM(outstanding), 0) FROM compute_ar_outstanding(t.id)),
  (SELECT COALESCE(SUM(jl.credit - jl.debit), 0) FROM journal_lines jl JOIN journal_entries je ON je.id = jl.journal_id JOIN chart_of_accounts c ON c.id = jl.account_id
    WHERE je.tenant_id = t.id AND je.status = 'POSTED' AND is_effective_journal(je.id) AND c.account_type = 'PAYABLE') - (SELECT COALESCE(SUM(outstanding), 0) FROM compute_ap_outstanding(t.id))
  FROM \"Tenant\" t ORDER BY 1"
BARIS="SELECT t.id, 'AR', x.* FROM \"Tenant\" t, compute_ar_outstanding(t.id) x UNION ALL SELECT t.id, 'AP', y.vendor_id::text, y.vendor_name, y.bill_id, y.bill_number, y.bill_date, y.due_date, y.bill_status, y.bill_total, y.paid_amount, y.outstanding FROM \"Tenant\" t, compute_ap_outstanding(t.id) y ORDER BY 1, 2, 3, 5, 6"
echo "--- SEBELUM (tenant|ΔAR|ΔAP) [kontrol merah alat ukur: kaos WAJIB != 0]"; Q -c "$SELISIH"
Q -c "$BARIS" > $D/baris_sebelum.txt
for f in compute_ar_outstanding compute_ap_outstanding; do Q -c "SELECT pg_get_functiondef('$f(text)'::regprocedure)" > $D/def_$f.sql; done
# kontrol merah migrasi: anchor rusak di salinan berkas -> WAJIB gagal keras, fungsi tak berubah
sed "s/AND cn.original_invoice_id IS NULL'/AND cn.original_invoice_id IS NULLX'/" $M/V456__ar_refund_nota_kredit.sql > $D/rusak.sql
if Q -1 < $D/rusak.sql >/dev/null 2>$D/e1; then echo "FAIL MERAH: V456 dgn anchor rusak lolos"; else echo "PASS MERAH: V456 anchor rusak gagal keras ($(grep -o 'V456: anchor[^\"]*' $D/e1 | head -1))"; fi
Q -1 < $M/V456__ar_refund_nota_kredit.sql >/dev/null 2>$D/e && echo "PASS V456 terapan" || { echo "FAIL V456 $(tail -2 $D/e)"; exit 1; }
Q -1 < $M/V457__ap_saldo_awal_dan_bayar_yatim.sql >/dev/null 2>$D/e && echo "PASS V457 terapan" || { echo "FAIL V457 $(tail -2 $D/e)"; exit 1; }
Q -1 < $M/V456__ar_refund_nota_kredit.sql >/dev/null 2>&1 && Q -1 < $M/V457__ap_saldo_awal_dan_bayar_yatim.sql >/dev/null 2>&1 && echo "PASS diulang = dilewati (idempoten)"
echo "--- SESUDAH (tenant|ΔAR|ΔAP) [WAJIB semua 0]"; Q -c "$SELISIH" | tee $D/sesudah.txt
awk -F'|' '$2+0 != 0 || $3+0 != 0 {bad=1} END {print (bad ? "FAIL GL != sub sesudah" : "PASS GL == sub SEMUA tenant")}' $D/sesudah.txt
Q -c "$BARIS" > $D/baris_sesudah.txt
echo "--- baris berubah (WAJIB hanya baris sintetis kaos):"; diff $D/baris_sebelum.txt $D/baris_sesudah.txt | grep -E "^[<>]" | sed 's/^/    /'
echo "baris tenant selain kaos berubah: $(diff <(grep -v '^kaos-biru-konveksi' $D/baris_sebelum.txt) <(grep -v '^kaos-biru-konveksi' $D/baris_sesudah.txt) | grep -c '^[<>]')"
Q -1 < $M/V457__ap_saldo_awal_dan_bayar_yatim_ROLLBACK.sql >/dev/null 2>$D/e && Q -1 < $M/V456__ar_refund_nota_kredit_ROLLBACK.sql >/dev/null 2>>$D/e \
  && echo "PASS ROLLBACK" || echo "FAIL ROLLBACK $(tail -2 $D/e)"
for f in compute_ar_outstanding compute_ap_outstanding; do
  Q -c "SELECT pg_get_functiondef('$f(text)'::regprocedure)" | diff -q - $D/def_$f.sql >/dev/null && echo "PASS rollback $f identik" || echo "FAIL rollback $f beda"
done
