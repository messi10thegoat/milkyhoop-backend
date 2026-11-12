# Konfig gerbang terfokus BF-1c1 (aksi Bank Feed cocokkan/kecualikan/lepaskan + riwayat + laporan tak-di-bank; NOL jurnal; penjaga void BANK_TX_BERTAUT; tanpa migrasi).
# Pemisah sabotase = "|", ekspresi sed memakai "@". POLA_UNIT dicari di tests/unit + tests/milkybook (gerbang-unit.sh, test_*.py).
W=/root/mh-bf1c1; CABANG=feat/bankfeed-bf1c1-aksi; NAMA_JENDELA=bankfeed-1c1
POLA_UNIT='bankfeed|mutasi_bank|bank-accounts|bank_accounts|kas_bank|kasbank|sql_bt_tanpa_batal|SQL_MUTASI|SQL_JOIN_DOKUMEN|jaga_rekonsiliasi|tolak_void_bila|inventaris_rute_tulis|gate_izin|include_router'
HARNESS=scripts/ops/uji-salinan-bankfeed-1c1.sh
PENANDA_BERKAS=services/bankfeed_aksi.py; PENANDA='def rencana_cocokkan('
A=backend/api_gateway/app
T="tests/unit/test_bankfeed_aksi.py tests/unit/test_bankfeed_tak_di_bank.py tests/unit/test_bankfeed_tebak.py tests/unit/test_void_rekon.py tests/unit/test_bankfeed_impor.py"
AK=$A/services/bankfeed_aksi.py
SAB=(
 "s1 jaga void TAK menghitung tautan Bank Feed|$A/services/jaga_rekonsiliasi.py|s@    if r and r.get(\"bertaut\"):@    if False:@|$T"
 "s2 jaga menghitung tautan yang SUDAH dilepas|$A/services/jaga_rekonsiliasi.py|s@AND t.dilepas_pada IS NULL)) AS bertaut@)) AS bertaut@|$T"
 "s3 status baris tak dikunci/dibaca ulang saat tulis|$AK|s@SQL_MUTASI + (\" FOR UPDATE\" if kunci else \"\")@SQL_MUTASI@|$T"
 "s4 tanpa kunci per dokumen MUTASI_BT|$AK|s@await conn.execute(\"SELECT pg_advisory_xact_lock(hashtext(\$1))\", f\"MUTASI_BT:{tid}:{bid}\")@pass@|$T"
 "s5 tanpa kunci MUTASI_BANK per baris|$AK|s@await conn.execute(\"SELECT pg_advisory_xact_lock(hashtext(\$1))\", f\"MUTASI_BANK:{mutasi_id}\")@pass@|$T"
 "s6 versi pratinjau tak dicek saat tulis|$AK|s@if body.get(\"versi\") and body\[\"versi\"\] != r\[\"versi\"\]:@if False:@|$T"
 "s7 Σ alokasi tak dicek terhadap nominal baris|$AK|s@    if jumlah != m\[\"nominal\"\]:@    if False:@|$T"
 "s8 arah dokumen tak dicek|$AK|s@        if r\[\"arah\"\] != m\[\"arah\"\]:@        if False:@|$T"
 "s9 lepaskan MENGHAPUS tautan (bukan melepas)|$AK|s@^SQL_TAUTAN_LEPAS = .*@SQL_TAUTAN_LEPAS = (\"DELETE FROM mutasi_bank_tautan WHERE tenant_id = \$1 AND mutasi_bank_id = \$2 AND \$3::uuid IS NOT NULL\")@|$T"
 "s10 aksi menulis kolom rekonsiliasi lama matched_statement_line_id|$AK|s@^SQL_PUTUS = (\"UPDATE mutasi_bank SET status = \$4,@SQL_PUTUS = (\"UPDATE mutasi_bank SET matched_statement_line_id = NULL, status = \$4,@|$T"
 "s11 alokasi boleh melebihi sisa dokumen|$AK|s@        if a <= 0 or a > sisa:@        if False:@|$T"
 "s12 dokumen penuh 3 baris tetap diterima|$AK|s@        if int(r\[\"jumlah_tautan\"\]) >= MAKS_BARIS_PER_BT:@        if False:@|$T"
 "s13 lepaskan boleh untuk keputusan Catat/Pribadi|$AK|s@    if m\[\"cara\"\] in (\"catat\", \"pribadi\"):@    if False:@|$T"
 "s14 lepaskan tanpa cek rekonsiliasi|$AK|s@    if m\[\"rekonsiliasi_id\"\]:@    if False:@|$T"
 "s15 laporan memasukkan pasangan batal (predikat dicabut)|$A/services/bankfeed_tebak.py|s@  AND {KR.sql_bt_tanpa_batal(\"bt\", \"\$1\")}\"\"\"@\"\"\"@|$T"
 "s16 laporan tanpa lag kliring 3 hari|$A/services/bankfeed_tak_di_bank.py|s@^LAG_KLIRING = 3 @LAG_KLIRING = 0 @|$T"
 "s17 laporan memasukkan saldo awal sebagai transaksi bank|$A/services/bankfeed_tak_di_bank.py|s@if k\[\"jenis\"\] != \"opening\" and k\[\"sumber\"\] not in SUMBER_BUKAN_MUTASI@if True@|$T"
 "s18 kandidat cocokkan menyaring tautan (bukan menghitung kapasitas)|$A/services/bankfeed_tebak.py|s@  AND bt.id = ANY(\$3::uuid\[\])@  AND bt.id = ANY(\$3::uuid[]) AND NOT EXISTS (SELECT 1 FROM mutasi_bank_tautan z WHERE z.tenant_id = \$1 AND z.bank_transaction_id = bt.id AND z.dilepas_pada IS NULL)@|$T"
)
