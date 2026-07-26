#!/bin/bash
# =============================================================================
# step_2_convert.sh — DP-flow STEP 2: quote -> Sales Order + confirm SO.
# Real FE path: POST /api/quotes/{id}/send (draft must be 'sent' to convert) ->
# POST /api/quotes/{id}/to-order -> POST /api/sales-orders/{id}/confirm.
# B4 CONVERSION-1 TEST: does the quote's dp_amount/dp_percent reach sales_orders?
# (sales_orders has NO dp/deposit column — structural check confirmed; prove at runtime.)
# EXPECT: SO created + confirmed, quote status 'converted', ZERO journal, drift AR/AP=0, BANK_GAP=0.
# =============================================================================
set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
source "$DIR/state.env"; source "$DIR/dates.env"
H=(-H "Authorization: Bearer $TOK" -H "X-Tenant-Slug: $TEN" -H "Content-Type: application/json")
J(){ local m=$1 p=$2 d=${3:-'{}'}; curl -s -X "$m" "$B$p" "${H[@]}" -d "$d"; }
PSQL(){ docker exec -i "$CONTAINER" psql -U postgres -d "$DB" -tAc "$1" | tr -d '[:space:]'; }
PSQLm(){ docker exec -i "$CONTAINER" psql -U postgres -d "$DB" -c "$1"; }

echo "===== STEP 2 — CONVERT quote -> SO + confirm ====="
JE_BEFORE=$(PSQL "SELECT count(*) FROM journal_entries WHERE tenant_id='$TEN';")
echo "journal_entries before: $JE_BEFORE"

SOID=$(PSQL "SELECT id FROM sales_orders WHERE tenant_id='$TEN' AND quote_id='$QID';")
if [ -n "$SOID" ]; then
  echo "SO exists (idempotent reuse): $SOID"
else
  QST=$(PSQL "SELECT status FROM quotes WHERE id='$QID';")
  echo "quote status: $QST"
  if [ "$QST" = "draft" ]; then
    echo "sending quote (draft -> sent)..."; J POST /quotes/$QID/send '{}' | head -c 120; echo
  fi
  echo "converting to order..."; RESP=$(J POST /quotes/$QID/to-order '{}'); echo "resp: $(echo "$RESP" | head -c 200)"
  SOID=$(PSQL "SELECT id FROM sales_orders WHERE tenant_id='$TEN' AND quote_id='$QID';")
  [ -z "$SOID" ] && { echo "!!! SO not created — ABORT (FINDING)"; exit 1; }
fi
echo "SOID=$SOID"
grep -q '^export SOID=' "$DIR/state.env" && sed -i "s|^export SOID=.*|export SOID=\"$SOID\"|" "$DIR/state.env" || echo "export SOID=\"$SOID\"" >> "$DIR/state.env"

# Confirm SO if not already confirmed
SOST=$(PSQL "SELECT status FROM sales_orders WHERE id='$SOID';")
echo "SO status before confirm: $SOST"
if [ "$SOST" != "confirmed" ] && [ "$SOST" != "partial_shipped" ]; then
  echo "confirming SO..."; J POST /sales-orders/$SOID/confirm '{}' | head -c 160; echo
fi

echo; echo "--- SO row (order_number, status, quote_id link, total) ---"
PSQLm "SELECT order_number, status, order_date, (quote_id='$QID') AS linked_to_quote, subtotal, total_amount, confirmed_at IS NOT NULL AS confirmed FROM sales_orders WHERE id='$SOID';"

echo "--- B4 CONVERSION-1: did dp reach sales_orders? (no dp/deposit column exists) ---"
DPCOLS=$(PSQL "SELECT count(*) FROM information_schema.columns WHERE table_name='sales_orders' AND (column_name LIKE '%dp_%' OR column_name LIKE '%deposit%' OR column_name LIKE '%down%');")
echo "sales_orders dp/deposit/down columns: $DPCOLS -> $([ "$DPCOLS" = "0" ] && echo 'NONE (DP has no destination -> EVAPORATES at conversion 1)' || echo 'EXIST (investigate)')"
echo "  (quote still holds dp for reference: $(PSQL "SELECT dp_amount||' / '||dp_percent||'%' FROM quotes WHERE id='$QID';"); SO links back via quote_id)"

echo "--- quote status after convert (expect 'converted') ---"
PSQL "SELECT status||' converted_to='||COALESCE(converted_to_type,'?')||':'||COALESCE(converted_to_id::text,'?') FROM quotes WHERE id='$QID';"

echo; echo "--- ZERO JOURNAL check (convert/confirm must not post) ---"
JE_AFTER=$(PSQL "SELECT count(*) FROM journal_entries WHERE tenant_id='$TEN';")
echo "journal_entries after: $JE_AFTER (before=$JE_BEFORE) -> $([ "$JE_AFTER" = "$JE_BEFORE" ] && echo 'PASS (no new journal)' || echo 'FAIL (SO created a journal!)')"

echo; echo "===== DRIFT + BANK GAP (must stay 0) ====="
docker exec -i "$CONTAINER" psql -U postgres -d "$DB" -v ten="'$TEN'" -f - < "$DIR/drift_check.sql"
