# Konfig gerbang unit BUG-197 "penyesuaian-standar-so" (galat berkode; void/preview tanpa alasan -> blok; ubah/hapus draf masuk riwayat; ?search= nomor+catatan+nama/SKU). Tiap sabotase WAJIB membuat tes merah.
W=/root/mh-b197; CABANG=feat/penyesuaian-standar-so; NAMA_JENDELA=sa197
POLA_UNIT='stock_adj|penyesuaian|audit_dokumen|riwayat|izin_rute|inventaris_rute|pagar_rute|permission'
HARNESS=scripts/ops/uji-salinan-sa197.sh
PENANDA_BERKAS=services/stok_sesuai.py; PENANDA='def syarat_cari'
A=backend/api_gateway/app; T=tests/unit/test_penyesuaian_197.py; SAR=$A/routers/stock_adjustments.py; SSF=$A/services/stok_sesuai.py; ADF=$A/services/audit_dokumen.py; SRF=$A/services/so_riwayat.py
SAB=(
 "a1 404 detail kembali string Inggris|$SAR|s#raise GalatBerkode(404, \"PENYESUAIAN_TIDAK_DITEMUKAN\", \"Penyesuaian stok tidak ditemukan.\")#raise HTTPException(status_code=404, detail=\"Stock adjustment not found\")#|$T"
 "a2 void preview tanpa alasan tak diblok|$SAR|s#d\[\"blocks\"\].append({\"code\": \"PENYESUAIAN_ALASAN_WAJIB\", \"message\": \"Isi alasan pembatalan.\"})#pass#|$T"
 "a3 PATCH tak mencatat riwayat|$SAR|s#_ad_sa.nama(\"stock_adjustment\", \"updated\")#_ad_sa.nama(\"stock_adjustment\", \"deleted\")#|$T"
 "a4 hapus draf tak dicatat|$SAR|s#await _catat_hapus_draf(conn, ctx, sa, \"api:stock_adjustments.delete\")#pass#|$T"
 "a5 event ubah tak tampil di riwayat|$ADF|s#\"Penyesuaian stok diubah\", True)#\"Penyesuaian stok diubah\", False)#|$T"
 "a6 search tanpa nama/SKU barang|$SSF|s#(i.product_name ILIKE \${mulai + i} OR i.product_code ILIKE \${mulai + i})#(FALSE)#|$T"
 "a7 search tanpa catatan|$SSF|s# OR {a}.notes ILIKE \${mulai + i}##|$T"
 "a8 riwayat tak membaca audit|$SSF|s#{\"stock_adjustment\": \[sa\[\"id\"\]\]}, {\"stock_adjustment\": True}, limit)#{}, {}, limit)#|$T"
)
