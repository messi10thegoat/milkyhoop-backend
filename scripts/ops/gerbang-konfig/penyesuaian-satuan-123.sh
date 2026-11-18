# Konfig gerbang BUG-123 (18 Nov 2026): Penyesuaian Stok bersatuan besar (unit per baris, konversi server lewat satuan_dasar, blok SATUAN_TAK_TERKONVERSI + field, teks campur). TANPA migrasi.
# Pemisah sabotase = "|", sed memakai "@".
W=/root/mh-sat; CABANG=feat/penyesuaian-satuan-123; NAMA_JENDELA=s123
POLA_UNIT='stok_sesuai|stock_adjust|penyesuaian|bug123|bug261|bayar_keluar|satuan_dasar|izin_rute|kasbank'
HARNESS=scripts/ops/uji-salinan-satuan-123.sh
PENANDA_BERKAS=services/stok_sesuai.py; PENANDA='async def _konversi'
A=backend/api_gateway/app
T=tests/unit/test_bug123_penyesuaian_satuan.py
SAB=(
 "s1 jumlah tak dikonversi|$A/services/stok_sesuai.py|s@        ubah = hq\[\"qty_dasar\"\]@        ubah = _d(item.quantity_adjustment)@|$T"
 "s2 fisik hitung ulang tak dikonversi|$A/services/stok_sesuai.py|s@fisik, sistem = hf\[\"qty_dasar\"\], sebelum@fisik, sistem = _d(item.physical_quantity), sebelum@|$T"
 "s3 satuan tanpa konversi lolos|$A/services/stok_sesuai.py|s@        if hq\[\"blok\"\]:@        if False:@|$T"
 "s4 field blok hilang|$A/services/stok_sesuai.py|s@field=f\"items\[{idx - 1}\].unit\")@field=None)@|$T"
 "s5 teks campur jumlah hilang|$A/services/stok_sesuai.py|s@\"quantity_adjustment_campur_text\": TLP.campur(@\"quantity_adjustment_campur_text\": None and TLP.campur(@|$T"
 "s6 masukan asli tak dicatat|$A/services/stok_sesuai.py|s@\"unit_input\": b.get(\"unit_masuk\")@\"unit_input\": None@|$T"
 "s7 galat tulis tanpa field|$A/services/bayar_keluar_rencana.py|s@raise GalatBerkode(status, code, detail, field)@raise GalatBerkode(status, code, detail)@|$T"
 "s8 konversi campur tanpa saring tenant|$A/services/stok_sesuai.py|s@FROM unit_conversions WHERE tenant_id = \$1 AND product_id = ANY(\$2::uuid\[\]) \"@FROM unit_conversions WHERE \$1::text IS NOT NULL AND product_id = ANY(\$2::uuid[]) \"@|$T"
)
