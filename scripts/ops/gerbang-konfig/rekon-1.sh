# Konfig gerbang unit (dibaca scripts/ops/gerbang-unit.sh). Sabotase = bukti unit ini; tiap baris WAJIB membuat tes merah.
W=/root/mh-rekonlama; CABANG=fix/rekon-lama-pagar; NAMA_JENDELA=rekon-lama-1
POLA_UNIT='rekon|reconcil|izin_rute|inventaris_rute'
HARNESS=scripts/ops/uji-salinan-rekon-lama-pagar.sh
PENANDA_BERKAS=routers/bank_reconciliation.py; PENANDA='PENANDA_REKON_LAMA_PAGAR = "rekon-lama-pagar-w20261110"'
A=backend/api_gateway/app; R=$A/routers/bank_reconciliation.py; T=tests/unit/test_rekon_lama_pagar.py
SAB=(
 "r1 penyesuaian diterima lagi|$R|s#    if penyesuaian:#    if False and penyesuaian:#|$T"
 "r2 toleransi selisih 100 kembali|$R|s#    if beda != 0:#    if beda > 100:#|$T"
 "r3 selisih tak diketahui dianggap nol|$R|s#    if any(x is None for x in selisih):#    if False:#|$T"
 "r4 saring tenant tercocok hilang|$R|s#WHERE l.session_id = \$1 AND s.tenant_id = \$2#WHERE l.session_id = \$1 AND TRUE#|$T"
)
