# Konfig gerbang terfokus onboarding-bawaan (BUG-153/157/162; dibaca scripts/ops/gerbang-unit.sh). Tanpa migrasi.
# Pemisah sabotase = "|", ekspresi sed memakai "@".
W=/root/mh-onboarding; CABANG=feat/onboarding-bawaan; NAMA_JENDELA=onboarding
POLA_UNIT='onboarding|rekening_awal|tax_codes|routers import units|satuan_master|create_tenant_and_user|signup|rekening_bawaan'
HARNESS=scripts/ops/uji-salinan-onboarding.sh
PENANDA_BERKAS=services/rekening_awal.py; PENANDA='def seed_rekening_awal('
A=backend/api_gateway/app
T=tests/unit/test_onboarding_bawaan.py
SAB=(
 "s1 Kas tak lagi default|$A/services/rekening_awal.py|s@(\"Kas\", \"cash\", \"1-10100\", True)@(\"Kas\", \"cash\", \"1-10100\", False)@|$T"
 "s2 Bank ikut default|$A/services/rekening_awal.py|s@(\"Bank\", \"bank\", \"1-10200\", False)@(\"Bank\", \"bank\", \"1-10200\", True)@|$T"
 "s3 pencarian akun CoA tanpa saring tenant|$A/services/rekening_awal.py|s@WHERE tenant_id = \$1 AND account_code = \$2@WHERE account_code = \$2@|$T"
 "s4 onboarding tak memanggil penyemai|$A/services/onboarding_service.py|s@rek_count = await seed_rekening_awal(conn, tenant_id, user_id)@rek_count = 0@|$T"
 "s5 kode pajak tak mengirim coa_name|$A/routers/tax_codes.py|s@\"coa_name\": row\[\"coa_name\"\],@@|$T"
 "s6 nama akun pajak lintas tenant|$A/routers/tax_codes.py|s@ AND c.tenant_id = tax_codes.tenant_id@@|$T"
 "s7 daftar satuan kembali kosong untuk tenant baru|$A/routers/units.py|s@semua = await SM.daftar(conn, ctx\[\"tenant_id\"\], search)@semua = []@|$T"
)
