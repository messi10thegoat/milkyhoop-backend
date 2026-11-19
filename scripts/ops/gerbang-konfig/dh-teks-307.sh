# Konfig gerbang terfokus BUG-307 BE (19 Nov 2026): min_price_text + diskon_text per baris harga dari server. Tanpa migrasi/jurnal.
# Pemisah sabotase = "|", sed memakai "@".
W=/root/mh-dhteks; CABANG=feat/dh-teks-minimum-diskon; NAMA_JENDELA=dh-teks-307
POLA_UNIT='dh_teks_minimum|harga_daftar|harga_riwayat|daftar_harga|dh_riwayat|dh_harga_minimum|rupiah_satu|izin_rute'
HARNESS=scripts/ops/uji-salinan-dh-teks-307.sh
PENANDA_BERKAS=services/harga_daftar.py; PENANDA='def diskon_text('
A=backend/api_gateway/app
T=tests/unit/test_dh_teks_minimum_diskon_307.py
SAB=(
 "t1 diskon 0 jadi None|$A/services/harga_daftar.py|s@return None if v is None else f\"{float(v):g}%\"@return None if not v else f\"{float(v):g}%\"@|$T"
 "t2 diskon tanpa persen|$A/services/harga_daftar.py|s@else f\"{float(v):g}%\"@else f\"{float(v):g}\"@|$T"
 "t3 minimum teks dibuang|$A/services/harga_daftar.py|s@return None if v is None else rp_text(v)@return None@|$T"
 "t4 detail tanpa diskon_text|$A/routers/price_lists.py|s@\"diskon_text\": HD.diskon_text(i\[\"discount_percent\"\]),@@|$T"
 "t5 respons tulis tanpa teks minimum|$A/services/harga_daftar.py|s@\"min_price_text\": min_price_text(r\[\"min_price\"\]), @@|$T"
 "t6 pemformat ganda di riwayat|$A/services/harga_riwayat.py|s@^async def snapshot@def _rp(v):\n    return str(v)\n\n\nasync def snapshot@|$T"
)
