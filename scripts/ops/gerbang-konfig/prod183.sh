# Konfig gerbang unit BUG-183 (terap upah+overhead STANDAR ke WIP saat report-output; aktual /labor menang; tak dobel). Tiap sabotase WAJIB membuat tes merah.
W=/root/mh-prod183; CABANG=feat/produksi-terap-standar; NAMA_JENDELA=prod183
POLA_UNIT='produksi|production|izin_rute|inventaris_rute|pagar_rute|permission'
HARNESS=scripts/ops/uji-salinan-prod183.sh
PENANDA_BERKAS=services/produksi_terap_standar.py; PENANDA='def kekurangan_standar'
A=backend/api_gateway/app; T=tests/unit/test_produksi_terap_standar.py; PS=$A/services/produksi_terap_standar.py; PR=$A/routers/production.py
SAB=(
 "t1 standar dobel (aktual tak dikurangkan)|$PS|s#return max(0, int(target - Decimal(aktual)))#return max(0, int(target - Decimal(0)))#|$T"
 "t2 upah borongan diabaikan (planned_labor 0)|$PR|s#Decimal(str(order\[\"planned_labor_cost\"\] or 0)), planned, _baik_sebelum#Decimal(0), planned, _baik_sebelum#|$T"
 "t3 output kedua menerapkan lagi (tak kumulatif)|$PS|s#(Decimal(sudah_baik) + Decimal(baik))#Decimal(baik)#|$T"
 "t4 overhead standar tak diterapkan|$PR|s#jumlah=_terap_oh, tanggal=hari_ini#jumlah=0, tanggal=hari_ini#|$T"
 "t5 biaya aktual WO tak ditambah (UPDATE)|$PR|s#actual_labor_cost = COALESCE(actual_labor_cost, 0) + \$4,#actual_labor_cost = COALESCE(actual_labor_cost, 0) + 0,#|$T"
)
