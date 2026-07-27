#!/bin/bash
# =============================================================================
# step_1_quote.sh — DP-flow STEP 1: create Penawaran (quote) via POST /api/quotes
# (the REAL FE useQuoteForm submit endpoint — NOT /quotes-with-items). Sends the FULL
# FE payload shape, incl. the V219 columns (opening_text/closing_text/payment_*) and DP
# (dp_amount canonical=1.500.000, dp_percent=30) so this exercises exactly what V219 fixed
# and sets up the B4 test (does DP survive to SO/invoice) at step 2.
# Item: Kaos Biru 30s, 100 pcs @ 50.000, non-PKP (tax_rate 0, tax_id null). Grand total 5.000.000.
# HIGH-RISK: quote_sequences has never produced a number in any DB. A broken generator is a
# FINDING, reported as-is.
# EXPECT: quote_number generated (sane format), quote_items saved, ZERO journal, drift AR/AP=0,
# BANK_GAP=0.
# =============================================================================
set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
source "$DIR/state.env"; source "$DIR/dates.env"; source "$DIR/verdict.sh"
H=(-H "Authorization: Bearer $TOK" -H "X-Tenant-Slug: $TEN" -H "Content-Type: application/json")
J(){ local m=$1 p=$2 d=${3:-'{}'}; curl -s -X "$m" "$B$p" "${H[@]}" -d "$d"; }
gid(){ python3 -c "import sys,json;d=json.load(sys.stdin);print((d.get('data') or d).get('id',''))" 2>/dev/null; }
PSQL(){ docker exec -i "$CONTAINER" psql -U postgres -d "$DB" -tAc "$1" | tr -d '[:space:]'; }
PSQLm(){ docker exec -i "$CONTAINER" psql -U postgres -d "$DB" -c "$1"; }

echo "===== STEP 1 — QUOTE (Penawaran) 100 pcs @ 50.000, DP 30% ====="
JE_BEFORE=$(PSQL "SELECT count(*) FROM journal_entries WHERE tenant_id='$TEN';")
echo "journal_entries before: $JE_BEFORE"

QID=$(PSQL "SELECT id FROM quotes WHERE tenant_id='$TEN' AND reference='QUO-KB-01';")
if [ -n "$QID" ]; then
  echo "quote exists (idempotent reuse): $QID"
else
  RESP=$(J POST /quotes "{
    \"customer_id\":\"$CUS\",\"customer_name\":\"Toko Merdeka\",
    \"quote_date\":\"$D_QUOTE\",\"expiry_date\":\"2026-07-31\",
    \"reference\":\"QUO-KB-01\",\"subject\":\"Penawaran Kaos Biru 100 pcs\",
    \"discount_type\":\"percentage\",\"discount_value\":0,
    \"dp_amount\":1500000,\"dp_percent\":30,
    \"notes\":\"Catatan pelanggan\",\"terms\":\"Syarat & ketentuan\",
    \"opening_text\":\"Dengan hormat, berikut penawaran kami.\",
    \"closing_text\":\"Demikian, terima kasih.\",
    \"payment_bank_name\":\"Bank BCA\",\"payment_account_number\":\"1111222233\",\"payment_account_holder\":\"Kaos Biru Konveksi\",
    \"items\":[{\"item_id\":\"$ITEM\",\"description\":\"Kaos Biru 30s\",\"quantity\":100,\"unit\":\"pcs\",\"unit_price\":50000,\"discount_percent\":0,\"tax_rate\":0,\"tax_id\":null,\"sort_order\":0}],
    \"status\":\"draft\"
  }")
  echo "resp: $(echo "$RESP" | head -c 220)"
  QID=$(echo "$RESP" | gid)
  [ -z "$QID" ] && { echo "!!! quote create FAILED (FINDING — inspect resp above) — ABORT"; exit 1; }
fi
grep -q '^export QID=' "$DIR/state.env" && sed -i "s|^export QID=.*|export QID=\"$QID\"|" "$DIR/state.env" || echo "export QID=\"$QID\"" >> "$DIR/state.env"
echo "QID=$QID"

echo; echo "--- ★ C2 GATE: read-back GET /api/quotes/{id} — 5 V219 columns NON-NULL (BATCH1 B1 fix) ---"
curl -s "$B/quotes/$QID" -H "Authorization: Bearer $TOK" -H "X-Tenant-Slug: $TEN" > /tmp/qdetail.json
echo "  detail keys: $(python3 -c "import json;d=json.load(open('/tmp/qdetail.json'));print({k:v for k,v in (d.get('data') or {}).items() if k in ('opening_text','closing_text','payment_bank_name','payment_account_number','payment_account_holder')})" 2>&1 | head -c 300)"
for col in opening_text closing_text payment_bank_name payment_account_number payment_account_holder; do
  v=$(python3 -c "import json;d=json.load(open('/tmp/qdetail.json'));print((d.get('data') or {}).get('$col') or '')" 2>/dev/null)
  ane "quote detail V219 col: $col non-null" "$v" ""
done

echo; echo "--- ★ C3 GATE: GET /api/quotes/{id}/pdf — 200 + RENDERED TEXT shows bank/account/dp ---"
PDFCODE=$(curl -s -o /tmp/quote_c3.pdf -w "%{http_code}" "$B/quotes/$QID/pdf" -H "Authorization: Bearer $TOK" -H "X-Tenant-Slug: $TEN")
aeq "quote PDF HTTP 200" "$PDFCODE" "200"
aeq "quote PDF is a real PDF (%PDF magic)" "$(head -c4 /tmp/quote_c3.pdf)" "%PDF"
# WeasyPrint embeds SUBSETTED fonts (glyph IDs, not ASCII) -> raw zlib/grep CANNOT read the text
# and would falsely pass. pdf_text.sh extracts via pdfminer (glyph->Unicode). Assert the customer-
# facing values actually RENDER — this catches a silent template var-name mismatch that would blank
# the Rekening/DP block with no error.
PTEXT=$(bash "$DIR/../pdf_text.sh" /tmp/quote_c3.pdf 2>/dev/null)
echo "  extracted text length: ${#PTEXT} chars"
acontains "PDF renders account number 1111222233" "$PTEXT" "1111222233"
acontains "PDF renders bank name Bank BCA" "$PTEXT" "Bank BCA"
acontains "PDF renders DP amount Rp 1.500.000" "$PTEXT" "1.500.000"
acontains "PDF renders Uang Muka label" "$PTEXT" "Uang Muka"

echo; echo "--- quote row: number/format + V219 columns + DP (all must be persisted) ---"
PSQLm "SELECT quote_number, status, quote_date, dp_amount, dp_percent,
              opening_text, closing_text, payment_bank_name, payment_account_number, payment_account_holder,
              subtotal, total_amount
       FROM quotes WHERE id='$QID';"

echo "--- quote_items (expect 1 row, qty 100 @ 50.000) ---"
PSQLm "SELECT description, quantity, unit, unit_price, tax_rate, tax_id, line_total FROM quote_items WHERE quote_id='$QID' ORDER BY sort_order;"

echo "--- quote_sequences row (the generator that had never produced a number) ---"
PSQLm "SELECT * FROM quote_sequences WHERE tenant_id='$TEN';" 2>&1 | head

echo; echo "--- ZERO JOURNAL check (quote must not post) + quote_number generated ---"
JE_AFTER=$(PSQL "SELECT count(*) FROM journal_entries WHERE tenant_id='$TEN';")
QNUM=$(PSQL "SELECT COALESCE(quote_number,'') FROM quotes WHERE id='$QID';")
QITEMS=$(PSQL "SELECT count(*) FROM quote_items WHERE quote_id='$QID';")
aeq "no new journal (quote must not post)" "$JE_AFTER" "$JE_BEFORE"
ane "quote_number generated" "$QNUM" ""
aeq "quote_items rows" "$QITEMS" "1"

echo; echo "===== DRIFT + BANK GAP (must stay 0) ====="
docker exec -i "$CONTAINER" psql -U postgres -d "$DB" -v ten="'$TEN'" -f - < "$DIR/drift_check.sql"
finish
