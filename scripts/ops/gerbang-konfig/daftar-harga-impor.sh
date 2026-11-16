# Konfig gerbang terfokus Daftar Harga IMPOR/EKSPOR Excel (BUG-251, 16 Nov 2026): services/impor_harga.py, rute /export + /import/{baca,preview,''}, izin + inventaris, impor_barang.baca_xlsx diparametri.
# Cabang turunan 245-r5 (kunci baris memuat tanggal mulai); TANPA migrasi baru (harness memasang V500 di salinan).
# Pemisah sabotase = "|", ekspresi sed memakai "@". Tiap sabotase sudah dibuktikan MERAH lokal pada tes unit (12/12).
W=/root/mh-dh-impor-251; CABANG=feat/daftarharga-impor-251; NAMA_JENDELA=daftar-harga-impor
POLA_UNIT='impor_harga|impor_barang|price_lists|harga_daftar|daftar_harga|dh_impor|dh_berjangka|bulk_f1|izin_rute|bug101|satuan_dasar'
HARNESS=scripts/ops/uji-salinan-daftar-harga-impor.sh
PENANDA_BERKAS=services/impor_harga.py; PENANDA='async def rencana('
A=backend/api_gateway/app
T=tests/unit/test_dh_impor_251.py
SAB=(
 "s1 satuan dasar tak disamakan|$A/services/impor_harga.py|s@kanon = SD.kanon(satuan or brg\[\"dasar\"\], alias)@kanon = SD.kanon(satuan or \"\", alias)@|$T"
 "s2 kunci tanpa tanggal mulai|$A/services/impor_harga.py|s@Decimal(str(qty)).normalize(), mulai)@Decimal(str(qty)).normalize(), None)@|$T"
 "s3 cocok abaikan tanggal mulai|$A/services/impor_harga.py|s@ and a\[\"start_date\"\] == nilai\[\"mulai\"\]), None)@), None)@|$T"
 "s4 tak atomik|$A/services/impor_harga.py|s@\"ok\": not bloks and not ditolak@\"ok\": not bloks@|$T"
 "s5 hitungan tulis salah|$A/services/impor_harga.py|s@n\[row\[\"aksi\"\]\] += 1@n[row[\"aksi\"]] += 0@|$T"
 "s6 tanpa kunci baris|$A/services/impor_harga.py|s@{' FOR UPDATE' if kunci else ''}@@|$T"
 "s7 audit dibuang|$A/services/impor_harga.py|s@await bulk.catat_audit(@_ = (@|$T"
 "s8 kunci idempoten opsional|$A/routers/price_lists.py|s@IMPOR_KUNCI_WAJIB@KUNCI_OPSIONAL@|$T"
 "s9 harga negatif lolos|$A/services/impor_harga.py|s@elif harga < 0:@elif False:@|$T"
 "s10 ekspor satuan kosong|$A/services/impor_harga.py|s@HD.unit_text(r\[\"unit\"\], dasar)@r[\"unit\"]@|$T"
 "s11 peringatan HPP dibuang|$A/services/impor_harga.py|s@nilai\[\"harga\"\] < b_hpp\[0\]\[\"hpp\"\]@False@|$T"
 "s12 izin impor dibuang|$A/middleware/permission_middleware.py|s@/import(/baca|/preview)?@/importx(/baca|/preview)?@|$T"
)
