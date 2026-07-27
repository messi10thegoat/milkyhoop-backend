#!/bin/bash
# =============================================================================
# step_0_buy.sh — DP-flow STEP 0: purchase 100 pcs Kaos Biru 30s @ 35.000 from
# vendor, non-PKP (no PPN). Expected journal on post:
#   Dr 1-10600 Persediaan Barang Dagangan (INVENTORY_MERCHANDISE)  3.500.000
#   Cr 2-10100 Hutang Usaha (AP_TRADE)                             3.500.000
# Idempotent: skips if a bill with ref_no BUY-KB-01 already exists.
# =============================================================================
set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
source "$DIR/state.env"
source "$DIR/dates.env"
source "$DIR/verdict.sh"
H=(-H "Authorization: Bearer $TOK" -H "X-Tenant-Slug: $TEN" -H "Content-Type: application/json")
J(){ local m=$1 p=$2 d=${3:-'{}'}; curl -s -X "$m" "$B$p" "${H[@]}" -d "$d"; }
gid(){ python3 -c "import sys,json;d=json.load(sys.stdin);print((d.get('data') or d).get('id',''))" 2>/dev/null; }
PSQL(){ docker exec -i "$CONTAINER" psql -U postgres -d "$DB" -tAc "$1" | tr -d '[:space:]'; }
PSQLm(){ docker exec -i "$CONTAINER" psql -U postgres -d "$DB" -c "$1"; }

echo "===== STEP 0 — BUY 100 pcs @ 35.000 (non-PKP) ====="
BILL=$(PSQL "SELECT id FROM bills WHERE tenant_id='$TEN' AND ref_no='BUY-KB-01';")
if [ -n "$BILL" ]; then
  echo "BILL exists (idempotent reuse): $BILL"
else
  BILL=$(J POST /bills/v2 "{\"vendor_id\":\"$VND\",\"issue_date\":\"$D_BUY\",\"due_date\":\"$D_BUY_DUE\",\"ref_no\":\"BUY-KB-01\",\"tax_rate\":0,\"tax_inclusive\":false,\"status\":\"draft\",\"notes\":\"Beli Kaos Biru 100 pcs\",\"items\":[{\"product_id\":\"$ITEM\",\"product_name\":\"Kaos Biru 30s\",\"qty\":100,\"unit\":\"pcs\",\"price\":35000}]}" | gid)
  echo "BILL created: $BILL"
  [ -z "$BILL" ] && { echo "!!! bill create failed — ABORT"; exit 1; }
fi

# Post if still draft
ST=$(PSQL "SELECT status_v2 FROM bills WHERE id='$BILL';")
echo "status_v2 before post: $ST"
if [ "$ST" = "draft" ] || [ -z "$ST" ]; then
  echo "posting..."; J POST /bills/$BILL/post '{}' | head -c 160; echo
fi

# persist BILL id
grep -q '^export BILL=' "$DIR/state.env" && sed -i "s|^export BILL=.*|export BILL=\"$BILL\"|" "$DIR/state.env" || echo "export BILL=\"$BILL\"" >> "$DIR/state.env"

echo; echo "--- grand_total (expect 3500000, no PPN) ---"
GT=$(PSQL "SELECT COALESCE(grand_total,0)::bigint FROM bills WHERE id='$BILL';")
aeq "bill grand_total (no PPN)" "$GT" "3500000"

echo; echo "===== JOURNAL (BILL) — accounts/sides/amounts ====="
PSQLm "SELECT je.source_type, je.status, c.account_code, LEFT(c.name,28) akun, c.account_type, jl.debit, jl.credit
       FROM journal_entries je JOIN journal_lines jl ON jl.journal_id=je.id
       JOIN chart_of_accounts c ON c.id=jl.account_id
       WHERE je.tenant_id='$TEN' AND je.source_id::text=(SELECT id::text FROM bills WHERE id='$BILL')
       ORDER BY je.chain_sequence, jl.line_number;"

echo "--- assertions (journal Dr 1-10600 3.5M / Cr 2-10100 3.5M) ---"
INV=$(PSQL "SELECT COALESCE(SUM(jl.debit-jl.credit),0)::bigint FROM journal_lines jl JOIN journal_entries je ON je.id=jl.journal_id JOIN chart_of_accounts c ON c.id=jl.account_id WHERE je.tenant_id='$TEN' AND je.status='POSTED' AND je.reversed_by_id IS NULL AND c.account_code='1-10600';")
AP=$(PSQL "SELECT COALESCE(SUM(jl.credit-jl.debit),0)::bigint FROM journal_lines jl JOIN journal_entries je ON je.id=jl.journal_id JOIN chart_of_accounts c ON c.id=jl.account_id WHERE je.tenant_id='$TEN' AND je.status='POSTED' AND je.reversed_by_id IS NULL AND c.account_code='2-10100';")
aeq "1-10600 Persediaan debit" "$INV" "3500000"
aeq "2-10100 Hutang Usaha credit" "$AP" "3500000"
finish
