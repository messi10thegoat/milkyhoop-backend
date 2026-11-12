# Konfig gerbang terfokus BF-1b (penentu tebakan Bank Feed: cocok -90/+20, bawaan BCA F2, layak_massal K4, /calon; BACA SAJA; tanpa migrasi).
# Pemisah sabotase = "|", ekspresi sed memakai "@". POLA_UNIT dicari di tests/unit + tests/milkybook (gerbang-unit.sh, test_*.py).
W=/root/mh-bf1b; CABANG=feat/bankfeed-bf1b-tebak; NAMA_JENDELA=bankfeed-1b
POLA_UNIT='bankfeed|mutasi_bank|bank-accounts|bank_accounts|kas_bank|kasbank|sql_bt_tanpa_batal|SQL_MUTASI|SQL_JOIN_DOKUMEN|inventaris_rute_tulis|gate_izin|include_router'
HARNESS=scripts/ops/uji-salinan-bankfeed-1b.sh
PENANDA_BERKAS=services/bankfeed_tebak.py; PENANDA='def putuskan('
A=backend/api_gateway/app
T="tests/unit/test_bankfeed_tebak.py tests/unit/test_bankfeed_impor.py tests/unit/test_bankfeed_parser.py"
TB=$A/services/bankfeed_tebak.py
SAB=(
 "s1 hanya pembalik dikecualikan (asli tetap calon)|$A/services/kasbank_ringkas.py|s@  AND je.reversed_by_id IS NULL AND je.reversal_of_id IS NULL@  AND je.reversal_of_id IS NULL@|$T"
 "s2 rentang P5 tertukar (+90/-20)|$TB|s@MUNDUR_HARI, MAJU_HARI = 90, 20@MUNDUR_HARI, MAJU_HARI = 20, 90@|$T"
 "s3 yakin tanpa nama cocok|$TB|s@yakin = cocok_nama and abs(hari) <= YAKIN_HARI and layak@yakin = abs(hari) <= YAKIN_HARI and layak@|$T"
 "s4 layak_massal tanpa cek unik dua arah|$TB|s@        elif len(pilih.get(ids\[0\], \[\])) != 1:@        elif False:@|$T"
 "s5 selisih Rp2.500 jadi yakin|$TB|s@\"selisih_nominal\": selisih, \"nama_cocok\": cocok_nama, \"keyakinan\": \"periksa\"@\"selisih_nominal\": selisih, \"nama_cocok\": cocok_nama, \"keyakinan\": \"yakin\"@|$T"
 "s6 arah diabaikan|$TB|s@if b\[\"arah\"\] == baris\[\"arah\"\] and dalam_rentang@if dalam_rentang@|$T"
 "s7 tautan aktif tak dikecualikan|$TB|s@AND NOT EXISTS (SELECT 1 FROM mutasi_bank_tautan t WHERE t.tenant_id = \$1 AND t.bank_transaction_id = bt.id AND t.dilepas_pada IS NULL)@AND TRUE@|$T"
 "s8 tenant dicabut dari SQL kandidat|$TB|s@WHERE bt.tenant_id = \$1 AND bt.bank_account_id = \$2@WHERE bt.bank_account_id = \$2@|$T"
 "s9 admin ke 5-80000 (putusan F2 tertukar)|$TB|s@AKUN_BAWAAN = {\"admin\": \"5-20850\"@AKUN_BAWAAN = {\"admin\": \"5-80000\"@|$T"
 "s10 sumber dokumen biaya boleh yakin|$TB|s@layak = b.get(\"sumber\") in SUMBER_YAKIN@layak = True@|$T"
 "s11 persaingan baris tak diturunkan|$TB|s@return tandai_layak_massal(turunkan_persaingan(peta, status), status)@return tandai_layak_massal(peta, status)@|$T"
 "s12 nominal dari tanda amount, bukan jurnal|$TB|s@abs(j.net) AS nominal@abs(bt.amount) AS nominal@|$T"
)
