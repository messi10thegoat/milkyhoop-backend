# Konfig gerbang unit BUG-215 (label Indonesia medan riwayat PO/Penawaran). Tiap sabotase WAJIB membuat tes merah.
W=/root/mh-b215; CABANG=feat/riwayat-label-medan; NAMA_JENDELA=label215
POLA_UNIT='riwayat|po_|purchase_order|so_riwayat|izin_rute|inventaris_rute|pagar_rute|permission'
HARNESS=scripts/ops/uji-salinan-label215.sh
PENANDA_BERKAS=services/so_riwayat.py; PENANDA='LABEL_PER_KEJADIAN'
A=backend/api_gateway/app; T=tests/unit/test_riwayat_label_215.py; RWF=$A/services/so_riwayat.py
SAB=(
 "l1 syarat bayar tak berlabel|$RWF|s#(\"payment_terms_days\", \"syarat bayar\")#(\"payment_terms_days_x\", \"syarat bayar\")#|$T"
 "l2 tanggal kirim PO tak berlabel|$RWF|s#(\"expected_date\", \"tanggal kirim\")#(\"expected_date_x\", \"tanggal kirim\")#|$T"
 "l3 diskon persen PO tak berlabel|$RWF|s#(\"discount_amount\", \"diskon\"), (\"discount_percent\", \"diskon\"),#(\"discount_amount\", \"diskon\"),#|$T"
 "l4 ref_no PO tetap no. faktur vendor|$RWF|s#LABEL_PER_KEJADIAN = {\"PO_UPDATED\": {\"ref_no\": \"referensi\"}}#LABEL_PER_KEJADIAN = {}#|$T"
 "l5 render tak meneruskan kejadian|$RWF|s#label_medan(_medan, r\['eventType'\])#label_medan(_medan)#|$T"
 "l6 medan Penawaran signer tak berlabel|$RWF|s#(\"signer_name\", \"penandatangan\"),#(\"signer_name_x\", \"penandatangan\"),#|$T"
)
