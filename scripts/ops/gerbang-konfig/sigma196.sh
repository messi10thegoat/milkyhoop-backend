# Konfig gerbang unit BUG-196 (sum_total daftar: batal/void/ditolak/kedaluwarsa keluar, draf tetap; satu penentu services/sum_total_berlaku). Tiap sabotase WAJIB membuat tes merah.
W=/root/mh-b196; CABANG=feat/sigma-daftar-berlaku; NAMA_JENDELA=sigma196
POLA_UNIT='sum_total|totals|listpage|daftar|expense|biaya|quote|penawaran|proforma|sales_order|sales_invoice|purchase_order|bill|credit|deposit|payment|izin_rute|inventaris_rute|pagar_rute|permission'
HARNESS=scripts/ops/uji-salinan-sigma196.sh
PENANDA_BERKAS=services/sum_total_berlaku.py; PENANDA='def sql_berlaku'
A=backend/api_gateway/app; T=tests/unit/test_sum_total_berlaku_196.py; SGF=$A/services/sum_total_berlaku.py; SPF=$A/services/so_posisi.py; EXR=$A/routers/expenses.py; QUF=$A/routers/quotes.py
SAB=(
 "s1 faktur penjualan: void ikut lagi|$SGF|s#\"sales_invoice\": (\"void\",),#\"sales_invoice\": (\"xvoid\",),#|$T"
 "s2 draf ikut hilang dari Σ|$SGF|s#return status not in TIDAK_BERLAKU\[modul\]#return status not in TIDAK_BERLAKU[modul] and status != \"draft\"#|$T"
 "s3 penawaran: kedaluwarsa turunan tak dibuang|$SGF|s#teks = f\"({teks} AND NOT {sql_kedaluwarsa(p_hari)})\"#teks = teks#|$T"
 "s4 proforma: expired ikut lagi|$SGF|s#\"proforma\": (\"cancelled\", \"expired\"),#\"proforma\": (\"cancelled\",),#|$T"
 "s5 SO: batal ikut lagi|$SPF|s#for r in rows if berlaku(\"sales_order\", r\[\"status\"\]))#for r in rows)#|$T"
 "s6 biaya: lepas dari penentu|$EXR|s#{SG.sql_berlaku('expense', 'expenses.status')}#TRUE#|$T"
 "s7 penawaran: hari tak diteruskan|$QUF|s#\*params, hari_ini))}#*params))}#|$T"
)
