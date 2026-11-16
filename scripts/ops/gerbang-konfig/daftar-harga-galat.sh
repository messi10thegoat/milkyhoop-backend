# Konfig gerbang terfokus SERAGAM GALAT berkode Daftar Harga (17 Nov 2026, putusan MASTER): BULK_KUNCI_WAJIB (D3), SALIN_KUNCI_WAJIB, RIWAYAT_AKSI_TIDAK_DIKENAL, race KODE_DAFTAR_GANDA (UniqueViolation)
# = GalatBerkode (code + message + field di TINGKAT ATAS), bukan objek di dalam `detail`. Cabang turunan master LIVE 835f44f60; tanpa migrasi; harness = harness salin (S3b memakai galat berkode).
W=/root/mh-dh-galat; CABANG=feat/daftarharga-galat-seragam; NAMA_JENDELA=daftar-harga-galat
POLA_UNIT='dh_galat_seragam|dh_salin|dh_riwayat|daftar_harga_d3|bulk_price|bulk_f1|izin_rute'
HARNESS=scripts/ops/uji-salinan-daftar-harga-salin.sh
PENANDA_BERKAS=routers/bulk_price_lists.py; PENANDA='GalatBerkode(400, "SALIN_KUNCI_WAJIB"'
A=backend/api_gateway/app
B=$A/routers/bulk_price_lists.py
T=tests/unit/test_dh_galat_seragam.py
SAB=(
 "g1 BULK_KUNCI_WAJIB kembali objek di detail|$B|s@raise GalatBerkode(400, \"BULK_KUNCI_WAJIB\", \"Aksi massal wajib membawa header X-Idempotency-Key.\")@raise HTTPException(status_code=400, detail={\"code\": \"BULK_KUNCI_WAJIB\", \"message\": \"Aksi massal wajib membawa header X-Idempotency-Key.\"})@|$T"
 "g2 SALIN_KUNCI_WAJIB kembali objek di detail|$B|s@raise GalatBerkode(400, \"SALIN_KUNCI_WAJIB\", \"Salin daftar wajib membawa header X-Idempotency-Key.\")@raise HTTPException(status_code=400, detail={\"code\": \"SALIN_KUNCI_WAJIB\", \"message\": \"Salin daftar wajib membawa header X-Idempotency-Key.\"})@|$T"
 "g3 riwayat aksi tak dikenal tanpa field|$B|s@, field=\"aksi\")@)@|$T"
 "g4 balapan kode jadi 500|$B|s@raise GalatBerkode(409, \"KODE_DAFTAR_GANDA\", \"Kode daftar harga itu sudah dipakai.\", field=\"code\")@raise@|$T"
 "g5 status BULK_KUNCI_WAJIB bukan 400|$B|s@GalatBerkode(400, \"BULK_KUNCI_WAJIB\"@GalatBerkode(422, \"BULK_KUNCI_WAJIB\"@|$T"
)
