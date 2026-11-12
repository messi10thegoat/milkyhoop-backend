# Konfig gerbang terfokus BF-1c3 (catat transfer/uang_muka_pelanggan/terima_pembayaran + batalkan generik menurut je.source_type + izin modul tujuan; jurnal HANYA lewat penulis modulnya
# hasil ekstraksi; tanpa migrasi). Pemisah sabotase = "|", ekspresi sed memakai "@". POLA_UNIT dicari di tests/unit + tests/milkybook (gerbang-unit.sh, test_*.py).
W=/root/mh-bf1c3; CABANG=feat/bankfeed-bf1c3-catat; NAMA_JENDELA=bankfeed-1c3
POLA_UNIT='bankfeed|mutasi_bank|bank_transfers|customer_deposits|receive_payments|buat_transfer_core|void_transfer_core|buat_dp_core|guc_tenant|jaga_rekonsiliasi|tolak_void_bila|inventaris_rute_tulis|gate_izin|include_router|izin_untuk_jalur|izin_rute_pengguna'
HARNESS=scripts/ops/uji-salinan-bankfeed-1c3.sh
PENANDA_BERKAS=services/bankfeed_aksi.py; PENANDA='def _rencana_dokumen('
A=backend/api_gateway/app
T="tests/unit/test_bankfeed_catat3.py tests/unit/test_bankfeed_catat.py tests/unit/test_bankfeed_aksi.py tests/unit/test_ekstraksi_c3.py tests/unit/test_guc_tenant_pagar.py tests/unit/test_as_g10.py tests/unit/test_deposit_jenis.py tests/unit/test_dp_pratinjau_buat.py tests/unit/test_dp_proforma_validasi.py tests/unit/test_pelanggan_satu_tenant.py tests/unit/test_so_audit_empat.py tests/unit/test_t203_plafon_dp.py tests/unit/test_void_baris_bank_satu.py tests/unit/test_void_rekon.py"
AK=$A/services/bankfeed_aksi.py; BTF=$A/routers/bank_transfers.py; CDF=$A/routers/customer_deposits.py
SAB=(
 's1 transfer: tautan ke bt sisi LAWAN (bukan sisi rekening ini)|'"$AK"'|s@p\["from_transaction_id"\] if m\["arah"\] == "keluar" else@p["from_transaction_id"] if m["arah"] == "masuk" else@|'"$T"
 's2 transfer: arah tertukar (keluar diperlakukan masuk)|'"$AK"'|s@(str(rekening_id), str(lawan)) if m\["arah"\] == "keluar" else@(str(rekening_id), str(lawan)) if m["arah"] == "masuk" else@|'"$T"
 's3 transfer: nominal jurnal tak diverifikasi ulang|'"$AK"'|s@if d != nominal or k != nominal or _bank_net@if False and _bank_net@|'"$T"
 's4 transfer: rekening lawan = rekening sendiri lolos|'"$AK"'|s@if str(lawan) == str(rekening_id):@if False:@|'"$T"
 's5 uang muka: so_id tak diteruskan ke penulis|'"$AK"'|s@sales_order_id=str(so) if so else None@sales_order_id=None@|'"$T"
 's6 uang muka/bayar: kunci idempotensi tanpa urutan (bentrok saat dicatat ulang)|'"$AK"'|s@{n_lama + 1}@{n_lama * 0 + 1}@|'"$T"
 's7 uang muka: arah keluar lolos|'"$AK"'|s@if jenis != "transfer" and m\["arah"\] != "masuk":@if False:@|'"$T"
 's8 uang muka: account_id memakai id rekening (bukan CoA)|'"$AK"'|s@account_id=str(rek\["coa_id"\]), bank_account_id@account_id=str(rek["id"]), bank_account_id@|'"$T"
 's9 bayar: bank_account_id memakai id rekening (bukan CoA)|'"$AK"'|s@payment_method="bank_transfer", bank_account_id=str(rek\["coa_id"\])@payment_method="bank_transfer", bank_account_id=str(rek["id"])@|'"$T"
 's10 bayar: alokasi melebihi nominal diterima|'"$AK"'|s@Decimal(0)) > nominal:@Decimal(0)) > nominal * 100:@|'"$T"
 's11 bayar: kelebihan tak ditampilkan di pratinjau|'"$AK"'|s@"nomor": ro.get("overpayment_deposit_number")} if sisa > 0 else None@"nomor": None} if False else None@|'"$T"
 's12 catat dokumen: bt hasil tak diperiksa tepat satu|'"$AK"'|s@if len(bts) != 1:@if False:@|'"$T"
 's13 catat dokumen: bt hasil tak ditautkan ke baris|'"$AK"'|s@await conn.execute(SQL_TAUTAN_INSERT, tid, m\["id"\], bt_id, m\["nominal"\], str(uid))@pass@|'"$T"
 's14 catat: GUC tenant tak dipasang sebelum penulis modul|'"$AK"'|s@        await GUC.pasang_tenant(conn, tid)@        pass@|'"$T"
 's15 catat: izin modul tujuan dicabut|'"$AK"'|s@if not await _izin(ctx, IZIN_BUAT\[jenis\]@if False and not await _izin(ctx, IZIN_BUAT[jenis]@|'"$T"
 's16 batalkan: izin void modul dokumen dicabut|'"$AK"'|s@if tipe in IZIN_VOID and not await _izin(@if False and not await _izin(@|'"$T"
 's17 batalkan: jenis dokumen tak dikenal lolos|'"$AK"'|s@if tipe not in TIPE_DOK:@if False:@|'"$T"
 's18 batalkan transfer: void tak dipanggil (hanya tautan dilepas)|'"$AK"'|s@await BTR.void_transfer_core(conn, ctx, did, alasan, rv)@pass@|'"$T"
 's19 batalkan uang muka: void tak dipanggil|'"$AK"'|s@await CD.void_deposit_core(conn, ctx, did, VoidCustomerDepositRequest(reason=alasan))@pass@|'"$T"
 's20 batalkan pembayaran: void tak ditulis|'"$AK"'|s@await RP._tulis_void_pembayaran(conn, ctx, did, alasan, rv)@pass@|'"$T"
 's21 batalkan: tautan tak dilepas sebelum void|'"$AK"'|s@await conn.execute(SQL_TAUTAN_LEPAS, tid, m\["id"\], str(uid))        # tautan DILEPAS dulu@pass        # tautan DILEPAS dulu@|'"$T"
 's22 batalkan pratinjau: pembalik uang muka/bayar tak ditukar (jurnal asli ditampilkan)|'"$AK"'|s@(x\["account_id"\], x\["credit"\], x\["debit"\], "Pembalik")@(x["account_id"], x["debit"], x["credit"], "Pembalik")@|'"$T"
 's23 batalkan pratinjau: penjaga tautan sendiri (BANK_TX_BERTAUT/DEPOSIT_RECONCILED) tak disaring|'"$AK"'|s@if not _penjaga_tautan(b\["code"\], b.get("message")):@if True:@|'"$T"
 's24 pelanggan tak ada lolos tanpa blok|'"$AK"'|s@bl.galat("PELANGGAN_TAK_ADA", 422, "Pelanggan tidak ditemukan.")@pass@|'"$T"
 's25 jenis yang belum ada (bayar_tagihan) lolos diam|'"$AK"'|s@JENIS_BELUM = ("bayar_tagihan",)@JENIS_BELUM = ()@|'"$T"
 's26 ekstraksi E1: transfer tak diposting walau auto_post|'"$BTF"'|s@post = await _post_transfer(conn, ctx, transfer_id) if body.auto_post else None@post = None@|'"$T"
 's27 ekstraksi E2: kunci void transfer dicabut|'"$BTF"'|s@await conn.fetchval("SELECT pg_advisory_xact_lock(hashtext($1))", f"BANK_TRANSFER_VOID:{transfer_id}")@pass@|'"$T"
 's28 ekstraksi E3: kunci DEPOSIT_POST sebelum posting dicabut|'"$CDF"'|s@f"DEPOSIT_POST:{dep_id}",@f"DEPOSIT_POST_X:{dep_id}",@|'"$T"
 's29 GUC tenant dipasang juga di jalur pratinjau (di luar jalur tulis dokumen)|'"$AK"'|s@^    await siapkan(pool, tid, aksi, body)@    await siapkan(pool, tid, aksi, body)\n    async with pool.acquire() as _c:\n        await GUC.pasang_tenant(_c, tid)@|'"$T"
 's30 GUC tenant dipasang untuk SEMUA aksi/jenis (jaga jenis dicabut)|'"$AK"'|s@    if aksi == "catat" and body.get("jenis") in JENIS_GUC:@    if True:@|'"$T"
)
