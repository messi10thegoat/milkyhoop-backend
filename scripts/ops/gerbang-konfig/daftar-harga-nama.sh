# Konfig gerbang terfokus Daftar Harga hapus-nama (15 Nov 2026): blok DH_DIPAKAI_PELANGGAN menyebut nama (maks 5 + dan N lainnya) + jumlah + baris_terhapus. Tanpa migrasi.
# Pemisah sabotase = "|", ekspresi sed memakai "@".
W=/root/mh-dh-nama; CABANG=feat/daftarharga-hapus-nama; NAMA_JENDELA=daftar-harga-nama
POLA_UNIT='harga_aksi|price_lists|harga_daftar|daftar_harga|dh_hapus_nama'
HARNESS=scripts/ops/uji-salinan-daftar-harga-nama.sh
PENANDA_BERKAS=services/harga_aksi.py; PENANDA='async def ukur_nama_pelanggan('
A=backend/api_gateway/app
T=tests/unit/test_dh_hapus_nama.py
SAB=(
 "n1 nama tak disebut di pesan|$A/services/harga_aksi.py|s@) if tampil else \"\"@) if False else \"\"@|$T"
 "n2 batas nama 5 jadi 50|$A/services/harga_aksi.py|s@^MAKS_NAMA = 5@MAKS_NAMA = 50@|$T"
 "n3 sisa tak dihitung|$A/services/harga_aksi.py|s@sisa = max(0, n_pelanggan - len(tampil)) if tampil else 0@sisa = 0@|$T"
 "n4 baris_terhapus dicabut dari blok|$A/services/harga_aksi.py|s@, baris_terhapus=n_baris)\], None)@)], None)@|$T"
 "n5 jumlah_pelanggan dicabut|$A/services/harga_aksi.py|s@jumlah_pelanggan=n_pelanggan, @@|$T"
 "n6 kolom name (kosong) bukan nama|$A/services/harga_aksi.py|s@SELECT nama FROM customers@SELECT name FROM customers@|$T"
 "n7 router tak memakai penentu nama|$A/routers/price_lists.py|s@await HA.ukur_nama_pelanggan(conn, tid, price_list_id)@await HA.ukur_pelanggan(conn, tid, price_list_id)@|$T"
 "n8 penentu nama tanpa urutan|$A/services/harga_aksi.py|s@ORDER BY lower(nama), id LIMIT@LIMIT@|$T"
)
