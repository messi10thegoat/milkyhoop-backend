#!/bin/bash
# =============================================================================
# step_1_invoice_autopost.sh — SKENARIO #2 (AUTO-FULFILL) langkah 1.
#
# Faktur 100 pcs Kaos Biru 30s @ 50.000, non-PKP, TANPA DP, lalu POSTING.
# Inilah yang membedakan skenario ini dari #1: tenant TIDAK punya baris
# tenant_config, jadi policy jatuh ke default kode 'invoice' -> sell-from-stock
# AUTO-FULFILL: TIGA jurnal lahir ATOMIK pada satu POST /post.
#
#   1. INVOICE              Dr 1-10400 Piutang 5.000.000 / Cr 2-10750 Pend.Dit.Dimuka
#   2. INVOICE_FULFILLMENT  Dr 5-10100 HPP     3.500.000 / Cr 1-10600 Persediaan
#   3. INVOICE_REVENUE      Dr 2-10750 Pend.Dit.Dimuka 5.000.000 / Cr 4-10100 Penjualan
#
# JALUR YANG DIUJI: POST /sales-invoices (draft) -> POST /{id}/post.
# Ini jalur FE, dan satu-satunya dari dua jalur masuk yang memanggil
# check_period_is_open di lapisan aplikasi (sales_invoices.py:2437). Jalur kedua
# (POST /sales-invoices dengan auto_post=true) TIDAK memanggilnya — difile
# terpisah, bukan yang diuji di sini.
#
# CATATAN LAW 34: briefing menjanjikan field "shippable" pada GET /fulfillments.
# Field itu TIDAK ADA. Yang setara dan nyata = item_summary[].remaining_qty dan
# .deferred_amount. Assert di bawah memakai yang nyata.
# =============================================================================
set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
DP="$DIR/../dp_flow"                       # REUSE, bukan salin
source "$DP/state.env"; source "$DP/dates.env"; source "$DP/verdict.sh"
H=(-H "Authorization: Bearer $TOK" -H "X-Tenant-Slug: $TEN" -H "Content-Type: application/json")
PSQL(){ docker exec -i "$CONTAINER" psql -U postgres -d "$DB" -tAc "$1" | tr -d '[:space:]'; }
PSQLm(){ docker exec -i "$CONTAINER" psql -U postgres -d "$DB" -c "$1"; }
# Nominal yang diharapkan — dapat disabotase oleh uji-merah (Law 33) lewat env.
E_AR=${E_AR:-5000000}
E_COGS=${E_COGS:-3500000}
E_REVENUE=${E_REVENUE:--5000000}

echo "===== SKENARIO #2 STEP 1 — FAKTUR 100 @ 50.000, POSTING -> AUTO-FULFILL ====="

# --- PRASYARAT: policy HARUS tak-ada-baris. Kalau bocor, seluruh skenario
#     menguji delivery mode sambil melaporkan hijau. Diperiksa DI SINI juga,
#     bukan hanya di step -1, karena inilah langkah yang bergantung padanya.
POL=$(PSQL "SELECT COALESCE((SELECT revenue_recognition_policy FROM tenant_config WHERE tenant_id='$TEN'),'<no-row>');")
aeq "policy = tanpa baris (auto-fulfill lewat default kode)" "$POL" "<no-row>"

JE_BEFORE=$(PSQL "SELECT count(*) FROM journal_entries WHERE tenant_id='$TEN';")
STOCK_BEFORE=$(PSQL "SELECT COALESCE(SUM(quantity_in-quantity_out),0)::bigint FROM inventory_ledger WHERE tenant_id='$TEN' AND product_id='$ITEM';")
WAC=$(PSQL "SELECT COALESCE(get_weighted_average_cost('$TEN','$ITEM'),0)::bigint;")
echo "before: journal_entries=$JE_BEFORE  stok=$STOCK_BEFORE  WAC=$WAC"
aeq "stok 100 SEBELUM posting" "$STOCK_BEFORE" "100"
aeq "WAC 35.000 (prasyarat all_have_cost -> jalur auto)" "$WAC" "35000"

CUSNAME=$(docker exec -i "$CONTAINER" psql -U postgres -d "$DB" -tAc "SELECT nama FROM customers WHERE id='$CUS';")

# --- create draft (idempotent lewat ref_no) ---------------------------------
INVID2=$(PSQL "SELECT id FROM sales_invoices WHERE tenant_id='$TEN' AND ref_no='AF-INV-01';")
if [ -n "$INVID2" ]; then
  echo "faktur sudah ada (idempotent reuse): $INVID2"
else
  RESP=$(curl -s -X POST "$B/sales-invoices" "${H[@]}" -d "{
    \"customer_id\":\"$CUS\",\"customer_name\":\"$CUSNAME\",
    \"invoice_date\":\"$D_INVOICE\",\"due_date\":\"2026-08-07\",
    \"ref_no\":\"AF-INV-01\",\"notes\":\"Auto-fulfill scenario\",
    \"tax_rate\":0,\"auto_post\":false,
    \"items\":[{\"item_id\":\"$ITEM\",\"description\":\"Kaos Biru 30s\",\"quantity\":100,\"unit\":\"pcs\",\"unit_price\":50000}]}")
  echo "create resp: $(echo "$RESP" | head -c 200)"
  INVID2=$(PSQL "SELECT id FROM sales_invoices WHERE tenant_id='$TEN' AND ref_no='AF-INV-01';")
  [ -z "$INVID2" ] && { echo "!!! faktur tak terbuat — ABORT"; exit 1; }
fi
echo "INVID2=$INVID2"
grep -q '^export INVID2=' "$DP/state.env" && sed -i "s|^export INVID2=.*|export INVID2=\"$INVID2\"|" "$DP/state.env" || echo "export INVID2=\"$INVID2\"" >> "$DP/state.env"

# --- ★ POSTING: satu panggilan, tiga jurnal ---------------------------------
INVST=$(PSQL "SELECT status FROM sales_invoices WHERE id='$INVID2';")
echo "status sebelum post: $INVST"
if [ "$INVST" = "draft" ]; then
  echo "posting (harap TIGA jurnal atomik)..."
  POSTCODE=$(curl -s -o /tmp/af_post.json -w "%{http_code}" -X POST "$B/sales-invoices/$INVID2/post" "${H[@]}" -d '{}')
  echo "  HTTP=$POSTCODE  body=$(head -c 200 /tmp/af_post.json)"
  aeq "POST /post -> 200" "$POSTCODE" "200"
fi

echo; echo "--- baris faktur ---"
PSQLm "SELECT invoice_number, status, invoice_date, fulfillment_status, revenue_status,
              total_amount::bigint, total_fulfilled_qty, total_recognized_amount::bigint
       FROM sales_invoices WHERE id='$INVID2';"

echo "--- ★ TIGA JURNAL (akun / sisi / nominal) ---"
PSQLm "SELECT je.source_type, je.journal_date, je.status, c.account_code, LEFT(c.name,24) akun,
              jl.debit::bigint dr, jl.credit::bigint cr
       FROM journal_entries je JOIN journal_lines jl ON jl.journal_id=je.id
       JOIN chart_of_accounts c ON c.id=jl.account_id
       WHERE je.tenant_id='$TEN' AND je.source_id::text='$INVID2'
       ORDER BY je.chain_sequence, jl.line_number;"

echo; echo "--- ASSERT A: status langsung sesudah POSTING (bukan sesudah pengiriman) ---"
FS=$(PSQL "SELECT fulfillment_status FROM sales_invoices WHERE id='$INVID2';")
RS=$(PSQL "SELECT revenue_status FROM sales_invoices WHERE id='$INVID2';")
aeq "fulfillment_status = fulfilled SEGERA setelah posting" "$FS" "fulfilled"
aeq "revenue_status = recognized SEGERA setelah posting" "$RS" "recognized"

echo; echo "--- ASSERT B: stok 100 -> 0 PADA SAAT POSTING ---"
STOCK_AFTER=$(PSQL "SELECT COALESCE(SUM(quantity_in-quantity_out),0)::bigint FROM inventory_ledger WHERE tenant_id='$TEN' AND product_id='$ITEM';")
echo "  stok: $STOCK_BEFORE -> $STOCK_AFTER"
aeq "stok 0 setelah posting (keluar saat POST, bukan saat kirim)" "$STOCK_AFTER" "0"

echo; echo "--- ASSERT C: tiga jurnal, source_type PERSIS, nol jurnal lain ---"
N_INV=$(PSQL "SELECT count(*) FROM journal_entries WHERE tenant_id='$TEN' AND source_id::text='$INVID2' AND source_type='INVOICE' AND status='POSTED';")
N_FUL=$(PSQL "SELECT count(*) FROM journal_entries WHERE tenant_id='$TEN' AND source_id::text='$INVID2' AND source_type='INVOICE_FULFILLMENT' AND status='POSTED';")
N_REV=$(PSQL "SELECT count(*) FROM journal_entries WHERE tenant_id='$TEN' AND source_id::text='$INVID2' AND source_type='INVOICE_REVENUE' AND status='POSTED';")
N_OTHER=$(PSQL "SELECT count(*) FROM journal_entries WHERE tenant_id='$TEN' AND source_id::text='$INVID2' AND source_type NOT IN ('INVOICE','INVOICE_FULFILLMENT','INVOICE_REVENUE');")
aeq "1x INVOICE" "$N_INV" "1"
aeq "1x INVOICE_FULFILLMENT (HPP)" "$N_FUL" "1"
aeq "1x INVOICE_REVENUE (pengakuan)" "$N_REV" "1"
aeq "nol jurnal source_type lain dari faktur ini" "$N_OTHER" "0"
JE_AFTER=$(PSQL "SELECT count(*) FROM journal_entries WHERE tenant_id='$TEN';")
aeq "journal_entries +3 dalam SATU panggilan" "$JE_AFTER" "$((JE_BEFORE+3))"

echo; echo "--- ★ ASSERT D: KETIGA jurnal SETANGGAL invoice_date ---"
# KENAPA assert ini ada (temuan recon (f), 2026-08-09):
#   Periode fiskal diperiksa aplikasi HANYA pada invoice_date (:2437), sedangkan
#   jurnal COGS/RECOG memakai fulfillment_date. Auto-fulfill kebetulan mengoper
#   invoice_date sebagai fulfillment_date, sehingga guard-nya memadai — KARENA
#   KEBETULAN, bukan karena dirancang. Kalau suatu hari fulfillment_date bergeser
#   (mis. default ke hari ini), jurnal COGS/RECOG bisa mendarat di periode yang
#   sudah ditutup tanpa satu pun pemeriksaan aplikasi menyadarinya — hanya trigger
#   DB trg_prevent_closed_period_journal yang tersisa, dengan galat kasar.
#   Assert ini mengubah kebetulan itu menjadi KONTRAK yang dijaga.
DATES_DISTINCT=$(PSQL "SELECT count(DISTINCT journal_date) FROM journal_entries WHERE tenant_id='$TEN' AND source_id::text='$INVID2';")
DATE_ALL=$(PSQL "SELECT DISTINCT journal_date::text FROM journal_entries WHERE tenant_id='$TEN' AND source_id::text='$INVID2';")
aeq "ketiga jurnal punya SATU tanggal saja" "$DATES_DISTINCT" "1"
aeq "tanggal itu = invoice_date ($D_INVOICE)" "$DATE_ALL" "$D_INVOICE"

echo; echo "--- ASSERT E: artefak invoice_fulfillments (penanda = idempotency_key) ---"
PSQLm "SELECT fulfillment_number, fulfillment_date, status, idempotency_key,
              (journal_id IS NOT NULL) AS ada_jurnal_cogs,
              (revenue_journal_id IS NOT NULL) AS ada_jurnal_revenue
       FROM invoice_fulfillments WHERE invoice_id='$INVID2';"
NF=$(PSQL "SELECT count(*) FROM invoice_fulfillments WHERE invoice_id='$INVID2';")
IDK=$(PSQL "SELECT idempotency_key FROM invoice_fulfillments WHERE invoice_id='$INVID2';")
FSTAT=$(PSQL "SELECT status FROM invoice_fulfillments WHERE invoice_id='$INVID2';")
HASJ=$(PSQL "SELECT (journal_id IS NOT NULL AND revenue_journal_id IS NOT NULL) FROM invoice_fulfillments WHERE invoice_id='$INVID2';")
FWH=$(PSQL "SELECT (warehouse_id='$WH') FROM invoice_fulfillments WHERE invoice_id='$INVID2';")
aeq "1 baris invoice_fulfillments (otomatis)" "$NF" "1"
aeq "idempotency_key = AUTO_FULFILL:{invoice_id}" "$IDK" "AUTO_FULFILL:$INVID2"
aeq "status posted" "$FSTAT" "posted"
atrue "kedua journal_id terisi (COGS + revenue)" "$HASJ"
atrue "gudang = WH provision (faktur tak kirim warehouse_id -> fallback)" "$FWH"
NFI=$(PSQL "SELECT count(*) FROM invoice_fulfillment_items fi JOIN invoice_fulfillments f ON f.id=fi.fulfillment_id WHERE f.invoice_id='$INVID2';")
TC=$(PSQL "SELECT COALESCE(SUM(fi.total_cost),0)::bigint FROM invoice_fulfillment_items fi JOIN invoice_fulfillments f ON f.id=fi.fulfillment_id WHERE f.invoice_id='$INVID2';")
aeq "1 baris invoice_fulfillment_items" "$NFI" "1"
aeq "total_cost = HPP" "$TC" "$E_COGS"

echo; echo "--- ASSERT F: saldo akun (journal-derived, Iron Law 16) ---"
bal(){ PSQL "SELECT COALESCE(SUM(jl.debit-jl.credit),0)::bigint FROM journal_lines jl JOIN journal_entries je ON je.id=jl.journal_id JOIN chart_of_accounts c ON c.id=jl.account_id WHERE je.tenant_id='$TEN' AND je.status='POSTED' AND je.reversed_by_id IS NULL AND c.account_code='$1';"; }
AR=$(bal 1-10400); DEF=$(bal 2-10750); REV=$(bal 4-10100); COGS=$(bal 5-10100); INVA=$(bal 1-10600)
echo "  1-10400 Piutang=$AR | 2-10750 Pend.Dit.Dimuka=$DEF | 4-10100 Penjualan=$REV | 5-10100 HPP=$COGS | 1-10600 Persediaan=$INVA"
aeq "Piutang $E_AR (penuh, TANPA DP)" "$AR" "$E_AR"
aeq "Pend. Diterima Dimuka netto 0 (dikredit lalu didebit dalam satu posting)" "$DEF" "0"
aeq "Penjualan diakui" "$REV" "$E_REVENUE"
aeq "HPP diakui" "$COGS" "$E_COGS"
aeq "Persediaan habis (3.5jt masuk, 3.5jt keluar)" "$INVA" "0"
AR_C=$(PSQL "SELECT COALESCE(SUM(outstanding),0)::bigint FROM compute_ar_outstanding('$TEN');")
aeq "compute_ar_outstanding == ledger (drift 0)" "$AR_C" "$AR"

echo; echo "--- ASSERT G: GET /sales-invoices/{id}/fulfillments -> 200, nol sisa kirim ---"
FCODE=$(curl -s -o /tmp/af_ful.json -w "%{http_code}" -X GET "$B/sales-invoices/$INVID2/fulfillments" "${H[@]}")
aeq "GET /fulfillments -> 200 (bukan 500)" "$FCODE" "200"
python3 - /tmp/af_ful.json <<'PY'
import sys, json
d = json.load(open(sys.argv[1])); r = d.get('data') or d
it = r.get('item_summary') or []
print("     fulfillment_status=%s revenue_status=%s total_fulfilled_qty=%s" %
      (r.get('fulfillment_status'), r.get('revenue_status'), r.get('total_fulfilled_qty')))
print("     fulfillments=%d  item_summary=%d" % (len(r.get('fulfillments') or []), len(it)))
print("     SUM_REMAINING=%g" % sum(float(s.get('remaining_qty') or 0) for s in it))
print("     SUM_DEFERRED=%g" % sum(float(s.get('deferred_amount') or 0) for s in it))
PY
REM=$(python3 -c "import json;d=json.load(open('/tmp/af_ful.json'));r=d.get('data') or d;print(int(sum(float(s.get('remaining_qty') or 0) for s in (r.get('item_summary') or []))))")
DEFR=$(python3 -c "import json;d=json.load(open('/tmp/af_ful.json'));r=d.get('data') or d;print(int(sum(float(s.get('deferred_amount') or 0) for s in (r.get('item_summary') or []))))")
NFUL=$(python3 -c "import json;d=json.load(open('/tmp/af_ful.json'));r=d.get('data') or d;print(len(r.get('fulfillments') or []))")
# "shippable" TIDAK ADA di respons (Law 34) — remaining_qty adalah padanannya yang nyata.
aeq "sisa yang bisa dikirim = 0" "$REM" "0"
aeq "nilai tertangguh = 0" "$DEFR" "0"
aeq "1 pengiriman tercatat (yang otomatis)" "$NFUL" "1"

echo; echo "--- ASSERT H: IDEMPOTENSI — posting ulang tak melahirkan jurnal kedua ---"
RE=$(curl -s -o /tmp/af_repost.json -w "%{http_code}" -X POST "$B/sales-invoices/$INVID2/post" "${H[@]}" -d '{}')
echo "  repost HTTP=$RE  body=$(head -c 160 /tmp/af_repost.json)"
ane "posting ulang DITOLAK (bukan 200)" "$RE" "200"
JE_REPOST=$(PSQL "SELECT count(*) FROM journal_entries WHERE tenant_id='$TEN';")
NF2=$(PSQL "SELECT count(*) FROM invoice_fulfillments WHERE invoice_id='$INVID2';")
aeq "nol jurnal baru dari posting ulang" "$JE_REPOST" "$JE_AFTER"
aeq "masih 1 baris invoice_fulfillments" "$NF2" "1"

echo; echo "===== DRIFT + BANK GAP ====="
docker exec -i "$CONTAINER" psql -U postgres -d "$DB" -v ten="'$TEN'" -f - < "$DP/drift_check.sql"
finish
