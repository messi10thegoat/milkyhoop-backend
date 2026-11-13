# Konfig gerbang unit BUG-203 (reject GRN tampil di API; respons terima: sisa + petunjuk + pointer tutup PO; items kosong: pratinjau bawaan semua sisa, tulis ITEMS_KOSONG). Tiap sabotase WAJIB membuat tes merah.
W=/root/mh-b203; CABANG=feat/grn-reject-terlihat; NAMA_JENDELA=grn203
POLA_UNIT='grn|goods_receipt|terima|receive|purchase_order|po_rencana|po_pratinjau|izin_rute|inventaris_rute|pagar_rute|permission'
HARNESS=scripts/ops/uji-salinan-grn203.sh
PENANDA_BERKAS=services/grn_ditolak.py; PENANDA='def ditolak_text'
A=backend/api_gateway/app; T=tests/unit/test_grn_reject_203.py; GRR=$A/routers/goods_receipts.py; POR=$A/routers/purchase_orders.py; GTF=$A/services/grn_ditolak.py
SAB=(
 "j1 detail GRN tak memuat quantity_rejected|$GRR|s#\"quantity_rejected\": str(i.get(\"quantity_rejected\") or 0),#\"qr\": str(i.get(\"quantity_rejected\") or 0),#|$T"
 "j2 teks ditolak tanpa alasan|$GTF|s#bagian.append(str(alasan).strip())#pass#|$T"
 "j3 tulis menerima semua sisa (items kosong lolos)|$POR|s#if not body.items:  \# BUG-203#if False and not body.items:  \# BUG-203#|$T"
 "j4 pratinjau tak menandai bawaan semua sisa|$POR|s#\"bawaan_semua_sisa\": not (body and body.items)#\"bawaan_semua_sisa\": False#|$T"
 "j5 respons terima tanpa sisa|$POR|s#\"sisa_belum_diterima\": _sisa,#\"sisa_x\": _sisa,#|$T"
 "j6 tanpa pointer tutup PO|$POR|s#\"tutup_po\": {\"rute\": f\"/api/purchase-orders/{po_id}/close\"#\"tutup_x\": {\"rute\": f\"/api/purchase-orders/{po_id}/close\"#|$T"
 "j7 petunjuk tak menyebut aksi tutup|$GTF|s#tutup pesanan (aksi Tutup)#hubungi admin#|$T"
)
