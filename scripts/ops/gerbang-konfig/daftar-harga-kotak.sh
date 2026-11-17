# Konfig gerbang terfokus Daftar Harga KOTAK PILIHAN (temuan prod kaos 17 Nov 2026): kotak kategori/pemasok/merek_pilihan di GET /{id}/barang-pilihan memakai predikat HF.syarat yang SAMA
# dengan resolusi filter (aktif+dijual; di_daftar=True = barang di daftar) -- dulu menghitung semua barang tak-terhapus ("Bahan Kain (4)" dipilih = 0 barang). Turunan master LIVE d98edcafe; TANPA migrasi.
W=/root/mh-dh-kotak-r2; CABANG=feat/daftarharga-kotak-r2; NAMA_JENDELA=daftar-harga-kotak
POLA_UNIT='dh_filter|harga_filter|daftar_harga|bulk_price|bulk_f1|barang_pilihan|cari_kata|items|izin_rute|dh_galat'
HARNESS=scripts/ops/uji-salinan-daftar-harga-filter.sh
PENANDA_BERKAS=routers/bulk_price_lists.py; PENANDA='kotak pilihan = predikat SAMA dengan resolusi'
A=backend/api_gateway/app
B=$A/routers/bulk_price_lists.py
T=tests/unit/test_dh_filter_254.py
SAB=(
 "k1 kotak kategori tanpa predikat resolusi|$B|s@p.deleted_at IS NULL{sb} AND COALESCE(p.kategori@p.deleted_at IS NULL AND COALESCE(p.kategori@|$T"
 "k2 kotak merek tanpa predikat resolusi|$B|s@p.deleted_at IS NULL{sb} AND COALESCE(p.merek@p.deleted_at IS NULL AND COALESCE(p.merek@|$T"
 "k3 kotak pemasok tanpa predikat resolusi|$B|s@p.deleted_at IS NULL{sb} GROUP BY v.id@p.deleted_at IS NULL GROUP BY v.id@|$T"
 "k4 kotak memakai predikat sekali-saja bukan HF.syarat|$B|s@sb, pb, _ = HF.syarat({\"kategori\": \[\], \"pemasok\": \[\], \"merek\": \[\]}, price_list_id, di_daftar, 2)@sb, pb = \" AND p.status = 'active'\", []@|$T"
 "k5 kotak abaikan di_daftar|$B|s@{\"kategori\": \[\], \"pemasok\": \[\], \"merek\": \[\]}, price_list_id, di_daftar, 2)@{\"kategori\": [], \"pemasok\": [], \"merek\": []}, price_list_id, None, 2)@|$T"
 "k6 kotak pemasok tanpa saring tenant|$B|s@WHERE p.tenant_id = \$1 AND p.deleted_at IS NULL{sb} GROUP BY v.id, v.name@WHERE p.deleted_at IS NULL{sb} GROUP BY v.id, v.name@|$T"
)
