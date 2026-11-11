# Konfig gerbang terfokus BUG-149 (nomor dokumen SATU aturan; dibaca scripts/ops/gerbang-unit.sh). Tanpa migrasi.
# Pemisah sabotase = "|", ekspresi sed memakai "@" (bukan "|" atau "#" yang dipakai kode).
W=/root/mh-bug149; CABANG=feat/bug149-nomor-dokumen; NAMA_JENDELA=bug149
POLA_UNIT='nomor_dokumen|NOMOR\.|nomor_tagihan|create_bill|create_purchase_order|create_invoice|create_sales_order|create_quote|update_sales_order|update_invoice|bills_service|idempotensi'
HARNESS=scripts/ops/uji-salinan-bug149.sh
PENANDA_BERKAS=services/nomor_dokumen.py; PENANDA='def dari_pelanggaran_unik('
S=backend/api_gateway/app/services/nomor_dokumen.py; R=backend/api_gateway/app/routers
T="tests/unit/test_bug149_nomor_dokumen.py tests/unit/test_nomor_pembelian_lompat.py"
SAB=(
 "s1 pratinjau salah mulai (last tanpa +1)|$S|s@n = (last or 0) + 1@n = (last or 0)@|$T"
 "s2 pratinjau tak melompati nomor terpakai|$S|s@if calon not in terpakai:@if True:@|$T"
 "s3 format manual dilonggarkan (maks 5000)|$S|s@{1,50}@{1,5000}@|$T"
 "s4 manual tak dicek terpakai|$S|s@^    await pastikan_bebas(conn, tenant_id, jenis, nomor)\$@    pass@|$T"
 "s5 ubah draf menghitung nomor sendiri sebagai bentrok|$S|s@sql += \" AND id <> \$3\"@sql += \"\"@|$T"
 "s6 pemetaan unik menyamarkan semua pelanggaran|$S|s@not in {j.indeks for j in JENIS.values()}:@not in {j.indeks for j in JENIS.values()} and False:@|$T"
 "s7 penawaran kembali ke salinan sendiri|$R/quotes.py|s@await NOMOR.tetapkan_nomor(@await NOMOR.tetapkan_nomor_x(@|$T"
 "s8 PO tak memetakan balapan nomor|$R/purchase_orders.py|s@galat_nomor = NOMOR.dari_pelanggaran_unik(e)   \# balapan@galat_nomor = None   \# balapan@|$T"
)
