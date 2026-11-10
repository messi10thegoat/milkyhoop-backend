# Konfig gerbang unit (dibaca scripts/ops/gerbang-unit.sh). Sabotase = bukti unit ini; tiap baris WAJIB membuat tes merah.
W=/root/mh-lamp-r3; CABANG=feat/lampiran-baca-kasbank-kasbon-r3; NAMA_JENDELA=lamp-1
POLA_UNIT='lampiran|izin_rute|inventaris_rute'
HARNESS=scripts/ops/uji-salinan-lampiran-kasbank.sh
PENANDA_BERKAS=routers/lampiran_dokumen.py; PENANDA='PENANDA_LAMPIRAN_KASBANK = "lampiran-baca-kasbank-kasbon-w20261110"'
A=backend/api_gateway/app; L=$A/routers/lampiran_dokumen.py; T=tests/unit/test_lampiran_baca_kasbank.py
SAB=(
 "l1 jenis baru tak diterima|$L|s#and jenis not in JENIS_TUNGGAL:#:#|$T"
 "l2 pagar pay-group kasbon hilang|$L|s#(s.pra_cek_dok is not None and not await#(False and not await#|$T"
 "l3 saring tenant dokumen hilang|$L|s#AND {t}.tenant_id = \$2#AND TRUE#|$T"
 "l4 kasbon berizin kas_bank|$A/middleware/permission_middleware.py|s#employee_advance/\[^/\]+\$\", \[\"GET\"\], \"payroll\"#employee_advance/[^/]+$\", [\"GET\"], \"kas_bank\"#|$T"
 "l5 url grup transfer salah modul|$A/routers/sales_order_lampiran.py|s#\"bank_transfer\": (\"bank-transfers\", None)#\"bank_transfer\": (\"bank-transactions\", None)#|$T"
)
