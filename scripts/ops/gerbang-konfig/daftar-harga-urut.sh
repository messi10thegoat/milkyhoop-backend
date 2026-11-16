# Konfig gerbang terfokus URUTAN baris GET detail daftar harga (16 Nov 2026, permintaan FE berjangka): ORDER BY ... pli.min_quantity, pli.start_date NULLS FIRST, pli.id.
# Cabang turunan 253 (feat/daftarharga-urut-start); satu baris kode + tes, tanpa migrasi. Harness = harness berjangka 245 (regresi baca/tulis daftar di salinan; gerbang-unit.sh mewajibkan HARNESS); urutan SQL sendiri diuji lewat tes sumber + simulasi.
W=/root/mh-dh-urut; CABANG=feat/daftarharga-urut-start; NAMA_JENDELA=daftar-harga-urut
POLA_UNIT='price_lists|daftar_harga|dh_urut|dh_berjangka'
HARNESS=scripts/ops/uji-salinan-daftar-harga-berjangka.sh
PENANDA_BERKAS=routers/price_lists.py; PENANDA='async def get_price_list('
A=backend/api_gateway/app
T=tests/unit/test_dh_urut_start.py
SAB=(
 "u1 tanpa start_date|$A/routers/price_lists.py|s@pli.min_quantity, pli.start_date NULLS FIRST, pli.id@pli.min_quantity, pli.id@|$T"
 "u2 start_date NULLS LAST|$A/routers/price_lists.py|s@pli.start_date NULLS FIRST@pli.start_date NULLS LAST@|$T"
 "u3 tanpa pemutus id|$A/routers/price_lists.py|s@pli.start_date NULLS FIRST, pli.id@pli.start_date NULLS FIRST@|$T"
)
