# Konfig gerbang unit K8-W (peringatan Terima Pemindahan Barang yang menyisakan barang di Transit; aditif, tanpa migrasi). Tiap sabotase WAJIB membuat tes merah.
W=/root/mh-k8w; CABANG=feat/k8w-peringatan-terima; NAMA_JENDELA=k8w
POLA_UNIT='k8w|pemindahan|stock_transfer|bug246|izin_rute|sql_placeholder'
HARNESS=scripts/ops/uji-salinan-k8w.sh
PENANDA_BERKAS=services/pemindahan_dua_langkah.py; PENANDA='peringatan_terima'
A=backend/api_gateway/app; T=backend/api_gateway/tests/unit/test_k8w_peringatan_terima.py; DF=$A/services/pemindahan_dua_langkah.py; RF=$A/routers/stock_transfers.py; SF=$A/schemas/stock_transfers.py
SAB=(
 "w1 peringatan tak pernah muncul|$DF|s#if sisa > _NOL:#if False:#|$T"
 "w2 satuan tak ikut kalimat|$DF|s#satuan = f\" {ln\['unit'\]}\" if ln.get(\"unit\") else \"\"#satuan = \"\"#|$T"
 "w3 pratinjau tak meneruskan peringatan|$RF|s#\"bawaan_semua_tertunda\", \"tujuan\", \"peringatan\")#\"bawaan_semua_tertunda\", \"tujuan\")#|$T"
 "w4 Terima (tulis) tak mengembalikan peringatan|$DF|s#\"catatan_fase\": catatan, \"peringatan\": rencana\[\"peringatan\"\]#\"catatan_fase\": catatan#|$T"
 "w5 respons /receive membuang peringatan|$RF|s#peringatan=hasil.get(\"peringatan\") or \[\]#peringatan=[]#|$T"
 "w6 peringatan jadi blok (menggagalkan Terima)|$DF|s#out.append({\"code\": \"PB_TERIMA_KURANG\"#blocks.append({\"code\": \"PB_TERIMA_KURANG\"#|$T"
 "w7 skema respons kehilangan medan peringatan|$SF|s#    peringatan: List\[Dict\[str, Any\]\] = \[\]#    peringatan_x: List[Dict[str, Any]] = []#|$T"
 "w8 selisih salah hitung|$DF|s#Selisih {_q(sisa)}#Selisih {_q(_d(ln[\"tertunda_sesudah\"]) + 1)}#|$T"
 "w9 rencana tak memuat peringatan|$DF|s#\"tuntas\": tuntas, \"peringatan\": peringatan_terima(lines),#\"tuntas\": tuntas,#|$T"
)
