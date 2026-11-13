# Konfig gerbang unit BUG-192 (/labor tarif standar 0: peringatan + tarif_ditemukan; perilaku terap tak berubah). Tiap sabotase WAJIB membuat tes merah.
W=/root/mh-b192; CABANG=feat/labor-peringatan-tarif-nol; NAMA_JENDELA=labor192
POLA_UNIT='labor|produksi|production|izin_rute|inventaris_rute|pagar_rute|permission'
HARNESS=scripts/ops/uji-salinan-labor192.sh
PENANDA_BERKAS=services/labor_peringatan.py; PENANDA='def peringatan'
A=backend/api_gateway/app; T=tests/unit/test_labor_peringatan_192.py; LPF=$A/services/labor_peringatan.py; PRF=$A/routers/production.py
SAB=(
 "l1 tarif 0 dianggap ditemukan|$LPF|s#return Decimal(str(labor_rate_std or 0)) > 0 or Decimal(str(oh_rate_std or 0)) > 0#return True#|$T"
 "l2 hanya labor diperiksa (overhead diabaikan)|$LPF|s# or Decimal(str(oh_rate_std or 0)) > 0##|$T"
 "l3 peringatan selalu ada|$LPF|s#return None if tarif_ditemukan(labor_rate_std, oh_rate_std) else PERINGATAN_TARIF_NOL#return PERINGATAN_TARIF_NOL#|$T"
 "l4 respons tak memuat tarif_ditemukan|$PRF|s#\"tarif_ditemukan\": LP.tarif_ditemukan(labor_rate_std, oh_rate_std),#\"tarif_x\": LP.tarif_ditemukan(labor_rate_std, oh_rate_std),#|$T"
 "l5 teks peringatan tanpa jalan keluar|$LPF|s#Pilih operasi atau Work Center bertarif; #Hubungi admin; #|$T"
)
