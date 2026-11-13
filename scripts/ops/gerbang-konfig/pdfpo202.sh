# Konfig gerbang unit BUG-202 (PDF PO: Tanggal kirim, Syarat bayar dari termin vendor, pajak per baris opt-in, kode barang cadangan SKU master, petunjuk kop kosong layar). Tiap sabotase WAJIB membuat tes merah.
W=/root/mh-b202; CABANG=feat/pdf-po-lengkap; NAMA_JENDELA=pdfpo202
POLA_UNIT='pdf|cetak|po_cetak|purchase_order|tabel_barang|izin_rute|inventaris_rute|pagar_rute|permission'
HARNESS=scripts/ops/uji-salinan-pdfpo202.sh
PENANDA_BERKAS=services/po_cetak.py; PENANDA='kop_kosong'
A=backend/api_gateway/app; T=tests/unit/test_pdf_po_202.py; PCF=$A/services/po_cetak.py; TPO=$A/templates/pdf/purchase_order.html; TBF=$A/templates/pdf/_partials/tabel_barang.html
SAB=(
 "q1 label kembali Jadwal terima|$TPO|s#<b>Tanggal kirim</b>#<b>Jadwal terima</b>#|$T"
 "q4 pajak per baris tak diteruskan|$TPO|s#\"pajak\": {\"tarif\": \"tax_rate\", \"jumlah\": \"tax_amount\"}#\"pajak_x\": {\"tarif\": \"tax_rate\", \"jumlah\": \"tax_amount\"}#|$T"
 "q5 pajak baris tak opt-in (semua dokumen)|$TBF|s#{%- if k.pajak and item\[k.pajak.jumlah\]#{%- if item.tax_amount and item[k.pajak.jumlah]#|$T"
 "q6 kode barang tanpa cadangan SKU master|$PCF|s#COALESCE(NULLIF(i.item_code, ''), pr.item_code) AS item_code#i.item_code AS item_code#|$T"
 "q7 petunjuk kop ikut tercetak|$TPO|s#.petunjuk-kop{display:none}##|$T"
 "q8 petunjuk kop tak muncul|$TPO|s#{% if po.kop_kosong %}#{% if False %}#|$T"
 "q9 join produk tanpa saring tenant|$PCF|s#AND pr.tenant_id = \$2#AND TRUE#|$T"
)
