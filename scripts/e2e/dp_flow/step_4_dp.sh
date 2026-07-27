#!/bin/bash
# =============================================================================
# step_4_dp.sh — DP-flow STEP 4: customer pays the 30% DP.
# STEP 3 (tagih DP): ABSENT by design — no DP-billing/proforma endpoint exists; the canonical
# FE flow is "Terima DP" straight from the SO (SalesOrderDetailDesktop.tsx: P3 bridge reuses the
# deposit create form). Written gap; no billing document improvised.
#
# STEP 4 endpoint: POST /api/customer-deposits (FE "Terima DP"), auto_post=true (FE supports
# create+post in one call), sales_order_id + quote_id set (spine linkage — REQUIRED so the deposit
# is a candidate for apply at step 6 via get_applicable_deposits' quote_id/sales_order_id spine).
# DP entered MANUALLY (1.500.000) — B4 verdict: nothing pre-fills it (conversion 1 dropped dp).
#
# ARTIFACT CONTRACT for RECEIVE (NOT the 5-artifact settlement contract; that is APPLY/step 6):
# from code, create+post writes exactly (1) journal_entries, (2) customer_deposits wrapper
# (status posted, journal_id), (3) bank_transactions. NO allocation, NO invoice cache at receive.
#
# THE mission-critical assertion: ZERO journal_lines on any account_type='RECEIVABLE' (Law 29/30 —
# DP is a LIABILITY, must not touch AR). Tested by account_type, not literal code.
# EXPECT journal: Dr 1-10201 Bank 1.500.000 / Cr 2-10500 Uang Muka Pelanggan 1.500.000, 2 lines,
# no PPN. AR stays 0. Bank 18.000.000 (delta -2.000.000 from opening). drift AR/AP=0, BANK_GAP=0.
# =============================================================================
set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
source "$DIR/state.env"; source "$DIR/dates.env"; source "$DIR/verdict.sh"
H=(-H "Authorization: Bearer $TOK" -H "X-Tenant-Slug: $TEN" -H "Content-Type: application/json")
J(){ local m=$1 p=$2 d=${3:-'{}'}; curl -s -X "$m" "$B$p" "${H[@]}" -d "$d"; }
gid(){ python3 -c "import sys,json;d=json.load(sys.stdin);print((d.get('data') or d).get('id',''))" 2>/dev/null; }
PSQL(){ docker exec -i "$CONTAINER" psql -U postgres -d "$DB" -tAc "$1" | tr -d '[:space:]'; }
PSQLm(){ docker exec -i "$CONTAINER" psql -U postgres -d "$DB" -c "$1"; }

echo "===== STEP 3 — TAGIH DP: ABSENT by design (no endpoint). Written gap. Proceeding to step 4. ====="
echo; echo "===== STEP 4 — CUSTOMER PAYS DP 1.500.000 (30%) ====="
JE_BEFORE=$(PSQL "SELECT count(*) FROM journal_entries WHERE tenant_id='$TEN';")
echo "journal_entries before: $JE_BEFORE"

DEPID=$(PSQL "SELECT id FROM customer_deposits WHERE tenant_id='$TEN' AND reference='DP-KB-01';")
if [ -n "$DEPID" ]; then
  echo "deposit exists (idempotent reuse): $DEPID"
else
  RESP=$(J POST /customer-deposits "{
    \"customer_id\":\"$CUS\",\"customer_name\":\"Toko Merdeka\",
    \"amount\":1500000,\"deposit_date\":\"$D_DP\",
    \"payment_method\":\"transfer\",\"account_id\":\"$BANK_COA\",\"bank_account_id\":\"$BANK\",
    \"reference\":\"DP-KB-01\",\"notes\":\"Uang muka 30%\",
    \"auto_post\":true,
    \"quote_id\":\"$QID\",\"sales_order_id\":\"$SOID\",
    \"idempotency_key\":\"dp-kb-01-$TEN\"
  }")
  echo "resp: $(echo "$RESP" | head -c 220)"
  DEPID=$(echo "$RESP" | gid)
  [ -z "$DEPID" ] && { echo "!!! deposit create FAILED — ABORT (FINDING)"; exit 1; }
fi
grep -q '^export DEPID=' "$DIR/state.env" && sed -i "s|^export DEPID=.*|export DEPID=\"$DEPID\"|" "$DIR/state.env" || echo "export DEPID=\"$DEPID\"" >> "$DIR/state.env"
echo "DEPID=$DEPID"

echo; echo "========== RECEIVE ARTIFACT CONTRACT (journal + wrapper + bank_txn) =========="
echo "--- [wrapper] customer_deposits (status posted, journal_id, sales_order_id + quote_id linked) ---"
PSQLm "SELECT deposit_number, status, amount, deposit_date, journal_id IS NOT NULL AS has_journal,
              (sales_order_id::text='$SOID') AS so_linked, (quote_id::text='$QID') AS quote_linked,
              amount_applied
       FROM customer_deposits WHERE id='$DEPID';"

echo "--- [journal] lines (expect Dr 1-10201 1.5M / Cr 2-10500 1.5M, exactly 2, no PPN) ---"
PSQLm "SELECT c.account_code, LEFT(c.name,26) akun, c.account_type, jl.debit, jl.credit
       FROM journal_lines jl JOIN chart_of_accounts c ON c.id=jl.account_id
       WHERE jl.journal_id=(SELECT journal_id FROM customer_deposits WHERE id='$DEPID')
       ORDER BY jl.line_number;"

echo "--- [bank_txn] (money IN +1.5M, journal + reference linked, running_balance 18M) ---"
PSQLm "SELECT transaction_type, amount, transaction_date, journal_id IS NOT NULL AS has_journal, running_balance
       FROM bank_transactions WHERE tenant_id='$TEN' AND bank_account_id='$BANK' ORDER BY transaction_date, created_at;"

echo; echo "========== ★ MISSION-CRITICAL: ZERO RECEIVABLE lines in the DP journal (by account_type) =========="
PSQLm "SELECT c.account_type, count(*) AS lines, COALESCE(SUM(jl.debit+jl.credit),0) AS touched
       FROM journal_lines jl JOIN chart_of_accounts c ON c.id=jl.account_id
       WHERE jl.journal_id=(SELECT journal_id FROM customer_deposits WHERE id='$DEPID')
         AND c.account_type='RECEIVABLE'
       GROUP BY c.account_type;"
RCV=$(PSQL "SELECT COALESCE(count(*),0) FROM journal_lines jl JOIN chart_of_accounts c ON c.id=jl.account_id WHERE jl.journal_id=(SELECT journal_id FROM customer_deposits WHERE id='$DEPID') AND c.account_type='RECEIVABLE';")
aeq "ZERO RECEIVABLE lines in DP journal (Law 29/30: DP is a LIABILITY)" "$RCV" "0"

echo; echo "--- artifacts that must be ABSENT at receive (belong to APPLY/step 6) ---"
echo "customer_deposit_applications for this deposit: $(PSQL "SELECT count(*) FROM customer_deposit_applications WHERE deposit_id='$DEPID';") (expect 0 — no apply yet)"

echo; echo "--- bank balance + delta (expect 18.000.000, delta -2.000.000 from opening 20M) ---"
PSQL "SELECT COALESCE(SUM(jl.debit-jl.credit),0)::bigint FROM journal_lines jl JOIN journal_entries je ON je.id=jl.journal_id WHERE je.tenant_id='$TEN' AND je.status='POSTED' AND je.reversed_by_id IS NULL AND jl.account_id='$BANK_COA';" | sed 's/^/bank_ledger=/'

echo; echo "--- ZERO NEW-vs-expected: 1 new journal (the DP) ---"
JE_AFTER=$(PSQL "SELECT count(*) FROM journal_entries WHERE tenant_id='$TEN';")
echo "journal_entries after: $JE_AFTER (before=$JE_BEFORE, expect +1)"

echo; echo "===== DRIFT + BANK GAP (AR stays 0, deposit liability 1.5M, gap 0) ====="
docker exec -i "$CONTAINER" psql -U postgres -d "$DB" -v ten="'$TEN'" -f - < "$DIR/drift_check.sql"

echo; echo "--- assertions ---"
BANKLED=$(PSQL "SELECT COALESCE(SUM(jl.debit-jl.credit),0)::bigint FROM journal_lines jl JOIN journal_entries je ON je.id=jl.journal_id WHERE je.tenant_id='$TEN' AND je.status='POSTED' AND je.reversed_by_id IS NULL AND jl.account_id='$BANK_COA';")
DEPLIAB=$(PSQL "SELECT COALESCE(SUM(jl.credit-jl.debit),0)::bigint FROM journal_lines jl JOIN chart_of_accounts c ON c.id=jl.account_id JOIN journal_entries je ON je.id=jl.journal_id WHERE je.tenant_id='$TEN' AND je.status='POSTED' AND je.reversed_by_id IS NULL AND c.account_code='2-10500';")
aeq "bank ledger after DP (18M)" "$BANKLED" "18000000"
aeq "2-10500 Uang Muka liability (1.5M)" "$DEPLIAB" "1500000"
aeq "journal_entries +1 (DP posted)" "$JE_AFTER" "$((JE_BEFORE+1))"
finish
