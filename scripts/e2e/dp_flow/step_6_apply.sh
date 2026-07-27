#!/bin/bash
# =============================================================================
# step_6_apply.sh — DP-flow STEP 6: apply the 1.500.000 DP against INV-2607-0001.
# ARAP Flows Checklist #7 (Customer Deposit APPLY). This is the B0 test — the question that
# started the whole workstream: does applying a DP correctly REDUCE AR (Branch 3) rather than
# leave a phantom?
#
# Endpoint: POST /api/customer-deposits/{id}/apply (FE ApplyDepositPanel path).
# TWO FREE TESTS around it:
#  3(a) GET /api/sales-invoices/{id}/applicable-deposits BEFORE apply (FE panel spine path) —
#       our deposit must appear (proves sales_order_id linkage we worked to preserve).
#  3(b) AFTER apply, GET /api/members/{customer} — get_ar_balances_by_customer joins
#       je.source_id = ar.source_id (1-104%). The apply journal's source_id = DEPOSIT id, and no
#       accounts_receivable row carries that source_id -> predicted to MISS the credit -> 5.000.000
#       (BUG) vs compute_ar 3.500.000. Runtime resolves the FASE-1 [INFER].
#
# EXPECT: separate journal Dr 2-10500 1.500.000 / Cr 1-10400 1.500.000, source_type
# DEPOSIT_APPLICATION, 2 lines. ★ Branch 3: raw RECEIVABLE ledger == compute_ar == 3.500.000,
# drift 0. customer_deposit_applications 1 row (active, reversed_by_id NULL, journal_id set),
# sales_invoices.amount_paid=1.5M, status partial. NO new bank_transaction, BANK_GAP=0, bank 18M.
# 2-10500 -> 0. apply date after invoice 07-08. uq_cda_journal_id not violated (first cda row ever).
# =============================================================================
set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
source "$DIR/state.env"; source "$DIR/dates.env"; source "$DIR/verdict.sh"
H=(-H "Authorization: Bearer $TOK" -H "X-Tenant-Slug: $TEN" -H "Content-Type: application/json")
J(){ local m=$1 p=$2 d=${3:-'{}'}; curl -s -X "$m" "$B$p" "${H[@]}" -d "$d"; }
GET(){ curl -s "$B$1" -H "Authorization: Bearer $TOK" -H "X-Tenant-Slug: $TEN"; }
PSQL(){ docker exec -i "$CONTAINER" psql -U postgres -d "$DB" -tAc "$1" | tr -d '[:space:]'; }
PSQLm(){ docker exec -i "$CONTAINER" psql -U postgres -d "$DB" -c "$1"; }

echo "===== STEP 6 — APPLY DP 1.500.000 to INV (ARAP #7 / B0 test) ====="
JE_BEFORE=$(PSQL "SELECT count(*) FROM journal_entries WHERE tenant_id='$TEN';")
BT_BEFORE=$(PSQL "SELECT count(*) FROM bank_transactions WHERE tenant_id='$TEN';")
echo "journal_entries before: $JE_BEFORE | bank_transactions before: $BT_BEFORE"

echo; echo "--- ★ TEST 3(a): GET applicable-deposits BEFORE apply (FE panel spine path) ---"
GET "/sales-invoices/$INVID/applicable-deposits" -o /tmp/appdep.json 2>/dev/null; GET "/sales-invoices/$INVID/applicable-deposits" > /tmp/appdep.json
python3 -c "import json;d=json.load(open('/tmp/appdep.json'));r=d.get('data') or d;items=r if isinstance(r,list) else (r.get('deposits') or r.get('items') or []);print('applicable deposits:',len(items));[print('  ',{k:v for k,v in x.items() if k in ('deposit_number','deposit_id','id','amount','available_amount','available_balance','remaining')}) for x in items]" 2>&1 | head
APPCOUNT=$(python3 -c "import json;d=json.load(open('/tmp/appdep.json'));r=d.get('data') or d;items=r if isinstance(r,list) else (r.get('deposits') or r.get('items') or []);print(len(items))" 2>/dev/null)
# KNOWN BUG (filed HIGH, 2026-07-26-applicable-deposits-500-uuid-varchar): this endpoint 500s
# (customer_id UUID bound to VARCHAR). The FE apply panel is broken. Per owner decision (A) we do
# NOT abort — the ledger/Branch-3 proof runs via apply-by-ID below. This confirms the UI path is
# broken while the backend apply operation is still testable.
[ "$APPCOUNT" = "0" ] && echo "  ^ applicable-deposits returned error/empty (KNOWN filed bug) — UI apply path broken; proceeding via apply-by-ID (ledger proof only)"

APPCNT=$(PSQL "SELECT count(*) FROM customer_deposit_applications WHERE deposit_id='$DEPID';")
if [ "$APPCNT" != "0" ]; then
  echo "apply already done (idempotent): $APPCNT application(s)"
else
  echo; echo "--- applying... ---"
  J POST /customer-deposits/$DEPID/apply "{\"applications\":[{\"invoice_id\":\"$INVID\",\"amount\":1500000}],\"application_date\":\"$D_APPLYDP\"}" | head -c 220; echo
fi

echo; echo "--- ★ JOURNAL (separate, DEPOSIT_APPLICATION: Dr 2-10500 1.5M / Cr 1-10400 1.5M) ---"
PSQLm "SELECT je.source_type, (je.source_id::text='$DEPID') AS src_is_deposit, c.account_code, LEFT(c.name,24) akun, c.account_type, jl.debit, jl.credit
       FROM journal_entries je JOIN journal_lines jl ON jl.journal_id=je.id JOIN chart_of_accounts c ON c.id=jl.account_id
       WHERE je.tenant_id='$TEN' AND je.source_type='DEPOSIT_APPLICATION' ORDER BY jl.line_number;"

echo "--- ★★ BRANCH 3 (the core question): raw RECEIVABLE ledger == compute_ar == 3.500.000 ---"
AR_LEDGER=$(PSQL "SELECT COALESCE(SUM(jl.debit-jl.credit),0)::bigint FROM journal_lines jl JOIN journal_entries je ON je.id=jl.journal_id JOIN chart_of_accounts c ON c.id=jl.account_id WHERE je.tenant_id='$TEN' AND je.status='POSTED' AND je.reversed_by_id IS NULL AND c.account_type='RECEIVABLE';")
AR_COMPUTE=$(PSQL "SELECT COALESCE(SUM(outstanding),0)::bigint FROM compute_ar_outstanding('$TEN');")
echo "  raw RECEIVABLE ledger=$AR_LEDGER | compute_ar_outstanding=$AR_COMPUTE"
aeq "Branch 3 raw RECEIVABLE ledger (after DP apply)" "$AR_LEDGER" "3500000"
aeq "Branch 3 compute_ar == raw (drift 0)" "$AR_COMPUTE" "$AR_LEDGER"

echo "--- artifacts present: customer_deposit_applications (active, reversed_by_id NULL, journal_id) ---"
PSQLm "SELECT status, reversed_by_id IS NULL AS effective, journal_id IS NOT NULL AS has_journal, amount_applied FROM customer_deposit_applications WHERE deposit_id='$DEPID';"
echo "  sales_invoices.amount_paid (expect 1.5M) + status (expect partial): $(PSQL "SELECT amount_paid||' / '||status FROM sales_invoices WHERE id='$INVID';")"
echo "  2-10500 Uang Muka Pelanggan (expect 0): $(PSQL "SELECT COALESCE(SUM(jl.credit-jl.debit),0)::bigint FROM journal_lines jl JOIN journal_entries je ON je.id=jl.journal_id JOIN chart_of_accounts c ON c.id=jl.account_id WHERE je.tenant_id='$TEN' AND je.status='POSTED' AND je.reversed_by_id IS NULL AND c.account_code='2-10500';")"

echo "--- artifacts ABSENT: no new bank_transaction (apply = ledger-only) ---"
BT_AFTER=$(PSQL "SELECT count(*) FROM bank_transactions WHERE tenant_id='$TEN';")
aeq "no bank movement on apply (ledger-only)" "$BT_AFTER" "$BT_BEFORE"

echo "--- uq_cda_journal_id (first cda row ever) not violated ---"
PSQL "SELECT 'cda rows='||count(*)||' distinct journal_id='||count(DISTINCT journal_id) FROM customer_deposit_applications WHERE deposit_id='$DEPID';"

echo; echo "--- ★ TEST 3(b): GET /api/members/{customer} — members AR (predict 5.000.000 = BUG vs compute 3.5M) ---"
GET "/members/$CUS" > /tmp/member.json
python3 -c "import json;d=json.load(open('/tmp/member.json'));m=d.get('data') or d;print({k:v for k,v in m.items() if any(t in k.lower() for t in ('ar','piutang','balance','saldo','outstanding'))})" 2>&1 | head

echo; echo "--- JOURNAL COUNT (+1 apply), apply date ---"
JE_AFTER=$(PSQL "SELECT count(*) FROM journal_entries WHERE tenant_id='$TEN';")
echo "journal_entries after: $JE_AFTER (before=$JE_BEFORE) | apply date: $(PSQL "SELECT journal_date FROM journal_entries WHERE tenant_id='$TEN' AND source_type='DEPOSIT_APPLICATION';")"

echo; echo "===== DRIFT + BANK GAP (AR 3.5M drift 0 via Branch3, bank 18M, gap 0) ====="
docker exec -i "$CONTAINER" psql -U postgres -d "$DB" -v ten="'$TEN'" -f - < "$DIR/drift_check.sql"

echo; echo "--- assertions ---"
DEPBAL=$(PSQL "SELECT COALESCE(SUM(jl.credit-jl.debit),0)::bigint FROM journal_lines jl JOIN journal_entries je ON je.id=jl.journal_id JOIN chart_of_accounts c ON c.id=jl.account_id WHERE je.tenant_id='$TEN' AND je.status='POSTED' AND je.reversed_by_id IS NULL AND c.account_code='2-10500';")
CDAROWS=$(PSQL "SELECT count(*) FROM customer_deposit_applications WHERE deposit_id='$DEPID';")
aeq "2-10500 Uang Muka drawn down to 0" "$DEPBAL" "0"
aeq "customer_deposit_applications row created" "$CDAROWS" "1"
aeq "journal_entries +1 (DEPOSIT_APPLICATION)" "$JE_AFTER" "$((JE_BEFORE+1))"
finish
