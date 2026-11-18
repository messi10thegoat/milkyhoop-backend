# Konfig gerbang terfokus pajak-pilihan (18 Nov 2026): GET /items/pajak-pilihan dari SATU penentu pajak_sah (= penulis nama_pajak_item). Tanpa migrasi.
# Pemisah sabotase = "|", sed memakai "@".
W=/root/mh-pajak; CABANG=feat/pajak-pilihan; NAMA_JENDELA=pajak-pilihan
POLA_UNIT='pajak_pilihan|pajak|items|izin_rute'
HARNESS=scripts/ops/uji-salinan-pajak-pilihan.sh
PENANDA_BERKAS=services/pajak_sah.py; PENANDA='def daftar_pajak('
A=backend/api_gateway/app
T=tests/unit/test_pajak_pilihan.py
SAB=(
 "p1 arah tak disaring|$A/services/pajak_sah.py|s@    if tc\[\"direction\"\] and tc\[\"direction\"\] != arah:@    if False:@|$T"
 "p2 nonaktif lolos|$A/services/pajak_sah.py|s@    if not tc\[\"is_active\"\]:@    if False:@|$T"
 "p3 tarif digandakan di text|$A/services/pajak_sah.py|s@nama if (t == \"0\" or f\"{t}%\" in nama) else@nama if t == \"0\" else@|$T"
 "p4 daftar tak menyaring tenant|$A/services/pajak_sah.py|s@WHERE tenant_id = \$1 AND is_active = true ORDER BY@WHERE is_active = true ORDER BY@|$T"
 "p5 jenis tak dikenal lolos|$A/routers/items.py|s@    if jenis not in PS.JENIS:@    if False:@|$T"
 "p6 penulis tak memakai penentu|$A/routers/items.py|s@salah = PS.alasan_tolak(arah, dict(tc) if tc else None)@salah = None if tc else \"tidak ditemukan\"@|$T"
 "p7 arah jenis terbalik|$A/services/pajak_sah.py|s@JENIS = {\"penjualan\": \"output\", \"pembelian\": \"input\"}@JENIS = {\"penjualan\": \"input\", \"pembelian\": \"output\"}@|$T"
)
