# Konfig gerbang terfokus BF-1a (impor + daftar mutasi bank, NOL jurnal; V494 sudah LIVE; tanpa migrasi).
# Pemisah sabotase = "|", ekspresi sed memakai "@". POLA_UNIT dicari di folder unit tests/unit + tests/milkybook -- pelajaran cari-varian-r2.
W=/root/mh-bf1a; CABANG=feat/bankfeed-bf1a-impor-r2; NAMA_JENDELA=bankfeed-1a
POLA_UNIT='bankfeed|mutasi_bank|bank-accounts|bank_accounts|kas_bank|inventaris_rute_tulis|gate_izin|include_router|mutasi_bank_impor|ImporMutasi'
HARNESS=scripts/ops/uji-salinan-bankfeed-1a.sh
PENANDA_BERKAS=services/bankfeed_parser.py; PENANDA='def baca('
A=backend/api_gateway/app
T="tests/unit/test_bankfeed_parser.py tests/unit/test_bankfeed_impor.py"
SAB=(
 "s1 PEND tak ikut kaki Kredit lagi|$A/services/bankfeed_parser.py|s@h.urut if b.arah == \"masuk\"@h.baris if b.arah == \"masuk\"@|$T"
 "s2 pemisah koma naif|$A/services/bankfeed_parser.py|s@kolom = next(csv.reader(\[mentah\]))@kolom = mentah.split(\",\")@|$T"
 "s3 sidik tanpa saldo|$A/services/bankfeed_parser.py|s@ujung = format(b.saldo.quantize(SEN), \"f\")@ujung = \"#0\"@|$T"
 "s4 rantai saldo per baris tak diperiksa|$A/services/bankfeed_parser.py|s@if harap != b.saldo:@if False:@|$T"
 "s5 sidik ada tanpa pagar tenant|$A/services/bankfeed_impor.py|s@WHERE tenant_id = \$1 AND bank_account_id = \$2 AND sidik = ANY@WHERE bank_account_id = \$2 AND sidik = ANY@|$T"
 "s6 hash isi tak dicek|$A/services/bankfeed_impor.py|s@if body.get(\"hash_isi\") and body\[\"hash_isi\"\] != dalam\[\"hash_isi\"\]:@if False:@|$T"
 "s7 kunci impor per rekening dicabut|$A/services/bankfeed_impor.py|s@await conn.execute(\"SELECT pg_advisory_xact_lock(hashtext(\$1))\", f\"MUTASI_IMPOR:{tid}:{rekening_id}\")@pass@|$T"
 "s8 ON CONFLICT dicabut|$A/services/bankfeed_impor.py|s@ON CONFLICT (tenant_id, bank_account_id, sidik) DO NOTHING RETURNING id@RETURNING id@|$T"
)
