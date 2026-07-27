#!/bin/bash
# =============================================================================
# step_5_invoice.sh — DP-flow STEP 5: faktur from SO (deferred), Event 1 only.
# POST /api/sales-orders/{SOID}/to-invoice with the REAL FE body ({invoice_date,due_date,items};
# NO recognize_at — FE never sends it) -> draft invoice -> POST /api/sales-invoices/{id}/post.
# Delivery mode (tenant_config.revenue_recognition_policy='delivery') makes sell-from-stock DEFER:
# Event 1 only at post; COGS + revenue recognition happen at /fulfill (step 7).
#
# EXPECT: Dr 1-10400 Piutang 5.000.000 / Cr 2-10750 Pend. Diterima Dimuka 5.000.000, 2 lines,
# no PPN. fulfillment_status=pending, revenue_status=deferred. 4-10100=0, 5-10100=0, inventory
# 3.500.000, stock 100. sales_order_id set. invoice_date after DP (07-07). AR raw==compute_ar==5M.
# 2-10500 stays 1.500.000. BANK_GAP=0, bank 18.000.000. IF 3 journals (auto-fulfill) -> STOP.
# =============================================================================
set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
source "$DIR/state.env"; source "$DIR/dates.env"; source "$DIR/verdict.sh"
H=(-H "Authorization: Bearer $TOK" -H "X-Tenant-Slug: $TEN" -H "Content-Type: application/json")
J(){ local m=$1 p=$2 d=${3:-'{}'}; curl -s -X "$m" "$B$p" "${H[@]}" -d "$d"; }
PSQL(){ docker exec -i "$CONTAINER" psql -U postgres -d "$DB" -tAc "$1" | tr -d '[:space:]'; }
PSQLm(){ docker exec -i "$CONTAINER" psql -U postgres -d "$DB" -c "$1"; }

echo "===== STEP 5 — FAKTUR from SO (deferred, Event 1 only) ====="
JE_BEFORE=$(PSQL "SELECT count(*) FROM journal_entries WHERE tenant_id='$TEN';")
echo "journal_entries before: $JE_BEFORE (expect +1 = Event 1 only; +2/+3 => auto-fulfill FAIL)"
echo "policy: $(PSQL "SELECT revenue_recognition_policy FROM tenant_config WHERE tenant_id='$TEN';")"

INVID=$(PSQL "SELECT id FROM sales_invoices WHERE tenant_id='$TEN' AND sales_order_id='$SOID';")
if [ -n "$INVID" ]; then
  echo "invoice exists (idempotent reuse): $INVID"
else
  SOITEM=$(PSQL "SELECT id FROM sales_order_items WHERE sales_order_id='$SOID' LIMIT 1;")
  echo "so_item=$SOITEM"
  RESP=$(J POST /sales-orders/$SOID/to-invoice "{\"invoice_date\":\"$D_INVOICE\",\"due_date\":\"2026-08-07\",\"items\":[{\"so_item_id\":\"$SOITEM\",\"quantity\":100}]}")
  echo "to-invoice resp: $(echo "$RESP" | head -c 180)"
  INVID=$(PSQL "SELECT id FROM sales_invoices WHERE tenant_id='$TEN' AND sales_order_id='$SOID';")
  [ -z "$INVID" ] && { echo "!!! invoice not created — ABORT"; exit 1; }
fi
echo "INVID=$INVID"
grep -q '^export INVID=' "$DIR/state.env" && sed -i "s|^export INVID=.*|export INVID=\"$INVID\"|" "$DIR/state.env" || echo "export INVID=\"$INVID\"" >> "$DIR/state.env"

INVST=$(PSQL "SELECT status FROM sales_invoices WHERE id='$INVID';")
echo "invoice status before post: $INVST"
if [ "$INVST" = "draft" ]; then
  echo "posting invoice (Event 1)..."; J POST /sales-invoices/$INVID/post '{}' | head -c 200; echo
fi

echo; echo "--- invoice row: dates, spine, statuses ---"
PSQLm "SELECT invoice_number, status, invoice_date, (sales_order_id='$SOID') AS so_linked, fulfillment_status, revenue_status, total_amount FROM sales_invoices WHERE id='$INVID';"

echo "--- ★ JOURNAL (expect EVENT 1 ONLY: Dr 1-10400 5M / Cr 2-10750 5M, 2 lines, no PPN) ---"
PSQLm "SELECT je.source_type, c.account_code, LEFT(c.name,26) akun, c.account_type, jl.debit, jl.credit
       FROM journal_entries je JOIN journal_lines jl ON jl.journal_id=je.id
       JOIN chart_of_accounts c ON c.id=jl.account_id
       WHERE je.tenant_id='$TEN' AND je.status='POSTED' AND je.reversed_by_id IS NULL
         AND je.source_type IN ('INVOICE','INVOICE_FULFILLMENT','INVOICE_REVENUE','SALES_INVOICE_COGS','COGS')
       ORDER BY je.chain_sequence, jl.line_number;"

echo "--- revenue/COGS/inventory MUST be untouched at step 5 ---"
for pair in "4-10100:Penjualan(expect 0)" "5-10100:HPP(expect 0)" "1-10600:Persediaan(expect 3.5M)"; do
  code="${pair%%:*}"; lbl="${pair##*:}"
  v=$(PSQL "SELECT COALESCE(SUM(jl.debit-jl.credit),0)::bigint FROM journal_lines jl JOIN journal_entries je ON je.id=jl.journal_id JOIN chart_of_accounts c ON c.id=jl.account_id WHERE je.tenant_id='$TEN' AND je.status='POSTED' AND je.reversed_by_id IS NULL AND c.account_code='$code';")
  echo "  $code $lbl -> net=$v"
done
STOCK=$(PSQL "SELECT COALESCE(SUM(quantity_in-quantity_out),0)::bigint FROM inventory_ledger WHERE tenant_id='$TEN' AND product_id='$ITEM';")
REV0=$(PSQL "SELECT COALESCE(SUM(jl.debit-jl.credit),0)::bigint FROM journal_lines jl JOIN journal_entries je ON je.id=jl.journal_id JOIN chart_of_accounts c ON c.id=jl.account_id WHERE je.tenant_id='$TEN' AND je.status='POSTED' AND je.reversed_by_id IS NULL AND c.account_code='4-10100';")
COGS0=$(PSQL "SELECT COALESCE(SUM(jl.debit-jl.credit),0)::bigint FROM journal_lines jl JOIN journal_entries je ON je.id=jl.journal_id JOIN chart_of_accounts c ON c.id=jl.account_id WHERE je.tenant_id='$TEN' AND je.status='POSTED' AND je.reversed_by_id IS NULL AND c.account_code='5-10100';")
INV35=$(PSQL "SELECT COALESCE(SUM(jl.debit-jl.credit),0)::bigint FROM journal_lines jl JOIN journal_entries je ON je.id=jl.journal_id JOIN chart_of_accounts c ON c.id=jl.account_id WHERE je.tenant_id='$TEN' AND je.status='POSTED' AND je.reversed_by_id IS NULL AND c.account_code='1-10600';")
echo "  stock (expect 100): $STOCK"
aeq "revenue 4-10100 untouched at step 5" "$REV0" "0"
aeq "COGS 5-10100 untouched at step 5" "$COGS0" "0"
aeq "inventory 1-10600 still 3.5M (not yet shipped)" "$INV35" "3500000"
aeq "stock still 100" "$STOCK" "100"

echo; echo "--- ★ AR first meaningful read: raw RECEIVABLE ledger == compute_ar == 5.000.000 ---"
AR_LEDGER=$(PSQL "SELECT COALESCE(SUM(jl.debit-jl.credit),0)::bigint FROM journal_lines jl JOIN journal_entries je ON je.id=jl.journal_id JOIN chart_of_accounts c ON c.id=jl.account_id WHERE je.tenant_id='$TEN' AND je.status='POSTED' AND je.reversed_by_id IS NULL AND c.account_type='RECEIVABLE';")
AR_COMPUTE=$(PSQL "SELECT COALESCE(SUM(outstanding),0)::bigint FROM compute_ar_outstanding('$TEN');")
echo "  AR raw RECEIVABLE ledger=$AR_LEDGER | compute_ar_outstanding=$AR_COMPUTE"
aeq "AR raw RECEIVABLE ledger (Event 1)" "$AR_LEDGER" "5000000"
aeq "AR compute_ar_outstanding == raw (drift 0)" "$AR_COMPUTE" "$AR_LEDGER"
echo "  2-10500 Uang Muka Pelanggan (expect 1.500.000, unapplied): $(PSQL "SELECT COALESCE(SUM(jl.credit-jl.debit),0)::bigint FROM journal_lines jl JOIN journal_entries je ON je.id=jl.journal_id JOIN chart_of_accounts c ON c.id=jl.account_id WHERE je.tenant_id='$TEN' AND je.status='POSTED' AND je.reversed_by_id IS NULL AND c.account_code='2-10500';")"
echo "  customer_deposit_applications (expect 0, no apply yet): $(PSQL "SELECT count(*) FROM customer_deposit_applications WHERE deposit_id='$DEPID';")"

echo; echo "--- JOURNAL COUNT (expect +1) ---"
JE_AFTER=$(PSQL "SELECT count(*) FROM journal_entries WHERE tenant_id='$TEN';")
aeq "journal_entries +1 (Event 1 only, NOT auto-fulfill)" "$JE_AFTER" "$((JE_BEFORE+1))"

echo; echo "===== DRIFT + BANK GAP (AR now 5M drift 0, bank 18M, gap 0) ====="
docker exec -i "$CONTAINER" psql -U postgres -d "$DB" -v ten="'$TEN'" -f - < "$DIR/drift_check.sql"
finish
