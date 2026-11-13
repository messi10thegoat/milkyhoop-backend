# Konfig gerbang terfokus Daftar Harga D1+D2 (13 Nov 2026): ?aksi=/summary/aksi_tersedia/unit_text/berlaku_text, pratinjau status/bawaan/hapus/ubah/hapus baris, penjaga hapus dipakai pelanggan, barang tidak dijual, idempotensi, izin rute. Tanpa migrasi.
# Pemisah sabotase = "|", ekspresi sed memakai "@".
W=/root/mh-dh-d1; CABANG=feat/daftarharga-d1; NAMA_JENDELA=daftar-harga-d2
POLA_UNIT='harga_daftar|harga_aksi|price_lists|harga_jual|permission_middleware|aksi_tersedia|daftar_harga'
HARNESS=scripts/ops/uji-salinan-daftar-harga-d2.sh
PENANDA_BERKAS=services/harga_aksi.py; PENANDA='async def terapkan_status('
A=backend/api_gateway/app
T1=tests/unit/test_daftar_harga_d1.py
T2=tests/unit/test_daftar_harga_d2.py
SAB=(
 "s1 summary predikat beda dari ?aksi=|$A/routers/price_lists.py|s@FILTER (WHERE {HD.sql_aksi(k, 'p', '\$2')})@FILTER (WHERE {HD.sql_aksi(k, 'p', '\$2')} AND p.is_active)@|$T1"
 "s2 predikat bawaan longgar|$A/services/harga_daftar.py|s@{a}.is_default AND {a}.is_active)@{a}.is_default)@|$T1"
 "s3 urutan daftar tak stabil|$A/routers/price_lists.py|s@ORDER BY p.priority, p.name, p.id@ORDER BY p.priority, p.name@|$T1"
 "s4 hapus selalu tersedia|$A/services/harga_daftar.py|s@aksi(n == 0,@aksi(True,@|$T1"
 "s5 berlaku_text kosong tak ikut daftar|$A/services/harga_daftar.py|s@return \"Ikut daftar\"@return \"\"@|$T1"
 "s6 unit_text tanpa satuan dasar|$A/services/harga_daftar.py|s@ or \"Satuan dasar\"@ or \"X\"@|$T1"
 "s7 penjaga pelanggan dicabut|$A/services/harga_aksi.py|s@    if n_pelanggan:@    if False:@|$T2"
 "s8 peringatan nonaktif jadi blok|$A/services/harga_aksi.py|s@return _hasil(\[\], {\"dari\"@return _hasil(per, {\"dari\"@|$T2"
 "s9 bawaan lama tak dicabut|$A/services/harga_aksi.py|s@SET is_default = false, updated_at = NOW() WHERE tenant_id@SET is_default = true, updated_at = NOW() WHERE tenant_id@|$T2"
 "s10 nonaktif tak mencabut bawaan|$A/services/harga_aksi.py|s@is_active = false, is_default = false,@is_active = false,@|$T2"
 "s11 barang tidak dijual lolos|$A/services/harga_aksi.py|s@b\[\"status\"\] != \"active\" or b\[\"for_sales\"\] is False@False@|$T2"
 "s12 izin pratinjau bawaan dicabut|$A/middleware/permission_middleware.py|s@price-lists/\[^/\]+/default/preview@price-lists/\[^/\]+/default/previewX@|$T2"
 "s13 izin pratinjau hapus baris dicabut|$A/middleware/permission_middleware.py|s@price-lists/\[^/\]+/items/\[^/\]+/delete/preview@price-lists/\[^/\]+/items/\[^/\]+/delete/previewX@|$T2"
 "s14 idempotensi hapus dicabut|$A/routers/price_lists.py|s@\"DH_HAPUS\"@\"DH_HAPUS_X\"@|$T2"
 "s15 idempotensi ubah baris dicabut|$A/routers/price_lists.py|s@\"DH_BARIS_UBAH\"@\"DH_BARIS_UBAH_X\"@|$T2"
 "s16 rute summary hilang|$A/routers/price_lists.py|s#@router.get(\"/summary\")#@router.get(\"/summary_x\")#|$T1"
 "s17 barang dijual dicabut dari penulis|$A/routers/price_lists.py|s@await HA.periksa_barang_dijual(conn, tid, pid)@pass@|$T2"
 "s18 penjaga hapus dicabut dari penulis|$A/routers/price_lists.py|s@HA.tolak_bila_blok(HA.rencana_hapus(r,@(HA.rencana_hapus(r,@|$T2"
 "s19 kode ganda tak diperiksa|$A/routers/price_lists.py|s@HA.kode_dipakai(conn, ctx\[\"tenant_id\"\], kode, price_list_id)@False@|$T2"
 "s20 aksi_tersedia hilang dari detail|$A/routers/price_lists.py|s@\"aksi_tersedia\": HD.aksi_tersedia(row, n_pel),@@|$T1"
 "s21 pratinjau tak baca-saja|$A/routers/price_lists.py|s@readonly=True@readonly=False@|$T2"
)
