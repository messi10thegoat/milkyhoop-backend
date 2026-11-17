# Konfig gerbang terfokus BUG-289 Gudang country_text (17 Nov 2026): teks negara siap tampil dari server (kode ISO "ID" -> "Indonesia") di daftar, detail, dan data sesudah tulis. ADITIF, tanpa migrasi.
# Pemisah sabotase = "|", sed memakai "@".
W=/root/mh-gudang-country; CABANG=feat/gudang-country-text; NAMA_JENDELA=gudang-country
POLA_UNIT='gudang_country|gudang_g3|gudang_k|warehouse|izin_rute'
HARNESS=scripts/ops/uji-salinan-gudang.sh
PENANDA_BERKAS=services/gudang_daftar.py; PENANDA='def negara_text('
A=backend/api_gateway/app
T=tests/unit/test_gudang_country_text.py
SAB=(
 "c1 kode ID tak jadi Indonesia|$A/services/gudang_daftar.py|s@NEGARA = {\"ID\": \"Indonesia\"@NEGARA = {\"ID\": \"ID\"@|$T"
 "c2 kode peka huruf|$A/services/gudang_daftar.py|s@NEGARA.get(t.upper(), t)@NEGARA.get(t, t)@|$T"
 "c3 kosong bukan None|$A/services/gudang_daftar.py|s@    if not t:@    if False:@|$T"
 "c4 baca tak mengisi country_text|$A/routers/warehouses.py|s@country_text=GD.negara_text(row\[\"country\"\])@country_text=None@|$T"
 "c5 skema tanpa medan country_text|$A/schemas/warehouses.py|s@    country_text: Optional\[str\] = None@    country_x: Optional[str] = None@|$T"
 "c6 kode dua huruf bukan kapital di peta|$A/services/gudang_daftar.py|s@\"MY\": \"Malaysia\"@\"my\": \"Malaysia\"@|$T"
)
