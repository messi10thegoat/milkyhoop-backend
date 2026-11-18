# Konfig gerbang terfokus "akun sah SATU penentu" (18 Nov 2026): KAK.akun_sah/daftar_akun dipakai penulis kategori + GET /items/accounts/<jenis> + GET /items/categories/akun-pilihan; hpp = hanya COGS. Tanpa migrasi.
# Pemisah sabotase = "|", sed memakai "@".
W=/root/mh-akun-sah; CABANG=feat/akun-sah; NAMA_JENDELA=akun-sah
POLA_UNIT='akun_sah|bug282|kategori|izin_rute'
HARNESS=scripts/ops/uji-salinan-akun-sah.sh
PENANDA_BERKAS=services/kategori_akun.py; PENANDA='def daftar_akun('
A=backend/api_gateway/app
T=tests/unit/test_akun_sah_satu_penentu.py
SAB=(
 "s1 penentu tak menolak header|$A/services/kategori_akun.py|s@ and not a.get(\"is_header\") and tipe_sah(jenis, a)@ and tipe_sah(jenis, a)@|$T"
 "s2 hpp menerima EXPENSE|$A/services/kategori_akun.py|s@\"hpp\": (\"COGS\",)@\"hpp\": (\"COGS\", \"EXPENSE\")@|$T"
 "s3 pembelian tanpa persediaan|$A/services/kategori_akun.py|s@if jenis == \"pembelian\" and _persediaan(a):@if False:@|$T"
 "s4 penulis tak memakai penentu|$A/services/kategori_akun.py|s@if a and akun_sah(j, a):@if a and a[\"is_active\"] and tipe_sah(j, a):@|$T"
 "s5 jenis tak dikenal lolos|$A/routers/items.py|s@    if jenis not in KAK.JENIS:@    if False:@|$T"
 "s6 daftar tak menyaring tenant|$A/services/kategori_akun.py|s@WHERE tenant_id = \$1 AND is_active = true AND NOT COALESCE@WHERE is_active = true AND NOT COALESCE@|$T"
 "s7 endpoint barang hpp salah jenis|$A/routers/items.py|s@_daftar_akun_sah(request, \"hpp\")@_daftar_akun_sah(request, \"pembelian\")@|$T"
)
