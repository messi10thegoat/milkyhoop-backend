"""Kontrol merah penjaga teks-sumber yang diarahkan ke penentu bersama (syarat MASTER 24 Okt): pada SALINAN pohon, cabut SATU aturan dari
_siapkan_buat_* -> penjaga yang sesuai WAJIB merah. Pemakaian: python3 sabotase_penjaga.py <api_gateway_salinan> <S1|S2|S3|S4>
Mencetak id tes yang wajib gagal."""
import sys

SAB = {
    "S1": ("app/routers/customers.py", "async def _siapkan_buat_pelanggan", "    await tolak_nama_dipakai(conn, tenant_id, body.name)\n", "",
           "tests/unit/test_item_nama_jenis.py::test_buat_ubah_pulihkan_pelanggan_lewat_pemeriksa"),
    "S2": ("app/routers/items.py", "async def _siapkan_buat_barang", "nama_produk = $2", "nama_produk = $9",
           "tests/unit/test_item_nama_jenis.py::test_cek_nama_barang_app_sejajar_indeks_parsial"),
    "S3": ("app/routers/items.py", "async def _siapkan_buat_barang",
           'nama_pajak_item(conn, tenant_id, body.sales_tax_id, "sales_tax_id", "output")', 'body.sales_tax',
           "tests/unit/test_item_field_utuh.py::test_insert_create_memuat_id_pajak_dan_bisa_dikirim_dengan_placeholder_cocok"),
    "S4": ("app/routers/items.py", "async def _siapkan_buat_barang", "await periksa_saldo_awal(", "await (lambda *a: None)(",
           "tests/unit/test_saldo_awal_stok.py::test_rute_items_memakai_service_dalam_transaksi"),
}

akar, kunci = sys.argv[1], sys.argv[2]
berkas, fungsi, lama, baru, tes = SAB[kunci]
p = f"{akar}/{berkas}"
s = open(p).read()
i = s.index(fungsi)
j = s.find("\nasync def ", i + 1)
j = len(s) if j < 0 else j
badan = s[i:j]
if badan.count(lama) != 1:
    sys.exit(f"SABOTASE {kunci} GAGAL DIPASANG: pola {badan.count(lama)}x di {fungsi}")
open(p, "w").write(s[:i] + badan.replace(lama, baru) + s[j:])
print(tes)
