#!/bin/bash
# =============================================================================
# step_0b_pay.sh — DP-flow STEP 0b: pay the supplier bill in full via bank.
# Endpoint: POST /api/bill-payments  (== FE PaymentOut "Pembayaran Keluar" path,
# verified: BillPaymentsResource.basePath = /api/bill-payments). ARAP Rule 11:
# unified onto bill_payments_v2 + allocations.
# Expected journal: Dr 2-10100 Hutang Usaha 3.500.000 / Cr 1-10201 BCA 3.500.000 (no PPN).
# FIRST SETTLEMENT -> verifies the ARAP Rule 1 five-artifact contract in one go.
# Idempotent: skips if a v2 payment already allocates this bill.
# =============================================================================
set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
source "$DIR/state.env"
H=(-H "Authorization: Bearer $TOK" -H "X-Tenant-Slug: $TEN" -H "Content-Type: application/json")
J(){ local m=$1 p=$2 d=${3:-'{}'}; curl -s -X "$m" "$B$p" "${H[@]}" -d "$d"; }
gid(){ python3 -c "import sys,json;d=json.load(sys.stdin);print((d.get('data') or d).get('id',''))" 2>/dev/null; }
PSQL(){ docker exec -i "$CONTAINER" psql -U postgres -d "$DB" -tAc "$1" | tr -d '[:space:]'; }
PSQLm(){ docker exec -i "$CONTAINER" psql -U postgres -d "$DB" -c "$1"; }

echo "===== STEP 0b — PAY SUPPLIER 3.500.000 via bank ====="
PAYID=$(PSQL "SELECT bpv.id FROM bill_payments_v2 bpv JOIN bill_payment_allocations a ON a.payment_id=bpv.id WHERE bpv.tenant_id='$TEN' AND a.bill_id='$BILL' LIMIT 1;")
if [ -n "$PAYID" ]; then
  echo "payment exists (idempotent reuse): $PAYID"
else
  RESP=$(J POST /bill-payments "{\"vendor_id\":\"$VND\",\"payment_date\":\"2026-07-06\",\"payment_method\":\"bank_transfer\",\"bank_account_id\":\"$BANK\",\"total_amount\":3500000,\"reference_number\":\"PAY-KB-01\",\"allocations\":[{\"bill_id\":\"$BILL\",\"amount_applied\":3500000}]}")
  echo "resp: $(echo "$RESP" | head -c 160)"
  PAYID=$(echo "$RESP" | gid)
  [ -z "$PAYID" ] && { echo "!!! payment create failed — ABORT"; exit 1; }
  echo "PAYID=$PAYID"
fi
grep -q '^export PAYID=' "$DIR/state.env" && sed -i "s|^export PAYID=.*|export PAYID=\"$PAYID\"|" "$DIR/state.env" || echo "export PAYID=\"$PAYID\"" >> "$DIR/state.env"

echo; echo "========== 5-ARTIFACT CONTRACT (ARAP Rule 1) =========="
echo "--- [1] journal_entries (source_type/source_id, POSTED, effective) ---"
PSQLm "SELECT source_type, status, (source_id::text='$PAYID') AS src_matches_payment, reversed_by_id IS NULL AS effective
       FROM journal_entries WHERE id=(SELECT journal_id FROM bill_payments_v2 WHERE id='$PAYID');"

echo "--- journal LINES (expect Dr 2-10100 3.5M / Cr 1-10201 3.5M) ---"
PSQLm "SELECT c.account_code, LEFT(c.name,26) akun, c.account_type, jl.debit, jl.credit
       FROM journal_lines jl JOIN chart_of_accounts c ON c.id=jl.account_id
       WHERE jl.journal_id=(SELECT journal_id FROM bill_payments_v2 WHERE id='$PAYID')
       ORDER BY jl.line_number;"

echo "--- [2] bill_payments_v2 wrapper (journal_id + bank_transaction_id linked) ---"
PSQLm "SELECT total_amount, journal_id IS NOT NULL AS has_journal, bank_transaction_id IS NOT NULL AS has_bank_txn, status, accounting_status
       FROM bill_payments_v2 WHERE id='$PAYID';"

echo "--- [3] bill_payment_allocations (remaining_before MUST=3.500.000 -> empirical proof of live get_bill_remaining_from_journal) ---"
PSQLm "SELECT bill_id='$BILL' AS bill_matches, amount_applied, remaining_before, remaining_after
       FROM bill_payment_allocations WHERE payment_id='$PAYID';"

echo "--- [4] bank_transactions (journal linked, amount, direction, running_balance -> 16.5M) ---"
PSQLm "SELECT transaction_type, amount, journal_id IS NOT NULL AS has_journal, running_balance
       FROM bank_transactions WHERE tenant_id='$TEN' AND bank_account_id='$BANK' ORDER BY transaction_date, created_at;"

echo "--- [5] bills.amount_paid cache (expect 3.500.000) ---"
PSQLm "SELECT amount, amount_paid, (amount-amount_paid) AS remaining FROM bills WHERE id='$BILL';"

echo; echo "========== DRIFT + BANK GAP (AP->0, BANK_GAP=0) =========="
docker exec -i "$CONTAINER" psql -U postgres -d "$DB" -v ten="'$TEN'" -f - < "$DIR/drift_check.sql"

echo; echo "--- bank DELTA (expect -3.500.000 excl opening) ---"
PSQL "SELECT (COALESCE(SUM(jl.debit-jl.credit),0)-20000000)::bigint FROM journal_lines jl JOIN journal_entries je ON je.id=jl.journal_id WHERE je.tenant_id='$TEN' AND je.status='POSTED' AND je.reversed_by_id IS NULL AND jl.account_id='$BANK_COA';"
