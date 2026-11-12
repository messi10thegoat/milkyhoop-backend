# Konfig gerbang unit BUG-187 (ubah draf Biaya tercatat di riwayat; pola BUG-058). Tiap sabotase WAJIB membuat tes merah.
W=/root/mh-b187; CABANG=feat/biaya-riwayat-ubah; NAMA_JENDELA=biayaubah
POLA_UNIT='biaya|expense|riwayat|audit|izin_rute|inventaris_rute|pagar_rute|permission'
HARNESS=scripts/ops/uji-salinan-biayaubah.sh
PENANDA_BERKAS=services/biaya_riwayat.py; PENANDA='def ringkas_ubah'
A=backend/api_gateway/app; T=tests/unit/test_biaya_riwayat_ubah.py; EXR=$A/routers/expenses.py; BR=$A/services/biaya_riwayat.py; AD=$A/services/audit_dokumen.py; SR=$A/services/so_riwayat.py
SAB=(
 "v1 PATCH tak mencatat|$EXR|s#await catat_riwayat(conn, ctx\[\"tenant_id\"\], \"expenses\", expense_id,#await _tanpa_jejak(conn, ctx[\"tenant_id\"], \"expenses\", expense_id,#|$T"
 "v2 riwayat tak membaca audit biaya|$BR|s#{\"expense\": \[e\[\"id\"\]\]}, {\"expense\": True}, limit)#{}, {}, limit)#|$T"
 "v3 event tak masuk riwayat (riwayat=False)|$AD|s#Ev(\"EXPENSE_UPDATED\", \"expenses\", \"Biaya diubah\", True)#Ev(\"EXPENSE_UPDATED\", \"expenses\", \"Biaya diubah\", False)#|$T"
 "v4 jejak tanpa medan/total|$EXR|s#meta={\"medan\": _medan,#meta={\"x\": _medan,#|$T"
 "v5 entitas expenses tak dipetakan|$SR|s#    \"expenses\": \"expense\", \"expense\": \"expense\",#    \"expense\": \"expense\",#|$T"
)
