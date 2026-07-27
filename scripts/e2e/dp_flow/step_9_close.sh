#!/bin/bash
# =============================================================================
# step_9_close.sh — DP-flow STEP 9: TUTUP SO (Pesanan -> completed). ZERO journal.
# NOT period close — fiscal period is NOT touched (that would lock July 2026, Law 5).
# FE path (confirmed): SalesOrderDetailDesktop.tsx:205 -> POST /api/sales-orders/{id}/close
# ("Pesanan ditutup"). Backend sales_orders.py:950 — precondition status IN
# ('invoiced','shipped'); sets status='completed'; NO ledger write.
#
# ★ RUNTIME PROOF for audit finding #4 (independent counters, no cross-reconciliation):
#   PRE-close: shipped_qty=0 despite 100 pcs delivered (fulfill ran via
#   sales-invoices/{id}/fulfill, never sales-orders/{id}/ship) — while invoiced_qty=100
#   (filled by /to-invoice). /close does NOT reconcile shipped_qty either.
#
# EXPECT: status invoiced->completed · shipped_qty stays 0 · invoiced_qty stays 100 ·
# journal_entries stays 11 (ZERO new) · bank_transactions stays 4 · drift AR/AP=0 · BANK_GAP=0.
# =============================================================================
set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
source "$DIR/state.env"; source "$DIR/dates.env"
H=(-H "Authorization: Bearer $TOK" -H "X-Tenant-Slug: $TEN" -H "Content-Type: application/json")
PSQL(){ docker exec -i "$CONTAINER" psql -U postgres -d "$DB" -tAc "$1" | tr -d '[:space:]'; }
PSQLm(){ docker exec -i "$CONTAINER" psql -U postgres -d "$DB" -c "$1"; }

echo "===== STEP 9 — TUTUP SO (zero journal, NOT period close) ====="
JE_BEFORE=$(PSQL "SELECT count(*) FROM journal_entries WHERE tenant_id='$TEN';")
BT_BEFORE=$(PSQL "SELECT count(*) FROM bank_transactions WHERE tenant_id='$TEN';")
echo "--- PRE-close SO counters (★ shipped_qty=0 = finding #4 runtime proof) ---"
PSQLm "SELECT order_number, status, shipped_qty, invoiced_qty FROM sales_orders WHERE id='$SOID';"
echo "journal_entries before: $JE_BEFORE (expect 11) | bank_transactions: $BT_BEFORE (expect 4)"

STATUS=$(PSQL "SELECT status FROM sales_orders WHERE id='$SOID';")
if [ "$STATUS" = "completed" ]; then
  echo "  (already completed — idempotent skip)"
else
  echo; echo "--- POST /sales-orders/{SOID}/close ---"
  curl -s -w "\nHTTP=%{http_code}\n" -X POST "$B/sales-orders/$SOID/close" "${H[@]}" | head -c 300; echo
fi

echo; echo "--- POST-close SO state ---"
PSQLm "SELECT order_number, status, shipped_qty, invoiced_qty FROM sales_orders WHERE id='$SOID';"

JE_AFTER=$(PSQL "SELECT count(*) FROM journal_entries WHERE tenant_id='$TEN';")
BT_AFTER=$(PSQL "SELECT count(*) FROM bank_transactions WHERE tenant_id='$TEN';")
echo "  journal_entries: $JE_BEFORE -> $JE_AFTER $([ "$JE_AFTER" = "$JE_BEFORE" ] && echo 'PASS (ZERO new journal)' || echo 'FAIL — close created a journal!')"
echo "  bank_transactions: $BT_BEFORE -> $BT_AFTER $([ "$BT_AFTER" = "$BT_BEFORE" ] && echo 'PASS (no bank movement)' || echo 'FAIL')"
echo "  final status: $(PSQL "SELECT status FROM sales_orders WHERE id='$SOID';") (expect completed) | shipped_qty: $(PSQL "SELECT shipped_qty::bigint FROM sales_orders WHERE id='$SOID';") (STAYS 0 — finding #4, close does not reconcile) | invoiced_qty: $(PSQL "SELECT invoiced_qty::bigint FROM sales_orders WHERE id='$SOID';")"

echo; echo "--- fiscal period NOT touched (must stay open) ---"
PSQLm "SELECT period_name, status FROM fiscal_periods WHERE tenant_id='$TEN' AND '2026-07-20' BETWEEN start_date AND end_date;" 2>/dev/null || echo "  (fiscal_periods shape differs — verify manually)"

echo; echo "===== DRIFT + BANK GAP (unchanged: AR 0, AP 0, bank 21.5M, gap 0) ====="
docker exec -i "$CONTAINER" psql -U postgres -d "$DB" -v ten="'$TEN'" -f - < "$DIR/drift_check.sql"
