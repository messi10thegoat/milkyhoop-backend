# Konfig gerbang terfokus BF-1c2 (catat uang_masuk/uang_keluar/pribadi + batalkan-catatan + biaya BI-FAST; jurnal HANYA lewat penulis Uang Masuk/Keluar hasil ekstraksi; tanpa migrasi).
# Pemisah sabotase = "|", ekspresi sed memakai "@". POLA_UNIT dicari di tests/unit + tests/milkybook (gerbang-unit.sh, test_*.py).
W=/root/mh-bf1c2; CABANG=feat/bankfeed-bf1c2-catat; NAMA_JENDELA=bankfeed-1c2
POLA_UNIT='kasbank_ringkas|SQL_MUTASI|bankfeed|mutasi_bank|bank-accounts|kasbank|jaga_rekonsiliasi|tolak_void_bila|bank_transactions|create_manual_transaction|void_transaction|rencana_tx_manual|rencana_void_manual|inventaris_rute_tulis|gate_izin|include_router|izin_untuk_jalur'
HARNESS=scripts/ops/uji-salinan-bankfeed-1c2.sh
PENANDA_BERKAS=services/bankfeed_aksi.py; PENANDA='def rencana_catat('
A=backend/api_gateway/app
T="tests/unit/test_bankfeed_catat.py tests/unit/test_kasbank_ekstraksi_c2.py tests/unit/test_bankfeed_tebak.py tests/unit/test_bankfeed_aksi.py tests/unit/test_void_rekon.py tests/unit/test_pagar_uang_muka.py tests/unit/test_void_baris_bank_satu.py tests/unit/test_kas_minus_k1.py tests/unit/test_kasbank_b8.py tests/unit/test_kasbank_standar_so.py"
TB=$A/services/bankfeed_tebak.py; AK=$A/services/bankfeed_aksi.py; KTF=$A/services/kasbank_tulis.py; PM=$A/middleware/permission_middleware.py
SAB=(
 "s1 BI-FAST: pola cocok tetapi status jadi YAKIN (bawaan bukan lagi periksa)|$TB|s@return _kosong(jenis=\"catat\", keyakinan=\"periksa\",@return _kosong(jenis=\"catat\", keyakinan=\"yakin\",@|$T"
 "s2 BI-FAST: transfer BI-FAST biasa ikut diusulkan sebagai biaya (pola longgar)|$TB|s@BIAYA TXN(?: \.\*)?\$\"@\.\*\$\"@|$T"
 "s3 BI-FAST: nominal Rp2.500 tak dicek|$TB|s@sama = nominal is not None and Decimal(str(nominal)) == NOMINAL_BIAYA_BIFAST@sama = True@|$T"
 "s4 BI-FAST: arah masuk ikut dianggap biaya|$TB|s@if arah == \"keluar\" and sama else None@if sama else None@|$T"
 "s5 pribadi MASUK ke Prive (akun tertukar)|$AK|s@AKUN_PRIBADI = {\"keluar\": \"3-40000\", \"masuk\": \"3-10100\"}@AKUN_PRIBADI = {\"keluar\": \"3-40000\", \"masuk\": \"3-40000\"}@|$T"
 "s6 pribadi KELUAR ke Modal Pemilik (akun tertukar)|$AK|s@AKUN_PRIBADI = {\"keluar\": \"3-40000\", \"masuk\": \"3-10100\"}@AKUN_PRIBADI = {\"keluar\": \"3-10100\", \"masuk\": \"3-10100\"}@|$T"
 "s7 batal: tautan tak dilepas sebelum void (penjaga akan menolak)|$AK|s@    await conn.execute(SQL_TAUTAN_LEPAS, tid, m\[\"id\"\], str(uid))        # tautan DILEPAS dulu@    pass        # tautan DILEPAS dulu@|$T"
 "s8 catat: bt hasil tak ditautkan ke baris|$AK|s@    await conn.execute(SQL_TAUTAN_INSERT, tid, m\[\"id\"\], t\[\"bank_txn_id\"\], m\[\"nominal\"\], str(uid))@    pass@|$T"
 "s9 catat: nominal jurnal tak diverifikasi ulang|$AK|s@    if _bank_net(r\[\"baris\"\], r\[\"akun\"\]\[\"coa_id\"\]) != harap:@    if False:@|$T"
 "s10 catat: tanpa kunci BANK_TX_MANUAL|$AK|s@        await KT.kunci_tx_manual(conn, rekening_id)@        pass@|$T"
 "s11 batal: tanpa kunci BANK_TX_VOID|$AK|s@    await KT.kunci_void_manual(conn, r\[\"bt_id\"\])@    pass@|$T"
 "s12 batal: pratinjau tak menyaring BANK_TX_BERTAUT milik baris sendiri|$AK|s@            if b\[\"code\"\] != \"BANK_TX_BERTAUT\":@            if True:@|$T"
 "s13 catat: nominal berkoma diterima|$AK|s@    if m\[\"nominal\"\] != m\[\"nominal\"\].to_integral_value():@    if False:@|$T"
 "s14 catat: jenis yang belum ada lolos diam|$AK|s@    if jenis in JENIS_BELUM:@    if False:@|$T"
 "s15 catat: arah baris tak dicocokkan dengan jenis|$AK|s@    if wajib_arah and m\[\"arah\"\] != wajib_arah:@    if False:@|$T"
 "s16 pribadi: pengguna boleh memilih akun sendiri|$AK|s@        if body.get(\"akun_id\"):@        if False:@|$T"
 "s17 penulis: saldo berjalan bt tak memakai saldo rekening|$KTF|s@running_balance = int(r\[\"akun\"\]\[\"saldo\"\]) + txn_amount@running_balance = txn_amount@|$T"
 "s18 penulis: jurnal manual tak di-POSTED (rantai hash tak terpicu)|$KTF|s@await conn.execute(\"UPDATE journal_entries SET status = 'POSTED' WHERE id = \$1\", journal_id)    # Law 20: memicu rantai hash@pass    # Law 20@|$T"
 "s19 void: jurnal asli tak ditandai dibalik|$KTF|s@SET reversed_by_id = \$2, reversed_at = NOW()@SET reversed_by_id = NULL, reversed_at = NOW()@|$T"
 "s20 izin batalkan-catatan turun jadi C (bukan V)|$PM|s@batalkan-catatan(/preview)?\$\", \[\"POST\"\], \"kas_bank\", \"V\")@batalkan-catatan(/preview)?\$\", [\"POST\"], \"kas_bank\", \"C\")@|$T"
 "s21 PERIODE: catat di periode tertutup lolos (pemeriksaan periode penulis manual dicabut)|$KTF|s@^    await periode_terbuka(conn, tid, body.transaction_date, bl)@    pass@|$T"
 "s22 PERIODE: batalkan catatan yang tanggal asli kini di periode tertutup lolos|$KTF|s@^    await periode_terbuka(conn, tid, tx\[\"transaction_date\"\], bl)@    pass@|$T"
 "s23 KAS_MINUS dimatikan: uang keluar melebihi saldo kas lolos|$KTF|s@if a\[\"account_type\"\] in TIPE_KAS and _d(a\[\"saldo\"\]) - keluar < 0:@if False:@|$T"
 "s24 batalkan: blok void tak dilempar saat tulis (jurnal pembalik tetap ditulis walau diblok)|$AK|s@Blok(False), kunci=True)       # blok pertama dilempar@Blok(True), kunci=True)       # blok pertama dilempar@|$T"
 "s25 catat: blok penulis manual tak diteruskan ke pratinjau (KAS_MINUS/PERIODE hilang dari layar)|$AK|s@^        bl.daftar.append(b)@        pass@|$T"
 "s26 catat: akun lawan wajib dicabut (akun bawaan diam-diam)|$AK|s@^        if lawan is None:@        if False:@|$T"
)
