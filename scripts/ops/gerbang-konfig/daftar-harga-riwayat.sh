# Konfig gerbang terfokus Daftar Harga RIWAYAT perubahan (BUG-252, 16 Nov 2026): services/harga_riwayat.py (penulis + pembaca audit_logs immutable), hook di tulis/ubah/hapus
# baris, header, status, bawaan, hapus daftar, impor 251, ubah massal D3, GET /{id}/riwayat. TANPA migrasi (indeks idx_audit_entity ada; audit_logs 6 MB). Cabang turunan urut-start (-> 253 -> 040 -> 251 -> 245-r5).
# Pemisah sabotase = "|", ekspresi sed memakai "@". Tiap sabotase terbukti MERAH lokal pada tes unit (14/14).
W=/root/mh-dh-riwayat-252; CABANG=feat/daftarharga-riwayat-252; NAMA_JENDELA=daftar-harga-riwayat
POLA_UNIT='harga_riwayat|dh_riwayat|impor_harga|dh_impor|bulk_price|daftar_harga|dh_berjangka|dh_diskon|dh_harga_minimum|bulk_f1|izin_rute|audit'
HARNESS=scripts/ops/uji-salinan-daftar-harga-riwayat.sh
PENANDA_BERKAS=services/harga_riwayat.py; PENANDA='async def catat('
A=backend/api_gateway/app
T=tests/unit/test_dh_riwayat_252.py
SAB=(
 "r1 audit tanpa entity_id daftar|$A/services/harga_riwayat.py|s@\$3::uuid, \$4, \$5, 'api:price_list'@NULL::uuid, \$4, \$5, 'api:price_list'@|$T"
 "r2 daftar tanpa saring entity|$A/services/harga_riwayat.py|s@a.entity_type = \$2 AND a.entity_id = \$3::uuid@a.entity_type = \$2@|$T"
 "r3 urutan tertua dulu|$A/services/harga_riwayat.py|s@ORDER BY a.\"createdAt\" DESC, a.id DESC@ORDER BY a.\"createdAt\" ASC, a.id ASC@|$T"
 "r4 ubah baris tanpa nilai lama|$A/routers/price_lists.py|s@dari = await HR.snapshot(conn, ctx\[\"tenant_id\"\], line_id)@dari = None@|$T"
 "r5 hapus baris tanpa nilai lama|$A/routers/price_lists.py|s@dari = await HR.snapshot(conn, tid, item_id)@dari = None@|$T"
 "r6 batas potong longgar|$A/services/harga_riwayat.py|s@MAKS_BARIS = 200@MAKS_BARIS = 2000@|$T"
 "r7 impor mencatat baris tetap|$A/services/impor_harga.py|s@ if x\[\"aksi\"\] != \"tetap\"\]@]@|$T"
 "r8 replay dicatat ulang|$A/routers/bulk_price_lists.py|s@ or it\[\"replay\"\] or not it.get(\"result\")@ or not it.get(\"result\")@|$T"
 "r9 hapus daftar tanpa jumlah baris|$A/routers/price_lists.py|s@n_baris = int(await HA.ukur_baris(@n_baris = 0 * int(await HA.ukur_baris(@|$T"
 "r10 teks awal riwayat salah|$A/services/harga_riwayat.py|s@Riwayat tercatat sejak {tanggal}@Riwayat sejak {tanggal}@|$T"
 "r11 aksi riwayat tak divalidasi|$A/routers/bulk_price_lists.py|s@if aksi is not None and aksi not in HR.AKSI_TEKS:@if False:@|$T"
 "r12 pelaku tanpa username|$A/services/harga_riwayat.py|s@r\[\"nama\"\] or r\[\"username\"\]@r[\"nama\"]@|$T"
 "r13 izin baca riwayat dibuang|$A/middleware/permission_middleware.py|s@riwayat\$\", \[\"GET\"\]@riwayatx\$\", [\"GET\"]@|$T"
 "r14 tambah baris tak dicatat|$A/routers/price_lists.py|s@\"TAMBAH_BARIS\", HR.ringkas_baris(None, ke)@\"UBAH_BARIS\", HR.ringkas_baris(None, ke)@|$T"
)
