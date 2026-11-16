# Konfig gerbang terfokus Daftar Harga DISKON % per baris (BUG-253, 16 Nov 2026): resolve `diskon_persen` dari baris YANG SAMA dengan harga (tingkat qty + berjangka ikut pilih_baris),
# impor/ekspor 251 kolom "Diskon %". TANPA migrasi (price_list_items.discount_percent sudah ada). Cabang turunan 040 (-> 251 -> 245-r5).
# Nominal diskon dokumen tetap dihitung compute_document + uang_presisi (tak disentuh). Pemisah sabotase = "|", ekspresi sed memakai "@". Tiap sabotase terbukti MERAH lokal (10/10).
W=/root/mh-dh-diskon-253; CABANG=feat/daftarharga-diskon-253; NAMA_JENDELA=daftar-harga-diskon
POLA_UNIT='harga_jual|impor_harga|dh_diskon|dh_impor|dh_harga_minimum|daftar_harga|dh_berjangka|peringatan_207|izin_rute|satuan_dasar'
HARNESS=scripts/ops/uji-salinan-daftar-harga-diskon.sh
PENANDA_BERKAS=services/impor_harga.py; PENANDA='async def rencana('
A=backend/api_gateway/app
T53=tests/unit/test_dh_diskon_253.py
T51=tests/unit/test_dh_impor_251.py
SAB=(
 "d1 resolve tanpa diskon|$A/services/harga_jual.py|s@diskon_persen=float(r\[\"discount_percent\"\]) if r\[\"discount_percent\"\] is not None else None@diskon_persen=None@|$T53"
 "d2 tak diatur jadi 0|$A/services/harga_jual.py|s@else None)  \# BUG-253@else 0.0)  \# BUG-253@|$T53"
 "d3 SELECT tanpa discount_percent|$A/services/harga_jual.py|s@min_price, discount_percent, min_quantity@min_price, min_quantity@|$T51"
 "d4 diskon lama tak dipertahankan|$A/services/impor_harga.py|s@else diskon_lama   \# kolom@else None   \# kolom@|$T51"
 "d5 batas diskon longgar|$A/services/impor_harga.py|s@diskon > 100@diskon > 1000@|$T51"
 "d6 sama abaikan diskon|$A/services/impor_harga.py|s@ and diskon == diskon_lama and (@ and (@|$T51"
 "d7 diskon tak diteruskan ke penulis|$A/services/impor_harga.py|s@discount_percent=diskon, start_date=nilai\[\"mulai\"\]@start_date=nilai[\"mulai\"]@|$T51"
 "d8 ekspor tanpa diskon|$A/services/impor_harga.py|s@Decimal(str(r\[\"discount_percent\"\])) if r\[\"discount_percent\"\] is not None else None, r\[\"start_date\"\]@None, r[\"start_date\"]@|$T51"
 "d9 rencana tanpa diskon_persen|$A/services/impor_harga.py|s@\"harga_minimum\": minimum, \"diskon_persen\": diskon,@\"harga_minimum\": minimum,@|$T51"
 "d10 diskon negatif lolos|$A/services/impor_harga.py|s@diskon < 0 or @@|$T51"
)
