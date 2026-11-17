# Konfig gerbang GABUNGAN paket DH 3 (17 Nov 2026): galat berkode seragam + BUG-254 saringan/batas 500/barang-pilihan. SATU gerbang, SATU rilis. Cabang LINEAR di atas master 151e0d602
# (paket-be-all, V502 merek sudah ada); TANPA migrasi baru. Sabotase = gabungan daftar-harga-galat.sh (g1-g5) + daftar-harga-filter.sh (n1-n12); harness = salin + filter (UKUR 500 barang).
W=/root/mh-dh-paket3; CABANG=feat/daftarharga-paket3; NAMA_JENDELA=daftar-harga-paket3
POLA_UNIT='dh_galat_seragam|dh_salin|dh_riwayat|dh_filter|harga_filter|daftar_harga|bulk_price|bulk_f1|dh_berjangka|barang_pilihan|cari_kata|items|izin_rute'
HARNESS=scripts/ops/uji-salinan-daftar-harga-paket3.sh
PENANDA_BERKAS=services/harga_filter.py; PENANDA='async def resolusi('
A=backend/api_gateway/app
B=$A/routers/bulk_price_lists.py
TG=tests/unit/test_dh_galat_seragam.py; TF=tests/unit/test_dh_filter_254.py
SAB=(
 "g1 BULK_KUNCI_WAJIB kembali objek di detail|$B|s@raise GalatBerkode(400, \"BULK_KUNCI_WAJIB\", \"Aksi massal wajib membawa header X-Idempotency-Key.\")@raise HTTPException(status_code=400, detail={\"code\": \"BULK_KUNCI_WAJIB\", \"message\": \"Aksi massal wajib membawa header X-Idempotency-Key.\"})@|$TG"
 "g2 SALIN_KUNCI_WAJIB kembali objek di detail|$B|s@raise GalatBerkode(400, \"SALIN_KUNCI_WAJIB\", \"Salin daftar wajib membawa header X-Idempotency-Key.\")@raise HTTPException(status_code=400, detail={\"code\": \"SALIN_KUNCI_WAJIB\", \"message\": \"Salin daftar wajib membawa header X-Idempotency-Key.\"})@|$TG"
 "g3 riwayat aksi tak dikenal tanpa field|$B|s@, field=\"aksi\")@)@|$TG"
 "g4 balapan kode jadi 500|$B|s@raise GalatBerkode(409, \"KODE_DAFTAR_GANDA\", \"Kode daftar harga itu sudah dipakai.\", field=\"code\")@raise@|$TG"
 "g5 status BULK_KUNCI_WAJIB bukan 400|$B|s@GalatBerkode(400, \"BULK_KUNCI_WAJIB\"@GalatBerkode(422, \"BULK_KUNCI_WAJIB\"@|$TG"
 "n1 batas harga kembali 50|$A/services/bulk.py|s@\"harga\": 500}@\"harga\": 50}@|$TF"
 "n2 item_ids tak lagi menang|$A/routers/bulk_price_lists.py|s@if body.item_ids:@if False:@|$TF"
 "n3 payload id terselesaikan dibuang|$A/routers/bulk_price_lists.py|s@data\[\"payload\"\] =@data[\"payload_x\"] =@|$TF"
 "n4 resolusi tanpa saring tenant|$A/services/harga_filter.py|s@WHERE p.tenant_id = \$1 AND p.deleted_at IS NULL{s}@WHERE p.deleted_at IS NULL{s}@|$TF"
 "n5 di_daftar=false tak membalik|$A/services/harga_filter.py|s@'' if di_daftar else 'NOT '@'' if di_daftar else ''@|$TF"
 "n6 kategori sensitif huruf|$A/services/harga_filter.py|s@lower(p.kategori) = ANY@p.kategori = ANY@|$TF"
 "n7 merek selalu disentuh|$A/services/harga_filter.py|s@    if f\[\"merek\"\]:@    if True:@|$TF"
 "n8 filter kosong bukan blok|$A/routers/bulk_price_lists.py|s@FILTER_TAK_ADA_BARANG@FILTER_X@|$TF"
 "n9 batas nilai saringan longgar|$A/services/harga_filter.py|s@BATAS_FILTER = 50@BATAS_FILTER = 5000@|$TF"
 "n10 pemasok tak divalidasi|$A/services/harga_filter.py|s@f\[\"pemasok\"\].append(str(UUID(p)))@f[\"pemasok\"].append(p)@|$TF"
 "n11 pilihan tanpa helper items|$A/routers/bulk_price_lists.py|s@from .items import _syarat_cari@from .items import syarat_kata as _syarat_cari@|$TF"
 "n12 pemasok pilihan tanpa saring tenant|$A/routers/bulk_price_lists.py|s@WHERE p.tenant_id = \$1 AND p.deleted_at IS NULL GROUP BY v.id, v.name@WHERE p.deleted_at IS NULL GROUP BY v.id, v.name@|$TF"
)
