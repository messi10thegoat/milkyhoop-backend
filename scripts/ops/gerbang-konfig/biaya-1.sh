# Konfig gerbang unit Biaya BE gelombang 1 (B1 keadaan, B3 void/preview tanpa alasan, B4 cakupan, B5 detail+idempotensi, B7 delete/preview). Tiap sabotase WAJIB membuat tes merah.
W=/root/mh-biaya1; CABANG=feat/biaya-b1b5; NAMA_JENDELA=biaya-1
POLA_UNIT='biaya|expense|izin_rute|inventaris_rute|pagar_rute|permission'
HARNESS=scripts/ops/uji-salinan-biaya-gel1.sh
PENANDA_BERKAS=services/biaya_daftar.py; PENANDA='def blok_hapus'
A=backend/api_gateway/app; T=tests/unit/test_biaya_gel1.py; BDF=$A/services/biaya_daftar.py; EXR=$A/routers/expenses.py
SAB=(
 "g1 keadaan terbit dipetakan salah|$BDF|s#KEADAAN_BIAYA = {\"draft\": \"draf\", \"posted\": \"dibayar\"#KEADAAN_BIAYA = {\"draft\": \"draf\", \"posted\": \"draf\"#|$T"
 "g2 pratinjau batal tanpa alasan tetap bisa lanjut|$EXR|s#d\\[\"can_proceed\"\\] = False#d[\"can_proceed\"] = True#|$T"
 "g3 hapus boleh untuk biaya terbit|$BDF|s#if existing\\[\"status\"\\] != \"draft\":#if existing[\"status\"] == \"void\":#|$T"
 "g4 post tak memakai idempotensi|$EXR|s#await idem_buat.mulai_aksi(conn, ctx, idem_buat.kunci_dari(request), \"EXPENSE_POST\", expense_id, {}, response)#(None, None, None)#|$T"
 "g5 nama pelanggan daftar kosong|$EXR|s#\"billed_to_customer_name\": row\\[\"billed_to_customer_name\"\\],#\"billed_to_customer_name\": None,#|$T"
 "g6 cakupan pill disebut per periode|$BDF|s#\"draf\": \"Semua\", \"tanpa_bukti\"#\"draf\": \"Periode\", \"tanpa_bukti\"#|$T"
 "g7 detail tanpa position_text|$EXR|s#result\\[\"position_text\"\\] = BD.position_text(#result[\"position_text_x\"] = BD.position_text(#|$T"
)
