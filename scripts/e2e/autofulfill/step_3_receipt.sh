#!/bin/bash
# =============================================================================
# step_3_receipt.sh — SKENARIO #2 langkah 3: pelunasan PENUH 5.000.000.
#
# Bedanya dari skenario #1: TANPA DP, jadi yang ditagih adalah AR PENUH
# 5.000.000 — bukan sisa 3.500.000. Batas overpayment karenanya juga bergeser,
# dan itu diuji: 5.000.001 harus ditolak.
#
# EXPECT jurnal (RECEIVE_PAYMENT): Dr 1-10201 BCA 5.000.000 / Cr 1-10400 Piutang 5.000.000
# Bank sesudahnya: 20.000.000 opening - 3.500.000 bayar supplier + 5.000.000 = 21.500.000
# (delta +1.500.000 dari opening — sama seperti #1, lewat jalur berbeda).
#
# LANGKAH 2 (Pengiriman) SENGAJA TIDAK ADA: faktur sudah ter-fulfill saat posting.
# =============================================================================
set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
DP="$DIR/../dp_flow"
source "$DP/state.env"; source "$DP/dates.env"; source "$DP/verdict.sh"
H=(-H "Authorization: Bearer $TOK" -H "X-Tenant-Slug: $TEN" -H "Content-Type: application/json")
PSQL(){ docker exec -i "$CONTAINER" psql -U postgres -d "$DB" -tAc "$1" | tr -d '[:space:]'; }
PSQLm(){ docker exec -i "$CONTAINER" psql -U postgres -d "$DB" -c "$1"; }
E_SETTLE=${E_SETTLE:-5000000}
E_BANK=${E_BANK:-21500000}

echo "===== SKENARIO #2 STEP 3 — PELUNASAN PENUH $E_SETTLE (tanpa DP) ====="
JE_BEFORE=$(PSQL "SELECT count(*) FROM journal_entries WHERE tenant_id='$TEN';")
RP_BEFORE=$(PSQL "SELECT count(*) FROM receive_payments WHERE tenant_id='$TEN';")
AR_PRE=$(PSQL "SELECT COALESCE(SUM(outstanding),0)::bigint FROM compute_ar_outstanding('$TEN');")
echo "before: journal_entries=$JE_BEFORE receive_payments=$RP_BEFORE AR=$AR_PRE"
aeq "AR sebelum pelunasan = penuh (bukan sisa DP)" "$AR_PRE" "$E_SETTLE"

echo; echo "--- ★ (a) PROBE OVERPAYMENT $((E_SETTLE+1)) -> harus 400 ---"
OVER=$(curl -s -w "\n%{http_code}" -X POST "$B/receive-payments" "${H[@]}" -d "{
  \"customer_id\":\"$CUS\",\"payment_date\":\"$D_SETTLE\",\"payment_method\":\"bank_transfer\",
  \"bank_account_id\":\"$BANK\",\"total_amount\":$((E_SETTLE+1)),
  \"allocations\":[{\"invoice_id\":\"$INVID2\",\"amount_applied\":$((E_SETTLE+1))}],
  \"save_as_draft\":false,\"idempotency_key\":\"af-settle-OVERPROBE\"}")
OCODE=$(echo "$OVER" | tail -1)
echo "  HTTP=$OCODE body=$(echo "$OVER" | sed '$d' | head -c 200)"
aeq "overpayment ditolak" "$OCODE" "400"
aeq "probe nol jurnal baru (rollback)" "$(PSQL "SELECT count(*) FROM journal_entries WHERE tenant_id='$TEN';")" "$JE_BEFORE"
aeq "probe nol receive_payments (rollback)" "$(PSQL "SELECT count(*) FROM receive_payments WHERE tenant_id='$TEN';")" "$RP_BEFORE"

echo; echo "--- ★ (b) PELUNASAN NYATA $E_SETTLE ---"
if [ "$RP_BEFORE" != "0" ]; then
  echo "  (receive_payments sudah ada — idempotent skip)"
else
  RESP=$(curl -s -w "\n%{http_code}" -X POST "$B/receive-payments" "${H[@]}" -d "{
    \"customer_id\":\"$CUS\",\"payment_date\":\"$D_SETTLE\",\"payment_method\":\"bank_transfer\",
    \"bank_account_id\":\"$BANK\",\"total_amount\":$E_SETTLE,
    \"allocations\":[{\"invoice_id\":\"$INVID2\",\"amount_applied\":$E_SETTLE}],
    \"reference_number\":\"TRF-AF-SETTLE\",\"notes\":\"Pelunasan penuh (auto-fulfill)\",
    \"save_as_draft\":false,\"idempotency_key\":\"af-settle-$E_SETTLE\"}")
  echo "  HTTP=$(echo "$RESP" | tail -1)"
fi

echo; echo "--- JURNAL RECEIVE_PAYMENT ---"
PSQLm "SELECT je.source_type, je.journal_date, je.status, c.account_code, c.account_type,
              jl.debit::bigint dr, jl.credit::bigint cr
       FROM journal_entries je JOIN journal_lines jl ON jl.journal_id=je.id
       JOIN chart_of_accounts c ON c.id=jl.account_id
       WHERE je.tenant_id='$TEN' AND je.source_type='RECEIVE_PAYMENT' ORDER BY jl.line_number;"

echo; echo "--- assertions ---"
AR_LEDGER=$(PSQL "SELECT COALESCE(SUM(jl.debit-jl.credit),0)::bigint FROM journal_lines jl JOIN journal_entries je ON je.id=jl.journal_id JOIN chart_of_accounts c ON c.id=jl.account_id WHERE je.tenant_id='$TEN' AND je.status='POSTED' AND je.reversed_by_id IS NULL AND c.account_type='RECEIVABLE';")
AR_COMPUTE=$(PSQL "SELECT COALESCE(SUM(outstanding),0)::bigint FROM compute_ar_outstanding('$TEN');")
BANK_BAL=$(PSQL "SELECT COALESCE(SUM(jl.debit-jl.credit),0)::bigint FROM journal_lines jl JOIN journal_entries je ON je.id=jl.journal_id JOIN chart_of_accounts c ON c.id=jl.account_id WHERE je.tenant_id='$TEN' AND je.status='POSTED' AND je.reversed_by_id IS NULL AND c.account_code='1-10201';")
INVPAID=$(PSQL "SELECT COALESCE(amount_paid,0)::bigint FROM sales_invoices WHERE id='$INVID2';")
INVSTATUS=$(PSQL "SELECT status FROM sales_invoices WHERE id='$INVID2';")
JE_AFTER=$(PSQL "SELECT count(*) FROM journal_entries WHERE tenant_id='$TEN';")
aeq "AR ledger lunas" "$AR_LEDGER" "0"
aeq "compute_ar == ledger (drift 0)" "$AR_COMPUTE" "$AR_LEDGER"
aeq "bank 1-10201 = $E_BANK (delta +1.500.000 dari opening)" "$BANK_BAL" "$E_BANK"
aeq "amount_paid cache" "$INVPAID" "$E_SETTLE"
aeq "status faktur = paid" "$INVSTATUS" "paid"
aeq "journal_entries +1 (RECEIVE_PAYMENT)" "$JE_AFTER" "$((JE_BEFORE+1))"

echo; echo "--- ★ PIN: komposisi jurnal akhir skenario (7 = 1+1+1+3+1) ---"
# Diturunkan dari DESAIN, bukan dari pengamatan:
#   OPENING(step -1) + BILL(0) + BILL_PAYMENT(0b) + INVOICE+FULFILLMENT+REVENUE(1) + RECEIVE_PAYMENT(3)
PSQLm "SELECT source_type, count(*) FROM journal_entries WHERE tenant_id='$TEN' GROUP BY 1 ORDER BY 1;"
for pair in "OPENING:1" "BILL:1" "BILL_PAYMENT:1" "INVOICE:1" "INVOICE_FULFILLMENT:1" "INVOICE_REVENUE:1" "RECEIVE_PAYMENT:1"; do
  st="${pair%%:*}"; want="${pair##*:}"
  got=$(PSQL "SELECT count(*) FROM journal_entries WHERE tenant_id='$TEN' AND source_type='$st';")
  aeq "jurnal $st" "$got" "$want"
done
aeq "TOTAL journal_entries = 7" "$JE_AFTER" "7"

echo; echo "===== DRIFT + BANK GAP ====="
docker exec -i "$CONTAINER" psql -U postgres -d "$DB" -v ten="'$TEN'" -f - < "$DP/drift_check.sql"
finish
