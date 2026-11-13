# Konfig gerbang unit BUG-193 (galat post/void Biaya berkode + kalimat Indonesia; pratinjau post/void memuat keadaan/teks SESUDAH). Tiap sabotase WAJIB membuat tes merah.
W=/root/mh-b193; CABANG=feat/biaya-galat-berkode; NAMA_JENDELA=biaya193
POLA_UNIT='biaya|expense|izin_rute|inventaris_rute|pagar_rute|permission'
HARNESS=scripts/ops/uji-salinan-biaya193.sh
PENANDA_BERKAS=routers/expenses.py; PENANDA='BIAYA_SUDAH_BERJURNAL'
A=backend/api_gateway/app; T=tests/unit/test_biaya_galat_193.py; EXR=$A/routers/expenses.py
SAB=(
 "c1 kode galat berubah|$EXR|s#GalatBerkode(409, \"BIAYA_SUDAH_BERJURNAL\",#GalatBerkode(409, \"BIAYA_BERJURNAL\",#|$T"
 "c2 kalimat void kembali Inggris|$EXR|s#\"Biaya ini sudah dibatalkan.\")#\"Expense already voided\")#|$T"
 "c3 sesudah dari string tetap|$EXR|s#\"biaya_keadaan_sesudah\": BD.keadaan(r\[\"status\"\]),#\"biaya_keadaan_sesudah\": \"dibayar\",#|$T"
 "c4 pratinjau menurunkan kode lain dari tulis|$EXR|s#or getattr(e, \"code\", None) or f\"HTTP_#or f\"HTTP_#|$T"
 "c5 sesudah tak null saat alasan kosong|$EXR|s#d\[\"biaya_keadaan_sesudah\"\] = d\[\"position_text_sesudah\"\] = None#d = d#|$T"
 "c6 keadaan sesudah tanpa saring tenant|$EXR|s#AS jb FROM expenses WHERE id = \$1 AND tenant_id = \$2#AS jb FROM expenses WHERE id = \$1 AND \$2 = \$2#|$T"
)
