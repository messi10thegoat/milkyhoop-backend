# Konfig gerbang terfokus Barang & Jasa P3 (13 Nov 2026): keadaan, pratinjau status/hapus/duplikat/kategori/ubah, bulk status, idempotensi, audit. Tanpa migrasi.
# Pemisah sabotase = "|", ekspresi sed memakai "@".
W=/root/mh-barang-p3-r3; CABANG=feat/barang-p3-pratinjau-r3; NAMA_JENDELA=barang-p3
POLA_UNIT='barang_aksi|barang_daftar|bulk_items|routers.items|app.routers import items|update_item|delete_item|duplicate_item|create_category|delete_category|tolak_ubah_dasar|tolak_ubah_konversi|satuan_dasar|permission_middleware|AKSI_DIIZINKAN|stock_transfer_items'
HARNESS=scripts/ops/uji-salinan-barang-p3.sh
PENANDA_BERKAS=services/barang_aksi.py; PENANDA='async def terapkan_status('
A=backend/api_gateway/app
T=tests/unit/test_barang_p3.py
SAB=(
 "s1 keadaan selalu aktif|$A/services/barang_aksi.py|s@return \"aktif\" if b.get(\"status\", \"active\") == \"active\" else \"nonaktif\"@return \"aktif\"@|$T"
 "s2 peringatan stok dicabut|$A/services/barang_aksi.py|s@if dilacak(b) and s != 0:@if False:@|$T"
 "s3 peringatan pesanan terbuka dicabut|$A/services/barang_aksi.py|s@        if pesanan_terbuka:@        if False:@|$T"
 "s4 sudah-pada-keadaan tak diblok|$A/services/barang_aksi.py|s@    if dari == ke:@    if False:@|$T"
 "s5 peringatan jadi blok|$A/services/barang_aksi.py|s@_hasil(\[\], rencana, per)@_hasil(per, rencana, \[\])@|$T"
 "s6 pratinjau status mengaku berjurnal|$A/services/barang_aksi.py|s@\"jurnal\": False}@\"jurnal\": True}@|$T"
 "s7 hapus berjejak diizinkan|$A/services/barang_aksi.py|s@    if rinci:@    if False:@|$T"
 "s8 kategori terpakai lolos|$A/services/barang_aksi.py|s@    if dipakai:@    if False:@|$T"
 "s9 duplikat tak sebut harga bertingkat|$A/services/barang_aksi.py|s@    if n_harga:@    if False:@|$T"
 "s10 daftar jejak kehilangan transfer|$A/services/barang_aksi.py|\\@(\"stock_transfer_items\", \"item_id\", None),@d|$T"
 "s11 duplikat tak menyalin price_list_items|$A/routers/items.py|s@FROM price_list_items pli JOIN@FROM price_list_items_x pli JOIN@|$T"
 "s12 duplikat tanpa kode unik|$A/routers/items.py|s@kode_baru = await _kode_otomatis(conn, tenant_id, item_row.get(\"item_type\", \"goods\"))@kode_baru = None@|$T"
 "s13 aksi_tersedia kehilangan kunci ubah|$A/services/barang_daftar.py|\\@\"ubah\": {\"tersedia\": True, \"alasan\": None},@d|$T"
 "s14 izin pratinjau hapus dicabut|$A/middleware/permission_middleware.py|s@items/\[^/\]+/delete/preview@items/\[^/\]+/delete/previewX@|$T"
 "s15 izin pratinjau ubah dicabut|$A/middleware/permission_middleware.py|s@items/\[^/\]+/edit/preview@items/\[^/\]+/edit/previewX@|$T"
 "s16 izin bulk status dicabut|$A/middleware/permission_middleware.py|s@items/bulk/(status@items/bulk/(statusX@|$T"
 "s17 bulk tanpa kunci idempotensi wajib|$A/routers/bulk_items.py|s@    if not kunci:@    if False:@|$T"
 "s18 pratinjau bulk tak baca-saja|$A/routers/bulk_items.py|s@baca_saja=True@baca_saja=False@|$T"
 "s19 status bulk keluar daftar putih|$A/services/bulk.py|s@\"share\", \"status\"@\"share\"@|$T"
 "s20 riwayat status tak ditulis|$A/services/barang_aksi.py|s@\"activated\" if ke_aktif else \"deactivated\"@\"status_x\"@|$T"
 "s21 pemeriksa ubah dicabut dari PUT|$A/routers/items.py|s@        old_item = await _periksa_ubah(conn, ctx, item_id, body)@        old_item = None@|$T"
 "s22 pratinjau ubah tanpa konversi|$A/routers/items.py|s@            await _periksa_konversi(conn, ctx, item_id, b)@            pass@|$T"
 "s23 audit hapus dicabut|$A/routers/items.py|s@await BA.catat_hapus(@BA.catat_hapus(@|$T"
 "s24 audit duplikat dicabut|$A/routers/items.py|s@await BA.catat_duplikat(@BA.catat_duplikat(@|$T"
 "s25 idempotensi PUT dicabut|$A/routers/items.py|s@\"ITEM_UPDATE\", str(item_id),@\"ITEM_UPDATE_X\", str(item_id),@|$T"
 "s26 idempotensi hapus dicabut|$A/routers/items.py|s@\"ITEM_DELETE\"@\"ITEM_DELETE_X\"@|$T"
 "s27 keadaan hilang di daftar|$A/routers/items.py|s@keadaan=BA.keadaan(row), keadaan_text=BA.keadaan_text(row),@@|$T"
 "s28 perubahan ubah tak dihitung|$A/services/barang_aksi.py|s@_s(baru\[medan\]) != _s(lama.get(kolom))@False@|$T"
 "s29 duplikat menyalin item_pricing yatim|$A/routers/items.py|s@INSERT INTO price_list_items (price_list_id@INSERT INTO item_pricing (price_list_id@|$T"
 "s30 pratinjau hitung dari item_pricing|$A/routers/items.py|s@SELECT count(\*) FROM price_list_items pli JOIN@SELECT count(\*) FROM item_pricing pli JOIN@|$T"
)
