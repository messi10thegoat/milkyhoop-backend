# Konfig gerbang terfokus Daftar Harga FILTER barang (BUG-254, 16 Nov 2026): services/harga_filter.py, D3 bulk filter + payload id terselesaikan, batas barang `harga` 500,
# GET /{id}/barang-pilihan (+ merek_pilihan = products.merek, V502 BACKEND). Cabang turunan 245-r5 (TANPA menyentuh price_lists.py). MIGRASI V502 milik BACKEND:
# apply MASTER SEBELUM kode; harness menambah kolom merek di SALINAN bila V502 tak ada di pohon. Harness juga UKUR 500 barang (waktu pratinjau/tulis, per-barang).
# Pemisah sabotase = "|", ekspresi sed memakai "@". Tiap sabotase terbukti MERAH lokal pada tes unit (12/12).
W=/root/mh-dh-filter-254; CABANG=feat/daftarharga-filter-254; NAMA_JENDELA=daftar-harga-filter
POLA_UNIT='harga_filter|bulk_price|bulk_f1|daftar_harga|dh_filter|dh_berjangka|izin_rute|barang_pilihan|cari_kata|items'
HARNESS=scripts/ops/uji-salinan-daftar-harga-filter.sh
PENANDA_BERKAS=services/harga_filter.py; PENANDA='async def resolusi('
A=backend/api_gateway/app
T=tests/unit/test_dh_filter_254.py
SAB=(
 "n1 batas harga kembali 50|$A/services/bulk.py|s@\"harga\": 500}@\"harga\": 50}@|$T"
 "n2 item_ids tak lagi menang|$A/routers/bulk_price_lists.py|s@if body.item_ids:@if False:@|$T"
 "n3 payload id terselesaikan dibuang|$A/routers/bulk_price_lists.py|s@data\[\"payload\"\] =@data[\"payload_x\"] =@|$T"
 "n4 resolusi tanpa saring tenant|$A/services/harga_filter.py|s@WHERE p.tenant_id = \$1 AND p.deleted_at IS NULL{s}@WHERE p.deleted_at IS NULL{s}@|$T"
 "n5 di_daftar=false tak membalik|$A/services/harga_filter.py|s@'' if di_daftar else 'NOT '@'' if di_daftar else ''@|$T"
 "n6 kategori sensitif huruf|$A/services/harga_filter.py|s@lower(p.kategori) = ANY@p.kategori = ANY@|$T"
 "n7 merek selalu disentuh|$A/services/harga_filter.py|s@    if f\[\"merek\"\]:@    if True:@|$T"
 "n8 filter kosong bukan blok|$A/routers/bulk_price_lists.py|s@FILTER_TAK_ADA_BARANG@FILTER_X@|$T"
 "n9 batas nilai saringan longgar|$A/services/harga_filter.py|s@BATAS_FILTER = 50@BATAS_FILTER = 5000@|$T"
 "n10 pemasok tak divalidasi|$A/services/harga_filter.py|s@f\[\"pemasok\"\].append(str(UUID(p)))@f[\"pemasok\"].append(p)@|$T"
 "n11 pilihan tanpa helper items|$A/routers/bulk_price_lists.py|s@from .items import _syarat_cari@from .items import syarat_kata as _syarat_cari@|$T"
 "n12 pemasok pilihan tanpa saring tenant|$A/routers/bulk_price_lists.py|s@WHERE p.tenant_id = \$1 AND p.deleted_at IS NULL GROUP BY v.id, v.name@WHERE p.deleted_at IS NULL GROUP BY v.id, v.name@|$T"
)
