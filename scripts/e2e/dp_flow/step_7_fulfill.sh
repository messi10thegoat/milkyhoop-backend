#!/bin/bash
# =============================================================================
# step_7_fulfill.sh — DP-flow STEP 7: Pengiriman (fulfill) 60 then 40 pcs.
# Recognizes COGS (Event 2) + revenue (Event 3) at delivery. Two calls to make the GET
# /fulfillments gate 3-sided (100 -> 40 -> 0 shippable, each distinguishable from the 500/empty
# error state) AND to exercise the PARTIAL path (fulfillment_status/revenue_status='partial',
# last-fulfillment-absorbs-remainder). "shippable" = item_summary[].remaining_qty (quantity -
# fulfilled_qty). GET /fulfillments selects f.voided_reason -> 7.0 is the FIRST HTTP proof of
# f5cd41a5 (the 500 that started this whole session).
#
# END: revenue 4-10100=5.000.000, COGS 5-10100=3.500.000, gross profit 1.500.000, inventory 0,
# stock 0, 2-10750=0, AR stays 3.500.000, 2-10500=0. AR/AP drift=0 each sub-step, no new
# bank_transactions, BANK_GAP=0, bank 18.000.000.
# =============================================================================
set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
source "$DIR/state.env"; source "$DIR/dates.env"; source "$DIR/verdict.sh"
H=(-H "Authorization: Bearer $TOK" -H "X-Tenant-Slug: $TEN" -H "Content-Type: application/json")
J(){ local m=$1 p=$2 d=${3:-'{}'}; curl -s -X "$m" "$B$p" "${H[@]}" -d "$d"; }
PSQL(){ docker exec -i "$CONTAINER" psql -U postgres -d "$DB" -tAc "$1" | tr -d '[:space:]'; }
PSQLm(){ docker exec -i "$CONTAINER" psql -U postgres -d "$DB" -c "$1"; }
BT_BEFORE=$(PSQL "SELECT count(*) FROM bank_transactions WHERE tenant_id='$TEN';")

IIID=$(PSQL "SELECT id FROM sales_invoice_items WHERE invoice_id='$INVID' LIMIT 1;")
echo "invoice_item=$IIID"

fgate(){ # $1 = label ; prints HTTP code + shippable(remaining_qty) + fulfillment/revenue status
  local code body
  body=$(curl -s -w "\n%{http_code}" "$B/sales-invoices/$INVID/fulfillments" -H "Authorization: Bearer $TOK" -H "X-Tenant-Slug: $TEN")
  code=$(echo "$body" | tail -1); body=$(echo "$body" | sed \$d)
  echo "  [$1] HTTP=$code"
  [ "$code" = "500" ] && { echo "  !!! 500 — f5cd41a5 REGRESSED / schema drift — STOP"; return 1; }
  echo "$body" | python3 -c "import sys,json
d=json.load(sys.stdin); r=d.get('data') or d
summ=r.get('item_summary') or r.get('items_summary') or []
fs=r.get('fulfillment_status'); rs=r.get('revenue_status')
ship=sum(float(s.get('remaining_qty',0)) for s in summ) if summ else None
print('    fulfillment_status=%s revenue_status=%s shippable(remaining_qty)=%s fulfillments=%d'%(fs,rs,ship,len(r.get('fulfillments') or [])))"
}

echo; echo "===== 7.0 GATE — GET /fulfillments BEFORE fulfill (expect 200, shippable=100) ★ f5cd41a5 HTTP proof ====="
fgate "7.0 pre" || exit 1

FQ=$(PSQL "SELECT COALESCE(total_fulfilled_qty,0)::int FROM sales_invoices WHERE id='$INVID';")
echo; echo "===== 7.1 FULFILL 60 (fulfilled so far: $FQ) ====="
if [ "$FQ" -lt 60 ]; then
  J POST /sales-invoices/$INVID/fulfill "{\"warehouse_id\":\"$WH\",\"fulfillment_date\":\"2026-07-12\",\"recognize_revenue\":true,\"items\":[{\"invoice_item_id\":\"$IIID\",\"quantity\":60}],\"idempotency_key\":\"ship-kb-60\"}" | head -c 160; echo
else echo "  (already >=60, skip)"; fi
echo "--- journals after 60 (COGS Dr 5-10100 2.1M/Cr 1-10600 2.1M ; Revenue Dr 2-10750 3.0M/Cr 4-10100 3.0M) ---"
PSQLm "SELECT je.source_type, c.account_code, c.account_type, jl.debit, jl.credit FROM journal_entries je JOIN journal_lines jl ON jl.journal_id=je.id JOIN chart_of_accounts c ON c.id=jl.account_id WHERE je.tenant_id='$TEN' AND je.source_type IN ('INVOICE_FULFILLMENT','INVOICE_REVENUE','SALES_INVOICE_COGS') ORDER BY je.chain_sequence, jl.line_number;"
echo "  stock=$(PSQL "SELECT COALESCE(SUM(quantity_in-quantity_out),0) FROM inventory_ledger WHERE tenant_id='$TEN' AND product_id='$ITEM';") (expect 40) | persediaan 1-10600=$(PSQL "SELECT COALESCE(SUM(jl.debit-jl.credit),0)::bigint FROM journal_lines jl JOIN journal_entries je ON je.id=jl.journal_id JOIN chart_of_accounts c ON c.id=jl.account_id WHERE je.tenant_id='$TEN' AND je.status='POSTED' AND je.reversed_by_id IS NULL AND c.account_code='1-10600';") (expect 1400000)"

echo; echo "===== 7.2 GATE — GET /fulfillments (expect 200, shippable=40) ====="
fgate "7.2 mid" || exit 1

FQ=$(PSQL "SELECT COALESCE(total_fulfilled_qty,0)::int FROM sales_invoices WHERE id='$INVID';")
echo; echo "===== 7.3 FULFILL 40 (fulfilled so far: $FQ) ====="
if [ "$FQ" -lt 100 ]; then
  J POST /sales-invoices/$INVID/fulfill "{\"warehouse_id\":\"$WH\",\"fulfillment_date\":\"2026-07-14\",\"recognize_revenue\":true,\"items\":[{\"invoice_item_id\":\"$IIID\",\"quantity\":40}],\"idempotency_key\":\"ship-kb-40\"}" | head -c 160; echo
else echo "  (already 100, skip)"; fi
echo "--- allocated vs recognized (last fulfillment absorbs remainder; recognized<=allocated) ---"
PSQLm "SELECT description, quantity, fulfilled_qty, allocated_amount, recognized_amount FROM sales_invoice_items WHERE invoice_id='$INVID';"

echo; echo "===== 7.4 GATE — GET /fulfillments (expect 200, shippable=0 all-shipped; report shape) ====="
fgate "7.4 post" || exit 1

echo; echo "===== END STEP 7 TOTALS ====="
for pair in "4-10100:revenue(5000000)" "5-10100:COGS(3500000)" "1-10600:inventory(0)" "2-10750:deferred(0)" "2-10500:deposit(0)"; do
  code="${pair%%:*}"; lbl="${pair##*:}"
  echo "  $code $lbl = $(PSQL "SELECT COALESCE(SUM(jl.debit-jl.credit),0)::bigint FROM journal_lines jl JOIN journal_entries je ON je.id=jl.journal_id JOIN chart_of_accounts c ON c.id=jl.account_id WHERE je.tenant_id='$TEN' AND je.status='POSTED' AND je.reversed_by_id IS NULL AND c.account_code='$code';")"
done
REV=$(PSQL "SELECT COALESCE(-SUM(jl.debit-jl.credit),0)::bigint FROM journal_lines jl JOIN journal_entries je ON je.id=jl.journal_id JOIN chart_of_accounts c ON c.id=jl.account_id WHERE je.tenant_id='$TEN' AND je.status='POSTED' AND je.reversed_by_id IS NULL AND c.account_code='4-10100';")
COGS=$(PSQL "SELECT COALESCE(SUM(jl.debit-jl.credit),0)::bigint FROM journal_lines jl JOIN journal_entries je ON je.id=jl.journal_id JOIN chart_of_accounts c ON c.id=jl.account_id WHERE je.tenant_id='$TEN' AND je.status='POSTED' AND je.reversed_by_id IS NULL AND c.account_code='5-10100';")
STOCK7=$(PSQL "SELECT COALESCE(SUM(quantity_in-quantity_out),0)::bigint FROM inventory_ledger WHERE tenant_id='$TEN' AND product_id='$ITEM';")
INV7=$(PSQL "SELECT COALESCE(SUM(jl.debit-jl.credit),0)::bigint FROM journal_lines jl JOIN journal_entries je ON je.id=jl.journal_id JOIN chart_of_accounts c ON c.id=jl.account_id WHERE je.tenant_id='$TEN' AND je.status='POSTED' AND je.reversed_by_id IS NULL AND c.account_code='1-10600';")
DEF7=$(PSQL "SELECT COALESCE(SUM(jl.credit-jl.debit),0)::bigint FROM journal_lines jl JOIN journal_entries je ON je.id=jl.journal_id JOIN chart_of_accounts c ON c.id=jl.account_id WHERE je.tenant_id='$TEN' AND je.status='POSTED' AND je.reversed_by_id IS NULL AND c.account_code='2-10750';")
echo "  gross profit = $((REV-COGS)) | stock=$STOCK7"
BT_AFTER=$(PSQL "SELECT count(*) FROM bank_transactions WHERE tenant_id='$TEN';")
echo "  bank_transactions: before=$BT_BEFORE after=$BT_AFTER"
aeq "revenue recognized (4-10100)" "$REV" "5000000"
aeq "COGS recognized (5-10100)" "$COGS" "3500000"
aeq "gross profit" "$((REV-COGS))" "1500000"
aeq "inventory 1-10600 depleted to 0" "$INV7" "0"
aeq "deferred revenue 2-10750 fully recognized to 0" "$DEF7" "0"
aeq "stock depleted to 0" "$STOCK7" "0"
aeq "no bank movement on fulfill" "$BT_AFTER" "$BT_BEFORE"

echo; echo "===== DRIFT + BANK GAP (AR stays 3.5M, bank 18M, gap 0) ====="
docker exec -i "$CONTAINER" psql -U postgres -d "$DB" -v ten="'$TEN'" -f - < "$DIR/drift_check.sql"
finish
