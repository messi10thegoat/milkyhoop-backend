#!/bin/bash
# =============================================================================
# step_R_wac0_guard.sh — PAGAR SISI-SEHAT, dan assertion PALING BERHARGA di
# seluruh skenario ini.
#
# KENAPA INI BUKAN PELENGKAP:
#   Tanpa pagar ini, "tiga jurnal muncul saat posting" bisa berarti dua hal yang
#   sangat berbeda:
#     (i)  all_have_cost benar-benar MEMILIH jalur auto-fulfill, atau
#     (ii) auto-fulfill menyala TANPA SYARAT dan kebetulan kondisinya terpenuhi.
#   Keduanya menghasilkan skenario HIJAU. Yang kedua hijau untuk alasan yang salah.
#   Satu-satunya cara memisahkannya adalah menunjukkan jalur itu TIDAK menyala
#   ketika syaratnya tak terpenuhi.
#
# MEKANISME: item baru "Kaos Merah 30s" yang TAK PERNAH DIBELI -> WAC = 0 ->
#   all_have_cost = false -> cabang make-to-order:
#     fulfillment_status=pending, revenue_status=deferred, NOL jurnal COGS/RECOG,
#     dan sejak P4 juga post_warnings TERISI (dulu senyap — itu pun dibuktikan).
#
# BUKAN uji void/retur/nota kredit — faktur ini dibiarkan hidup dan jurnalnya
# DIPIN (7 -> 8). Void di luar scope (belum pernah dieksekusi runtime).
#
# Dijalankan SESUDAH closing_invariant supaya invariant skenario utama (AR=0 dsb)
# dinilai pada keadaan yang bersih.
# =============================================================================
set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
DP="$DIR/../dp_flow"
source "$DP/state.env"; source "$DP/dates.env"; source "$DP/verdict.sh"
H=(-H "Authorization: Bearer $TOK" -H "X-Tenant-Slug: $TEN" -H "Content-Type: application/json")
J(){ local m=$1 p=$2 d=${3:-'{}'}; curl -s -X "$m" "$B$p" "${H[@]}" -d "$d"; }
gid(){ python3 -c "import sys,json;d=json.load(sys.stdin);print((d.get('data') or d).get('id',''))" 2>/dev/null; }
PSQL(){ docker exec -i "$CONTAINER" psql -U postgres -d "$DB" -tAc "$1" | tr -d '[:space:]'; }
PSQLm(){ docker exec -i "$CONTAINER" psql -U postgres -d "$DB" -c "$1"; }

echo "===== PAGAR SISI-SEHAT — item WAC=0 HARUS menolak jalur auto-fulfill ====="
JE_BEFORE=$(PSQL "SELECT count(*) FROM journal_entries WHERE tenant_id='$TEN';")
aeq "mulai dari 7 jurnal (keadaan akhir skenario utama)" "$JE_BEFORE" "7"

# Snapshot akun yang HARUS TIDAK BERGERAK.
bal(){ PSQL "SELECT COALESCE(SUM(jl.debit-jl.credit),0)::bigint FROM journal_lines jl JOIN journal_entries je ON je.id=jl.journal_id JOIN chart_of_accounts c ON c.id=jl.account_id WHERE je.tenant_id='$TEN' AND je.status='POSTED' AND je.reversed_by_id IS NULL AND c.account_code='$1';"; }
COGS_BEFORE=$(bal 5-10100); REV_BEFORE=$(bal 4-10100)
echo "sebelum: HPP=$COGS_BEFORE  Penjualan=$REV_BEFORE"

# --- item ber-WAC-0 ---------------------------------------------------------
# ⚠️ KOREKSI 2026-08-09 (run pertama gagal DI SINI, dan benar begitu):
#   "tak pernah dibeli" TIDAK cukup untuk WAC=0. get_weighted_average_cost()
#   jatuh ke fallback products.purchase_price kalau inventory_ledger kosong:
#       SELECT average_cost FROM inventory_ledger ... LIMIT 1;
#       IF NULL -> RETURN COALESCE(products.purchase_price, 0);
#   Fixture pertama memakai purchase_price 40.000 -> WAC 40.000 -> all_have_cost
#   TRUE -> auto-fulfill menyala -> gagal 409 "Stok Kaos Merah 30s tidak cukup".
#   Produknya benar; fixture-nya yang salah. purchase_price WAJIB 0.
#   Ini juga kasus nyata: barang jadi konveksi yang DIPRODUKSI, bukan dibeli.
ITEM0=$(PSQL "SELECT id FROM products WHERE tenant_id='$TEN' AND item_code='FG-KAOS-MERAH-30S';")
[ -z "$ITEM0" ] && ITEM0=$(J POST /items '{"name":"Kaos Merah 30s","item_type":"goods","track_inventory":true,"base_unit":"pcs","item_code":"FG-KAOS-MERAH-30S","kategori":"Barang Jadi","purchase_price":0,"sales_price":60000,"for_sales":true,"for_purchases":false}' | gid)
[ -z "$ITEM0" ] && { echo "!!! item WAC-0 tak terbuat — ABORT"; exit 1; }
echo "ITEM0=$ITEM0"

WAC0=$(PSQL "SELECT COALESCE(get_weighted_average_cost('$TEN','$ITEM0'),0)::bigint;")
PP0=$(PSQL "SELECT COALESCE(purchase_price,0)::bigint FROM products WHERE id='$ITEM0';")
NLED_PRE=$(PSQL "SELECT count(*) FROM inventory_ledger WHERE tenant_id='$TEN' AND product_id='$ITEM0';")
TRK=$(PSQL "SELECT track_inventory FROM products WHERE id='$ITEM0';")
aeq "prasyarat fixture: purchase_price = 0 (fallback WAC)" "$PP0" "0"
aeq "prasyarat fixture: nol riwayat inventory_ledger" "$NLED_PRE" "0"
aeq "item baru WAC = 0" "$WAC0" "0"
atrue "item baru track_inventory (kalau tidak, cabangnya beda dan uji ini sia-sia)" "$TRK"
[ "$WAC0" = "0" ] || { echo "!!! WAC != 0 — cabang yang diuji TIDAK akan diambil. Uji ini jadi tak bermakna. ABORT"; exit 1; }

CUSNAME=$(docker exec -i "$CONTAINER" psql -U postgres -d "$DB" -tAc "SELECT nama FROM customers WHERE id='$CUS';")

# --- faktur kedua ------------------------------------------------------------
INVID0=$(PSQL "SELECT id FROM sales_invoices WHERE tenant_id='$TEN' AND ref_no='AF-INV-WAC0';")
if [ -z "$INVID0" ]; then
  curl -s -o /tmp/af_w_create.json -X POST "$B/sales-invoices" "${H[@]}" -d "{
    \"customer_id\":\"$CUS\",\"customer_name\":\"$CUSNAME\",
    \"invoice_date\":\"$D_SETTLE\",\"due_date\":\"2026-08-20\",
    \"ref_no\":\"AF-INV-WAC0\",\"notes\":\"pagar sisi-sehat WAC=0\",
    \"tax_rate\":0,\"auto_post\":false,
    \"items\":[{\"item_id\":\"$ITEM0\",\"description\":\"Kaos Merah 30s\",\"quantity\":10,\"unit\":\"pcs\",\"unit_price\":60000}]}" >/dev/null
  INVID0=$(PSQL "SELECT id FROM sales_invoices WHERE tenant_id='$TEN' AND ref_no='AF-INV-WAC0';")
  [ -z "$INVID0" ] && { echo "!!! faktur WAC-0 tak terbuat — ABORT"; exit 1; }
fi
echo "INVID0=$INVID0"

if [ "$(PSQL "SELECT status FROM sales_invoices WHERE id='$INVID0';")" = "draft" ]; then
  PCODE=$(curl -s -o /tmp/af_w_post.json -w "%{http_code}" -X POST "$B/sales-invoices/$INVID0/post" "${H[@]}" -d '{}')
  echo "  post HTTP=$PCODE  body=$(head -c 200 /tmp/af_w_post.json)"
  aeq "POST /post -> 200 (faktur TETAP sah, hanya pendapatannya ditangguhkan)" "$PCODE" "200"
fi

# ★ GERBANG KERAS — WAJIB sebelum membaca fulfillment_status/revenue_status.
# CACAT GATE INI SENDIRI (ditemukan di run pertama, 2026-08-09):
#   'pending'/'deferred' adalah NILAI DEFAULT faktur DRAFT. Ketika posting gagal
#   409, ASSERT 1 tetap HIJAU — melaporkan "auto-fulfill tidak menyala" padahal
#   yang sebenarnya terjadi adalah "tidak ada yang pernah di-posting".
#   Itu persis Law 33 mekanisme 7: assert yang tak bisa membedakan
#   BERHASIL-MENOLAK dari TIDAK-PERNAH-DIJALANKAN. Nilai bacaannya identik.
#   Gerbang ini membuat perbedaan itu mustahil disembunyikan.
ST0=$(PSQL "SELECT status FROM sales_invoices WHERE id='$INVID0';")
NI_GATE=$(PSQL "SELECT count(*) FROM journal_entries WHERE tenant_id='$TEN' AND source_id::text='$INVID0' AND source_type='INVOICE' AND status='POSTED';")
if [ "$ST0" != "posted" ] || [ "$NI_GATE" != "1" ]; then
  echo "!!! GERBANG GAGAL: faktur status='$ST0', jurnal INVOICE=$NI_GATE (harus posted/1)."
  echo "    Tanpa posting yang berhasil, 'pending/deferred' di bawah HANYA nilai default draft"
  echo "    dan TIDAK membuktikan apa pun tentang pemilihan cabang. ABORT — bukan hijau palsu."
  exit 1
fi
echo "  gerbang OK: faktur posted, 1 jurnal INVOICE -> pembacaan status di bawah BERMAKNA"

echo; echo "--- ★ ASSERT 1: jalur yang dipilih = make-to-order, BUKAN auto-fulfill ---"
FS0=$(PSQL "SELECT fulfillment_status FROM sales_invoices WHERE id='$INVID0';")
RS0=$(PSQL "SELECT revenue_status FROM sales_invoices WHERE id='$INVID0';")
aeq "fulfillment_status = pending (auto-fulfill TIDAK menyala)" "$FS0" "pending"
aeq "revenue_status = deferred" "$RS0" "deferred"

echo; echo "--- ★ ASSERT 2: NOL jurnal COGS dan NOL jurnal pengakuan pendapatan ---"
NF0=$(PSQL "SELECT count(*) FROM journal_entries WHERE tenant_id='$TEN' AND source_id::text='$INVID0' AND source_type='INVOICE_FULFILLMENT';")
NR0=$(PSQL "SELECT count(*) FROM journal_entries WHERE tenant_id='$TEN' AND source_id::text='$INVID0' AND source_type='INVOICE_REVENUE';")
NI0=$(PSQL "SELECT count(*) FROM journal_entries WHERE tenant_id='$TEN' AND source_id::text='$INVID0' AND source_type='INVOICE';")
NFUL0=$(PSQL "SELECT count(*) FROM invoice_fulfillments WHERE invoice_id='$INVID0';")
aeq "nol INVOICE_FULFILLMENT" "$NF0" "0"
aeq "nol INVOICE_REVENUE" "$NR0" "0"
aeq "tetap ada 1 INVOICE (penagihan sah)" "$NI0" "1"
aeq "nol baris invoice_fulfillments" "$NFUL0" "0"

echo; echo "--- ★ ASSERT 2b: STOK TIDAK BERKURANG (tak ada barang keluar) ---"
# Auto-fulfill mengeluarkan stok. Kalau cabangnya benar-benar TIDAK diambil,
# tak boleh ada satu pun pergerakan persediaan untuk item ini — dan item lama
# (Kaos Biru) juga harus diam di 0, bukan ikut terseret.
NLED0=$(PSQL "SELECT count(*) FROM inventory_ledger WHERE tenant_id='$TEN' AND product_id='$ITEM0';")
STOCK0=$(PSQL "SELECT COALESCE(SUM(quantity_in-quantity_out),0)::bigint FROM inventory_ledger WHERE tenant_id='$TEN' AND product_id='$ITEM0';")
STOCK_BIRU=$(PSQL "SELECT COALESCE(SUM(quantity_in-quantity_out),0)::bigint FROM inventory_ledger WHERE tenant_id='$TEN' AND product_id='$ITEM';")
aeq "nol baris inventory_ledger untuk item WAC-0 (nol barang keluar)" "$NLED0" "0"
aeq "stok item WAC-0 tetap 0" "$STOCK0" "0"
aeq "stok Kaos Biru tak ikut terseret (tetap 0)" "$STOCK_BIRU" "0"

echo; echo "--- ★ ASSERT 3: akun HPP dan Penjualan TAK BERGERAK ---"
COGS_AFTER=$(bal 5-10100); REV_AFTER=$(bal 4-10100)
echo "  HPP: $COGS_BEFORE -> $COGS_AFTER | Penjualan: $REV_BEFORE -> $REV_AFTER"
aeq "HPP tak bertambah" "$COGS_AFTER" "$COGS_BEFORE"
aeq "Penjualan tak bertambah" "$REV_AFTER" "$REV_BEFORE"

echo; echo "--- ★ ASSERT 4: post_warnings TERISI (P4 — dulu senyap, buktikan tak lagi) ---"
if [ -s /tmp/af_w_post.json ]; then
  python3 - /tmp/af_w_post.json <<'PY'
import sys, json
d = json.load(open(sys.argv[1])); r = d.get('data') or d
w = r.get('warnings') or d.get('warnings') or []
print("     warnings (%d):" % len(w))
for x in w: print("       - %s" % x)
PY
  NW=$(python3 -c "import json;d=json.load(open('/tmp/af_w_post.json'));r=d.get('data') or d;print(len(r.get('warnings') or d.get('warnings') or []))")
  WTXT=$(python3 -c "import json;d=json.load(open('/tmp/af_w_post.json'));r=d.get('data') or d;print(' | '.join(r.get('warnings') or d.get('warnings') or []))")
  ane "warnings TIDAK kosong (kalau 0 -> P4 regresi jadi senyap lagi)" "$NW" "0"
  acontains "warning menyebut harga pokok / WAC" "$WTXT" "WAC=0"
else
  _fail "respons post tak tersimpan — tak bisa memeriksa warnings"
fi

echo; echo "--- ★ ASSERT 5: PIN jumlah jurnal 7 -> 8 (hanya +1 = penagihan) ---"
PSQLm "SELECT source_type, count(*) FROM journal_entries WHERE tenant_id='$TEN' GROUP BY 1 ORDER BY 1;"
JE_AFTER=$(PSQL "SELECT count(*) FROM journal_entries WHERE tenant_id='$TEN';")
aeq "journal_entries 7 -> 8" "$JE_AFTER" "$((JE_BEFORE+1))"

echo; echo "--- AR sekarang 600.000 (faktur WAC-0 belum dibayar) — DISENGAJA dan DIPIN ---"
AR_END=$(PSQL "SELECT COALESCE(SUM(outstanding),0)::bigint FROM compute_ar_outstanding('$TEN');")
AR_LED=$(PSQL "SELECT COALESCE(SUM(jl.debit-jl.credit),0)::bigint FROM journal_lines jl JOIN journal_entries je ON je.id=jl.journal_id JOIN chart_of_accounts c ON c.id=jl.account_id WHERE je.tenant_id='$TEN' AND je.status='POSTED' AND je.reversed_by_id IS NULL AND c.account_type='RECEIVABLE';")
aeq "AR ledger = 600.000" "$AR_LED" "600000"
aeq "compute_ar == ledger (drift 0 walau tertangguh)" "$AR_END" "$AR_LED"

echo; echo "===== DRIFT + BANK GAP ====="
docker exec -i "$CONTAINER" psql -U postgres -d "$DB" -v ten="'$TEN'" -f - < "$DP/drift_check.sql"
finish
