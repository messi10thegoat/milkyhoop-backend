#!/bin/bash
# =============================================================================
# cleanup.sh — SELF-CLEAN: buktikan run ini tak meninggalkan apa pun yang bisa
# membuat gate berikutnya BERBOHONG.
#
# KENAPA BENTUKNYA ASSERT, BUKAN DELETE:
#   Kejadian yang melahirkan aturan ini (red_abc + deact_gate, 8 Agt) adalah gate
#   yang menumpuk fixture di DB bersama sampai gate lain membaca dunia yang sudah
#   bergeser. Obatnya BUKAN "hapus sesuatu di akhir" — menghapus faktur yang sudah
#   POSTED melanggar Law 2 (jurnal posted hanya boleh di-reversal), dan void
#   BELUM PERNAH dieksekusi runtime sehingga di luar scope.
#
#   Mekanisme pembersih yang sesungguhnya adalah PREFLIGHT: tiap run me-restore
#   milkydb ke pristine. Yang belum ada — dan inilah yang ditambahkan berkas ini —
#   adalah BUKTI bahwa keadaan akhir persis seperti yang dijanjikan, tanpa satu
#   pun baris tak terduga. Fixture yang tumbuh diam-diam hanya bisa ketahuan
#   kalau keadaan akhir DIPIN, bukan kalau ada perintah hapus yang mungkin
#   mengenai nol baris (kelas `git add` salah-case: exit 0 tanpa efek).
#
# JADI: berkas ini tak menghapus apa pun. Ia MENGUNCI keadaan akhir.
# =============================================================================
set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
DP="$DIR/../dp_flow"
source "$DP/state.env"; source "$DP/verdict.sh"
PSQL(){ docker exec -i "$CONTAINER" psql -U postgres -d "$DB" -tAc "$1" | tr -d '[:space:]'; }
PSQLm(){ docker exec -i "$CONTAINER" psql -U postgres -d "$DB" -c "$1"; }

echo "===== SELF-CLEAN — keadaan akhir DIPIN, nol kejutan untuk gate berikutnya ====="

echo "--- cakupan global: hanya tenant harness yang boleh ada ---"
PSQLm "SELECT id, name FROM \"Tenant\" ORDER BY id;"
NTEN=$(PSQL "SELECT count(*) FROM \"Tenant\";")
NUSR=$(PSQL "SELECT count(*) FROM \"User\";")
aeq "tepat 1 tenant (run me-restore dari pristine)" "$NTEN" "1"
aeq "tepat 1 user" "$NUSR" "1"
aeq "tenant itu adalah tenant harness" "$(PSQL "SELECT id FROM \"Tenant\" LIMIT 1;")" "$TEN"

echo; echo "--- dokumen: cacah PERSIS, tak boleh ada yang menyelinap ---"
for pair in "sales_invoices:2" "bills:1" "receive_payments:1" "bill_payments_v2:1" \
            "invoice_fulfillments:1" \
            "customer_deposits:0" "credit_notes:0" "sales_orders:0" "quotes:0"; do
  t="${pair%%:*}"; want="${pair##*:}"
  got=$(PSQL "SELECT count(*) FROM $t WHERE tenant_id='$TEN';" 2>/dev/null)
  # 'ERR' kalau kueri gagal: kegagalan ALAT tak boleh menyamar jadi cacah 0 yang kebetulan cocok.
  aeq "$t" "${got:-ERR}" "$want"
done
# invoice_fulfillment_items TIDAK punya kolom tenant_id (schema-first: diperiksa,
# bukan diasumsikan — run kedua gagal di sini persis karena asumsi itu). Cakupan
# tenant-nya lewat induknya.
IFI=$(PSQL "SELECT count(*) FROM invoice_fulfillment_items fi JOIN invoice_fulfillments f ON f.id=fi.fulfillment_id WHERE f.tenant_id='$TEN';" 2>/dev/null)
aeq "invoice_fulfillment_items (lewat induk)" "${IFI:-ERR}" "1"

echo; echo "--- jurnal: komposisi PERSIS (8 = 7 skenario + 1 pagar WAC-0) ---"
PSQLm "SELECT source_type, count(*), min(journal_date) AS dmin, max(journal_date) AS dmax
       FROM journal_entries WHERE tenant_id='$TEN' GROUP BY 1 ORDER BY 3;"
aeq "TOTAL journal_entries" "$(PSQL "SELECT count(*) FROM journal_entries WHERE tenant_id='$TEN';")" "8"
aeq "nol jurnal DRAFT tertinggal" "$(PSQL "SELECT count(*) FROM journal_entries WHERE tenant_id='$TEN' AND status<>'POSTED';")" "0"
aeq "nol jurnal ter-reversal" "$(PSQL "SELECT count(*) FROM journal_entries WHERE tenant_id='$TEN' AND reversed_by_id IS NOT NULL;")" "0"

echo; echo "--- rantai hash utuh sampai baris terakhir ---"
BREAKS=$(PSQL "WITH ch AS (SELECT chain_sequence, previous_hash, LAG(content_hash) OVER (ORDER BY chain_sequence) AS exp FROM journal_entries WHERE tenant_id='$TEN') SELECT count(*) FROM ch WHERE chain_sequence>1 AND previous_hash IS DISTINCT FROM exp;")
SEQGAP=$(PSQL "SELECT (max(chain_sequence)-count(*))::int FROM journal_entries WHERE tenant_id='$TEN';")
aeq "nol putus rantai (Law 20)" "$BREAKS" "0"
aeq "nol lompatan chain_sequence (Law 22)" "$SEQGAP" "0"

echo; echo "--- tenant_config TETAP tanpa baris (kalau bocor, run berikutnya jadi delivery mode) ---"
aeq "tenant_config nol baris" "$(PSQL "SELECT count(*) FROM tenant_config WHERE tenant_id='$TEN';")" "0"

echo; echo "--- yang SENGAJA tersisa, dan alasannya ---"
echo "  · faktur AF-INV-WAC0 (AR 600.000, deferred) — fixture pagar sisi-sehat."
echo "    TIDAK dihapus: jurnalnya sudah POSTED (Law 2), dan void di luar scope."
echo "    Ia DIPIN di atas, jadi pertumbuhannya akan terdeteksi, bukan tersembunyi."
echo "  · pembersihan sesungguhnya = PREFLIGHT restore pada run berikutnya."
AR_END=$(PSQL "SELECT COALESCE(SUM(outstanding),0)::bigint FROM compute_ar_outstanding('$TEN');")
aeq "AR akhir = 600.000 (hanya fixture pagar, tak lebih)" "$AR_END" "600000"
aeq "AP akhir = 0" "$(PSQL "SELECT COALESCE(SUM(outstanding),0)::bigint FROM compute_ap_outstanding('$TEN');")" "0"
finish
