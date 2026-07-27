#!/bin/bash
# =============================================================================
# step_8_settle.sh — DP-flow STEP 8: pelunasan (final settlement of remaining AR 3.500.000).
# FE PATH (confirmed): Penerimaan Pembayaran -> useReceivePaymentForm.submit ->
# POST /api/receive-payments with save_as_draft:false (single call; backend auto-posts,
# NO separate /post or /confirm). Payload shape mirrors the FE exactly. The shortcut path
# POST /sales-invoices/{id}/payments is NOT used by this screen -> logged as a COVERAGE GAP.
#
# TWO tests:
#  (a) OVERPAYMENT GUARD: request 3.500.001 first -> MUST 400 "exceeds invoice remaining (3500000)".
#      Guard (receive_payments.py:1090-1096) compares alloc vs get_invoice_remaining_from_journal
#      -> compute_ar_outstanding (deposit-aware): proves the cap is 3.500.000 (NOT the 5.000.000
#      gross invoice). Guard runs BEFORE any INSERT, inside the txn -> rolls back, zero artifacts.
#  (b) REAL SETTLE 3.500.000.
#
# EXPECT journal (source_type RECEIVE_PAYMENT): Dr 1-10201 BCA 3.500.000 / Cr 1-10400 Piutang
# 3.500.000, 2 lines, date D_SETTLE (07-20, AFTER last fulfill 07-14).
# 5 ARTIFACTS: journal_entries(POSTED) + receive_payments(posted, journal linked) +
# receive_payment_allocations(remaining_before=3.500.000 <- P35_ARCANON deposit-aware) +
# bank_transactions(+3.500.000) + sales_invoices cache(amount_paid=5.000.000, status=paid).
# AFTER: AR raw==compute==0 drift 0 · bank 21.500.000 (delta +1.500.000 from opening 20M) ·
# BANK_GAP 0 · JE 10->11 · BT 3->4.
# =============================================================================
set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
source "$DIR/state.env"; source "$DIR/dates.env"
H=(-H "Authorization: Bearer $TOK" -H "X-Tenant-Slug: $TEN" -H "Content-Type: application/json")
PSQL(){ docker exec -i "$CONTAINER" psql -U postgres -d "$DB" -tAc "$1" | tr -d '[:space:]'; }
PSQLm(){ docker exec -i "$CONTAINER" psql -U postgres -d "$DB" -c "$1"; }

echo "===== STEP 8 — PELUNASAN (settle remaining AR 3.500.000 via POST /api/receive-payments) ====="
JE_BEFORE=$(PSQL "SELECT count(*) FROM journal_entries WHERE tenant_id='$TEN';")
BT_BEFORE=$(PSQL "SELECT count(*) FROM bank_transactions WHERE tenant_id='$TEN';")
RP_BEFORE=$(PSQL "SELECT count(*) FROM receive_payments WHERE tenant_id='$TEN';")
AR_PRE=$(PSQL "SELECT COALESCE(SUM(outstanding),0)::bigint FROM compute_ar_outstanding('$TEN');")
echo "before: journal_entries=$JE_BEFORE (expect 10) | bank_transactions=$BT_BEFORE | receive_payments=$RP_BEFORE | AR(compute)=$AR_PRE (expect 3500000)"

echo; echo "--- ★ TEST (a): OVERPAYMENT PROBE 3.500.001 -> expect HTTP 400 (cap 3.500.000, deposit-aware) ---"
OVER=$(curl -s -w "\n%{http_code}" -X POST "$B/receive-payments" "${H[@]}" -d "{
  \"customer_id\":\"$CUS\",\"payment_date\":\"$D_SETTLE\",\"payment_method\":\"bank_transfer\",
  \"bank_account_id\":\"$BANK\",\"total_amount\":3500001,
  \"allocations\":[{\"invoice_id\":\"$INVID\",\"amount_applied\":3500001}],
  \"save_as_draft\":false,\"idempotency_key\":\"settle-kb-OVERPROBE\"}")
OCODE=$(echo "$OVER" | tail -1); OBODY=$(echo "$OVER" | sed '$d')
echo "  HTTP=$OCODE  body=$(echo "$OBODY" | head -c 220)"
[ "$OCODE" = "400" ] && echo "  PASS (rejected)" || echo "  !!! expected 400 — guard did not fire as expected"
echo "  no artifacts from probe? JE=$(PSQL "SELECT count(*) FROM journal_entries WHERE tenant_id='$TEN';") BT=$(PSQL "SELECT count(*) FROM bank_transactions WHERE tenant_id='$TEN';") RP=$(PSQL "SELECT count(*) FROM receive_payments WHERE tenant_id='$TEN';") (expect $JE_BEFORE/$BT_BEFORE/$RP_BEFORE — txn rollback)"

echo; echo "--- ★ TEST (b): REAL SETTLE 3.500.000 ---"
if [ "$RP_BEFORE" != "0" ]; then
  echo "  (receive_payments already present — idempotent skip of create)"
else
  RESP=$(curl -s -w "\n%{http_code}" -X POST "$B/receive-payments" "${H[@]}" -d "{
    \"customer_id\":\"$CUS\",\"payment_date\":\"$D_SETTLE\",\"payment_method\":\"bank_transfer\",
    \"bank_account_id\":\"$BANK\",\"total_amount\":3500000,
    \"allocations\":[{\"invoice_id\":\"$INVID\",\"amount_applied\":3500000}],
    \"reference_number\":\"TRF-SETTLE-KB\",\"notes\":\"Pelunasan sisa piutang\",
    \"save_as_draft\":false,\"idempotency_key\":\"settle-kb-3500000\"}")
  RCODE=$(echo "$RESP" | tail -1); RBODY=$(echo "$RESP" | sed '$d')
  echo "  HTTP=$RCODE"
  echo "  ★ remaining_before (expect 3500000 = P35_ARCANON deposit-aware, NOT 5000000):"
  echo "$RBODY" | python3 -c "import sys,json
d=json.load(sys.stdin); r=d.get('data') or d
al=r.get('allocations') or []
for a in al: print('     alloc invoice=%s applied=%s remaining_before=%s remaining_after=%s'%(a.get('invoice_id'),a.get('amount_applied'),a.get('remaining_before'),a.get('remaining_after')))
print('     payment_number=%s status=%s accounting_status=%s'%(r.get('payment_number'),r.get('status'),r.get('accounting_status')))" 2>&1 | head
fi

echo; echo "--- ★ JOURNAL (RECEIVE_PAYMENT: Dr 1-10201 BCA 3.5M / Cr 1-10400 Piutang 3.5M) ---"
PSQLm "SELECT je.source_type, je.journal_date, je.status, c.account_code, c.account_type, jl.debit::bigint dr, jl.credit::bigint cr
       FROM journal_entries je JOIN journal_lines jl ON jl.journal_id=je.id JOIN chart_of_accounts c ON c.id=jl.account_id
       WHERE je.tenant_id='$TEN' AND je.source_type='RECEIVE_PAYMENT' ORDER BY jl.line_number;"

echo "--- ★★ AR (raw RECEIVABLE ledger == compute_ar == 0) ---"
AR_LEDGER=$(PSQL "SELECT COALESCE(SUM(jl.debit-jl.credit),0)::bigint FROM journal_lines jl JOIN journal_entries je ON je.id=jl.journal_id JOIN chart_of_accounts c ON c.id=jl.account_id WHERE je.tenant_id='$TEN' AND je.status='POSTED' AND je.reversed_by_id IS NULL AND c.account_type='RECEIVABLE';")
AR_COMPUTE=$(PSQL "SELECT COALESCE(SUM(outstanding),0)::bigint FROM compute_ar_outstanding('$TEN');")
echo "  raw RECEIVABLE=$AR_LEDGER | compute_ar=$AR_COMPUTE | $([ "$AR_LEDGER" = "0" ] && [ "$AR_COMPUTE" = "0" ] && echo 'PASS (AR fully settled, drift 0)' || echo 'FAIL')"

echo "--- 5 ARTIFACTS ---"
echo "  1 journal_entries RECEIVE_PAYMENT POSTED: $(PSQL "SELECT count(*)||' status='||COALESCE(max(status),'-') FROM journal_entries WHERE tenant_id='$TEN' AND source_type='RECEIVE_PAYMENT';")"
echo "  2 receive_payments (posted, journal linked): $(PSQL "SELECT count(*) FROM receive_payments WHERE tenant_id='$TEN';") | $(PSQLm "SELECT status, total_amount::bigint, payment_date FROM receive_payments WHERE tenant_id='$TEN';" | sed -n '3p' | tr -s ' ')"
echo "  3 receive_payment_allocations (amount, remaining_before): $(PSQL "SELECT count(*) FROM receive_payment_allocations rpa JOIN receive_payments rp ON rp.id=rpa.payment_id WHERE rp.tenant_id='$TEN';")"
PSQLm "SELECT rpa.amount_applied::bigint applied, rpa.remaining_before::bigint rem_before, rpa.remaining_after::bigint rem_after FROM receive_payment_allocations rpa JOIN receive_payments rp ON rp.id=rpa.payment_id WHERE rp.tenant_id='$TEN';"
echo "  4 bank_transactions (+3.5M, POSTED): $(PSQL "SELECT count(*) FROM bank_transactions WHERE tenant_id='$TEN';") total | newest: $(PSQL "SELECT amount::bigint||' '||status||' '||transaction_date FROM bank_transactions WHERE tenant_id='$TEN' ORDER BY created_at DESC LIMIT 1;")"
echo "  5 sales_invoices cache (expect amount_paid=5000000 status=paid): $(PSQL "SELECT amount_paid::bigint||' / '||status FROM sales_invoices WHERE id='$INVID';")"

echo; echo "--- BANK (expect 21.500.000 = 20M opening -3.5M billpay +1.5M DP +3.5M settle; delta +1.5M) ---"
echo "  1-10201 ledger balance = $(PSQL "SELECT COALESCE(SUM(jl.debit-jl.credit),0)::bigint FROM journal_lines jl JOIN journal_entries je ON je.id=jl.journal_id JOIN chart_of_accounts c ON c.id=jl.account_id WHERE je.tenant_id='$TEN' AND je.status='POSTED' AND je.reversed_by_id IS NULL AND c.account_code='1-10201';")"

JE_AFTER=$(PSQL "SELECT count(*) FROM journal_entries WHERE tenant_id='$TEN';")
BT_AFTER=$(PSQL "SELECT count(*) FROM bank_transactions WHERE tenant_id='$TEN';")
echo "  journal_entries: $JE_BEFORE -> $JE_AFTER (expect 11) | bank_transactions: $BT_BEFORE -> $BT_AFTER (expect 4)"

echo; echo "===== DRIFT + BANK GAP (AR 0, bank 21.5M, gap 0) ====="
docker exec -i "$CONTAINER" psql -U postgres -d "$DB" -v ten="'$TEN'" -f - < "$DIR/drift_check.sql"
