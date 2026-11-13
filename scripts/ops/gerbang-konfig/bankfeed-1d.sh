# Konfig gerbang terfokus BF-1d (catat bayar_tagihan + batalkan BILL_PAYMENT; ekstraksi E4/E5 di routers/bill_payments; jurnal HANYA lewat penulis modul; tanpa migrasi).
# Pemisah sabotase = "|", ekspresi sed memakai "@". Alamat rentang /awal/,/akhir/ dipakai agar sabotase hanya mengenai fungsi bayar_tagihan.
W=/root/mh-bf1d; CABANG=feat/bankfeed-bf1d-bayar-r2; NAMA_JENDELA=bankfeed-1d
POLA_UNIT='bankfeed|mutasi_bank|bank_transfers|customer_deposits|receive_payments|bill_payments|bayar_keluar|buat_bayar_core|void_bayar_core|buat_transfer_core|void_transfer_core|buat_dp_core|guc_tenant|jaga_rekonsiliasi|tolak_void_bila|inventaris_rute_tulis|gate_izin|include_router|izin_untuk_jalur|izin_rute_pengguna'
HARNESS=scripts/ops/uji-salinan-bankfeed-1d.sh
PENANDA_BERKAS=services/bankfeed_aksi.py; PENANDA='def _rencana_bayar_tagihan('
A=backend/api_gateway/app
T="tests/unit/test_bankfeed_catat_bayar.py tests/unit/test_ekstraksi_bf1d.py tests/unit/test_bankfeed_catat3.py tests/unit/test_bankfeed_catat.py tests/unit/test_bankfeed_aksi.py tests/unit/test_guc_tenant_pagar.py tests/unit/test_ekstraksi_c3.py tests/unit/test_void_rekon.py tests/unit/test_void_baris_bank_satu.py"
AK=$A/services/bankfeed_aksi.py; BPF=$A/routers/bill_payments.py
R='/async def _rencana_bayar_tagihan/,/^async def _tulis_dokumen/'
SAB=(
 'b1 alokasi melebihi nominal diterima|'"$AK"'|'"$R"'s@Decimal(0)) > nominal:@Decimal(0)) > nominal * 100:@|'"$T"
 'b2 arah masuk lolos untuk bayar_tagihan|'"$AK"'|s@    if wajib and m\["arah"\] != wajib:@    if False:@|'"$T"
 'b3 vendor tak ada lolos tanpa blok|'"$AK"'|s@bl.galat("VENDOR_TAK_ADA", 422, "Vendor tidak ditemukan.")@pass@|'"$T"
 'b4 nominal menurut rencana tak diverifikasi ulang|'"$AK"'|s@    if bank and sum(@    if False and sum(@|'"$T"
 'b5 tagihan ber-PPh lolos (blok TAGIHAN_BER_PPH dicabut)|'"$AK"'|s@    if Decimal(str((r.get("pph") or {}).get("amount") or 0)) > 0:@    if False:@|'"$T"
 'b6 bank_account_id memakai CoA (bukan bank_accounts.id)|'"$AK"'|s@bank_account_id=str(rek\["id"\]), bank_account_name=rek\["account_name"\], total_amount=nominal, discount_amount=0@bank_account_id=str(rek["coa_id"]), bank_account_name=rek["account_name"], total_amount=nominal, discount_amount=0@|'"$T"
 'b7 kunci idempotensi dokumen tanpa urutan (bentrok saat dicatat ulang)|'"$AK"'|'"$R"'s@{n_lama + 1}@{n_lama * 0 + 1}@|'"$T"
 'b8 kunci domain BILL_PAYMENT_CREATE dicabut|'"$AK"'|/BILL_PAYMENT_CREATE:{tid}/s@.*@        pass@|'"$T"
 'b9 bt hasil tak diperiksa tepat satu|'"$AK"'|s@if len(bts) != 1:@if False:@|'"$T"
 'b10 bt hasil tak ditautkan ke baris|'"$AK"'|s@await conn.execute(SQL_TAUTAN_INSERT, tid, m\["id"\], bt_id, m\["nominal"\], str(uid))@pass@|'"$T"
 'b11 izin buat modul tujuan dicabut|'"$AK"'|s@if not await _izin(ctx, IZIN_BUAT\[jenis\]@if False and not await _izin(ctx, IZIN_BUAT[jenis]@|'"$T"
 'b12 izin void modul dokumen dicabut|'"$AK"'|s@if tipe in IZIN_VOID and not await _izin(@if False and not await _izin(@|'"$T"
 'b13 batalkan: void pembayaran tagihan tak ditulis|'"$AK"'|s@await BP.void_bayar_core(conn, ctx, did, alasan, rv)@pass@|'"$T"
 'b14 batalkan: tautan tak dilepas sebelum void|'"$AK"'|s@await conn.execute(SQL_TAUTAN_LEPAS, tid, m\["id"\], str(uid))        # tautan DILEPAS dulu@pass        # tautan DILEPAS dulu@|'"$T"
 'b15 batalkan pratinjau: pembalik tak ditukar|'"$AK"'|s@(x\["account_id"\], x\["credit"\], x\["debit"\], "Pembalik")@(x["account_id"], x["debit"], x["credit"], "Pembalik")@|'"$T"
 'b16 batalkan pratinjau: penjaga tautan tak disaring|'"$AK"'|s@if not _penjaga_tautan(b\["code"\], b.get("message")):@if True:@|'"$T"
 'b17 kunci void pembayaran (kunci_void_bayar) dicabut di Bank Feed|'"$AK"'|s@await BP.kunci_void_bayar(conn, did)@pass@|'"$T"
 'b18 E4: posting jurnal+cermin bank (efek_posting_bayar) dicabut|'"$BPF"'|s@await efek_posting_bayar(@(lambda *a, **k: None)(@|'"$T"
 'b19 E5: cache tagihan tak dihitung ulang saat void|'"$BPF"'|s@await segarkan_cache_hutang_tagihan(conn, ctx\["tenant_id"\], alloc\["bill_id"\])@pass@|'"$T"
 'b20 GUC tenant dipasang juga di jalur pratinjau|'"$AK"'|s@^    await siapkan(pool, tid, aksi, body)@    await siapkan(pool, tid, aksi, body)\n    async with pool.acquire() as _c:\n        await GUC.pasang_tenant(_c, tid)@|'"$T"
 'b21 JENIS_BELUM masih memuat bayar_tagihan|'"$AK"'|s@JENIS_BELUM = ()@JENIS_BELUM = ("bayar_tagihan",)@|'"$T"
 'b22 diskon terisi (bukan nol)|'"$AK"'|s@total_amount=nominal, discount_amount=0, bank_fee_amount=0,@total_amount=nominal, discount_amount=1, bank_fee_amount=0,@|'"$T"
 'b23 BILL_PAYMENT hilang dari IZIN_VOID/TIPE_DOK|'"$AK"'|s@, "BILL_PAYMENT": "/api/bill-payments/{}/void"}@}@|'"$T"
 'b24 kelebihan tak ditampilkan di pratinjau|'"$AK"'|s@h\["kelebihan"\] = {"jumlah": str(sisa.quantize(SEN)), "nomor": None} if sisa > 0 else None@h["kelebihan"] = None@|'"$T"
 'b25 E4: alokasi pembayaran tak ditulis|'"$BPF"'|s@INSERT INTO bill_payment_allocations (@INSERT INTO bill_payment_alokasi (@|'"$T"
 'b26 faktur ganda dalam alokasi lolos|'"$AK"'|s@ or bid in ada:@:@|'"$T"
 'b27 sen: NOMINAL_BERSEN berlaku juga untuk bayar_tagihan|'"$AK"'|s@    if jenis != "bayar_tagihan" and m\["nominal"\]@    if m["nominal"]@|'"$T"
)
