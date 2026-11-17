# Konfig gerbang unit BUG-257 (GET /api/customers/{id}/price-history: histori harga jual per pelanggan+barang dari faktur terposting non-void, konversi satuan di server). Tiap sabotase WAJIB membuat tes merah. Tanpa migrasi, hanya baca.
W=/root/mh-b257; CABANG=feat/bug257-histori-harga; NAMA_JENDELA=b257
POLA_UNIT='bug257|histori_harga|customers|izin_rute|sql_placeholder'
HARNESS=scripts/ops/uji-salinan-b257.sh
PENANDA_BERKAS=services/histori_harga_pelanggan.py; PENANDA='SQL_RIWAYAT'
A=backend/api_gateway/app; T=backend/api_gateway/tests/unit/test_bug257_histori_harga.py; HF=$A/services/histori_harga_pelanggan.py
SAB=(
 "h1 harga konversi terbalik (faktor tujuan/baris)|$HF|s#harga = _r2(harga \* ht\[\"faktor\"\] / hb\[\"faktor\"\])#harga = _r2(harga * hb[\"faktor\"] / ht[\"faktor\"])#|$T"
 "h2 qty tak dikonversi|$HF|s#qty = qty \* hb\[\"faktor\"\] / ht\[\"faktor\"\]#pass#|$T"
 "h3 faktur saldo awal ikut|$HF|s# AND NOT si.is_opening_balance##|$T"
 "h4 draf/void ikut|$HF|s#si.status NOT IN ('draft', 'void')#si.status <> 'x'#|$T"
 "h5 limit tak dijepit|$HF|s#BATAS_MAKS = 50#BATAS_MAKS = 5000#|$T"
 "h6 urut terlama dulu|$HF|s#ORDER BY si.invoice_date DESC#ORDER BY si.invoice_date ASC#|$T"
 "h7 exclude_doc_id diabaikan|$HF|s#(\$4::uuid IS NULL OR si.id <> \$4)#(\$4::uuid IS NULL OR TRUE)#|$T"
 "h8 tenant tak disaring|$HF|s#WHERE si.tenant_id = \$1 AND si.customer_id = \$2#WHERE si.customer_id = \$2#|$T"
 "h9 izin rute hilang|$A/middleware/permission_middleware.py|s#price-history\$\", \[\"GET\"\], \"sales_invoice\", \"R\"#price-history\$\", [\"GET\"], \"sales_invoice\", \"X\"#|$T"
 "h10 rute tak memanggil riwayat|$A/routers/customers.py|s#histori_harga.riwayat(#histori_harga.tak_ada(#|$T"
)
