# Konfig gerbang unit BUG-184 (a: satuan WO ikut BOM; b: status_text Indonesia + siap_diselesaikan di daftar/detail WO). Tiap sabotase WAJIB membuat tes merah.
W=/root/mh-wounit; CABANG=feat/wo-satuan-bom-r3; NAMA_JENDELA=wounit
POLA_UNIT='produksi|production|wo_|izin_rute|inventaris_rute|pagar_rute|permission'
HARNESS=scripts/ops/uji-salinan-wounit.sh
PENANDA_BERKAS=services/wo_status.py; PENANDA='def siap_diselesaikan'
A=backend/api_gateway/app; T1=tests/unit/test_wo_status_184b.py; T2=tests/unit/test_wo_satuan_bom.py; WS=$A/services/wo_status.py; PR=$A/routers/production.py
SAB=(
 "w1 satuan BOM diabaikan|$PR|s#    if bom_setara and isinstance(unit_bom, str)#    if False and isinstance(unit_bom, str)#|$T2"
 "w2 siap_diselesaikan true padahal belum >= rencana|$WS|s#Decimal(str(completed)) >= Decimal(str(planned))#Decimal(str(completed)) >= Decimal(str(planned)) - 1#|$T1"
 "w3 siap_diselesaikan tak memeriksa status|$WS|s#if status != \"in_progress\" or completed is None#if completed is None#|$T1"
 "w4 status_text mentah/Inggris|$WS|s#\"released\": \"Dirilis\"#\"released\": \"Released\"#|$T1"
 "w5 daftar tak mengisi status_text|$PR|s#\"status_text\": WS.status_text(row\[\"status\"\]),#\"status_text\": None,#|$T1"
 "w6 isi satuan dari BOM tanpa cek kesetaraan|$PR|s#wo_unit = satuan_wo(body.unit, _bom_unit, _setara)#wo_unit = satuan_wo(body.unit, _bom_unit, True)#|tests/unit/test_wo_satuan_bom.py"
)
