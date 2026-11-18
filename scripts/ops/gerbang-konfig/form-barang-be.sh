# Konfig gerbang FORM BARANG CW BE (18 Nov 2026): stok awal PER GUDANG (satu jurnal + kartu "Saldo awal" per gudang), GET /items/form-bawaan, pratinjau Buat (akun/pajak berlaku, peringatan,
# kategori bawaan) + pratinjau Ubah ber-teks, BUG-290 (batch aktif lagi saat void). TANPA migrasi. Pemisah sabotase "|", sed "@".
W=/root/mh-fb; CABANG=feat/form-barang-be-r2; NAMA_JENDELA=formbrg
POLA_UNIT='saldo_awal|barang_aksi|bawaan_berlaku|form_barang|stok_awal|izin_rute|impor_barang|barang_p3|bug282|kosakata|item_|inventory_helpers|bug286|batch'
HARNESS=scripts/ops/uji-salinan-form-barang.sh
PENANDA_BERKAS=services/stok_awal_barang.py; PENANDA='async def rencana'
A=backend/api_gateway/app
T=tests/unit/test_form_barang_be.py
SAB=(
 "f1 penulis tak memakai inti per gudang|$A/routers/items.py|s@            await catat_saldo_awal_per_gudang(@            await catat_saldo_awal_stok(@|$T"
 "f2 jumlah bersatuan tak dikonversi|$A/services/stok_awal_barang.py|s@qty_dasar = Decimal(str(b.quantity)) \* fk@qty_dasar = Decimal(str(b.quantity)) * 1@|$T"
 "f3 harga per satuan tak dibagi faktor|$A/services/stok_awal_barang.py|s@(harga_satuan / fk).quantize@(harga_satuan / 1).quantize@|$T"
 "f4 gudang sistem lolos|$A/services/stok_awal_barang.py|s@if not g or g\[\"is_system\"\]:@if not g:@|$T"
 "f5 barang batch lolos|$A/services/stok_awal_barang.py|s@    if body.track_batches or body.track_expiry:@    if False:@|$T"
 "f6 usaha berjalan lolos|$A/services/stok_awal_barang.py|s@    if await conn.fetchval(SAS.SQL_SUDAH_BERJALAN, tid):@    if False:@|$T"
 "f7 WAC berjalan antar-gudang hilang|$A/services/saldo_awal_stok.py|s@wac = rate_d if len(rows) == 1 else@wac = rate_d if True else@|$T"
 "f8 sumber kategori hilang|$A/services/bawaan_berlaku.py|s@            r\[\"sumber\"\] = \"kategori\"@            pass@|$T"
 "f9 peringatan kedepan hilang|$A/services/barang_aksi.py|s@        r\[\"peringatan\"\].append(_blok(\"AKUN_PAJAK_BERLAKU_KEDEPAN\", TEKS_KEDEPAN))@        pass@|$T"
 "f10 batch tak aktif lagi saat void|$A/services/inventory_helpers.py|s@UPDATE item_batches SET status = 'active'@UPDATE item_batches SET status = 'depleted'@|$T"
 "f11 gudang tanpa saring tenant|$A/services/stok_awal_barang.py|s@FROM warehouses WHERE tenant_id = \$1 AND id = \$2@FROM warehouses WHERE \$1::text IS NOT NULL AND id = \$2@|$T"
)
