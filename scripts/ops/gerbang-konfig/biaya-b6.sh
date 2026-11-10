# Konfig gerbang unit (dibaca scripts/ops/gerbang-unit.sh). Sabotase = bukti unit ini; tiap baris WAJIB membuat tes merah.
W=/root/mh-biaya-b6-r3; CABANG=feat/biaya-b6-bundel-r3; NAMA_JENDELA=biaya-b6
POLA_UNIT='biaya|expense|izin_rute|inventaris_rute'
HARNESS=scripts/ops/uji-salinan-biaya-b6.sh
PENANDA_BERKAS=services/biaya_riwayat.py; PENANDA='PENANDA_BIAYA_B6 = "biaya-b6-bundel-w20261110"'
A=backend/api_gateway/app; R=$A/services/biaya_riwayat.py; T=tests/unit/test_biaya_b6_bundel.py
SAB=(
 "b1 paper None kembali|$R|s#            \"paper\": \"A4\", \"sent\":#            \"paper\": None, \"sent\":#|$T"
 "b2 vendor hilang dari skema respons|$A/schemas/expenses.py|s@    vendor: str = \"\"  # B6@    # B6@|$T"
 "b3 vendor bukan nama vendor|$R|s#\"vendor\": e\[\"vendor_name\"\] or \"\"#\"vendor\": e[\"account_name\"] or \"\"#|$T"
)
