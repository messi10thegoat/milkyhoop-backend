"""UJI NYATA PAY-PREVIEW U1 (23 Okt 2026) -- kaos SAJA, tagihan uji 'UJI Maklun 3B09D3' BILL-2609-0003; SEMUA tulisan di SATU transaksi luar
yang SELALU di-ROLLBACK (handler mendapat koneksi itu lewat get_pool tertambal -> transaksinya jadi savepoint; nol commit). grapgrap tak disentuh.
MODE (env) dijalankan oleh .sh, tiap MODE = proses terpisah:
  JEPRET   keadaan BER-COMMIT (koneksi READ ONLY): hitungan/jumlah jurnal, cermin bank, pembayaran, alokasi, PPh, cache tagihan, hutang vendor,
           pencacah nomor (bill_payment_sequences + journal_number_sequences), idempotency_keys -> /out/<NAMA>.json
  TULIS    record_payment (POST /bills/{id}/payments) di pohon yang di-mount -> rekaman jurnal/baris/cermin bank/pembayaran/alokasi tanpa
           uuid & cap waktu -> /out/<NAMA>.json. Pohon BARU juga:
             (b1) pratinjau /bills/{id}/payments/preview (koneksi READ ONLY terpisah, SEBELUM tulis) == yang ditulis (nomor diganti)
             (b2) pratinjau POST /bill-payments/preview == POST /bill-payments tertulis (bayar 2.000, potongan 100, alokasi 1.600 ->
                  lebih bayar 500 -> baris uang muka vendor; cermin bank)
             (b3) blok: nominal > sisa -> pratinjau can_proceed=false MELEBIHI_SISA_TAGIHAN; tulis -> galat dengan pesan YANG SAMA
             (c)  pratinjau tak menyentuh pencacah: jepret pencacah sebelum == sesudah pratinjau (koneksi lain)
  BANDING  /out/lama.json == /out/baru.json (record_payment byte-identik: nomor BP-, akun, nominal, memo, cermin bank, alokasi)
           + /out/sebelum.json == /out/sesudah.json (NET 0 sesudah proses selesai)
MERAH=1        satu baris rencana pratinjau dimutasi -> (b1) WAJIB gagal
MERAH_BOCOR=1  TULIS menulis 1 baris idempotency_keys lewat koneksi TERPISAH yang di-commit -> BANDING net-0 WAJIB gagal (.sh membersihkan)."""
import asyncio, json, os, sys
from datetime import date
from decimal import Decimal
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "x")
import asyncpg
from starlette.requests import Request

T = "kaos-biru-konveksi"
NOMOR_TAGIHAN = "BILL-2609-0003"
REKENING = "56842900-0ae4-4c25-8ad5-d0726a36e21b"  # BCA Operasional kaos
TGL = date(2026, 10, 23)
MODE, NAMA = os.environ["MODE"], os.environ.get("NAMA", "")
MERAH, BOCOR = os.environ.get("MERAH") == "1", os.environ.get("MERAH_BOCOR") == "1"
KUNCI_BOCOR = "TES-E2E-PAYPREVIEW-BOCOR"
gagal = []


def cek(ok, l, i=""):
    print(("PASS " if ok else "FAIL ") + l + (f" | {i}" if i else ""), flush=True)
    if not ok: gagal.append(l)


def js(v):
    if isinstance(v, Decimal): return str(v.normalize()) if v == v.to_integral() else str(v)
    if isinstance(v, date): return v.isoformat()
    return str(v) if v is not None and not isinstance(v, (int, float, str, bool)) else v


def baris_dict(r, buang=()):
    return {k: js(v) for k, v in dict(r).items() if k not in buang}


def req(user_id):
    async def receive(): return {"type": "http.request", "body": b"", "more_body": False}
    r = Request({"type": "http", "method": "POST", "path": "/", "headers": [], "query_string": b""}, receive)
    r.state.user = {"tenant_id": T, "user_id": str(user_id)}; return r


SQL_JEPRET = {
    "jurnal": "SELECT count(*), COALESCE(sum(total_debit),0) FROM journal_entries WHERE tenant_id = $1",
    "baris": "SELECT count(*), COALESCE(sum(jl.debit),0), COALESCE(sum(jl.credit),0) FROM journal_lines jl JOIN journal_entries je ON je.id = jl.journal_id WHERE je.tenant_id = $1",
    "bank": "SELECT count(*), COALESCE(sum(amount),0) FROM bank_transactions WHERE tenant_id = $1",
    "bayar": "SELECT count(*), COALESCE(sum(total_amount),0) FROM bill_payments_v2 WHERE tenant_id = $1",
    "alokasi": "SELECT count(*), COALESCE(sum(a.amount_applied),0) FROM bill_payment_allocations a JOIN bill_payments_v2 p ON p.id = a.payment_id WHERE p.tenant_id = $1",
    "pph": "SELECT count(*) FROM withholding_tax_records WHERE tenant_id = $1",
    "tagihan": "SELECT amount_paid, status, status_v2, updated_at FROM bills WHERE tenant_id = $1 AND invoice_number = '" + NOMOR_TAGIHAN + "'",
    "hutang_vendor": "SELECT COALESCE(sum(o.outstanding),0) FROM compute_ap_outstanding($1) o JOIN bills b ON b.id = o.bill_id WHERE b.invoice_number = '" + NOMOR_TAGIHAN + "'",
    "seq_bayar": "SELECT json_agg(json_build_object('ym', year_month, 'n', last_number, 'u', updated_at) ORDER BY year_month) FROM bill_payment_sequences WHERE tenant_id = $1",
    "seq_jurnal": "SELECT json_agg(json_build_object('p', prefix, 'y', year, 'm', month, 'n', last_number, 'u', updated_at) ORDER BY prefix, year, month) FROM journal_number_sequences WHERE tenant_id = $1",
    "idem": "SELECT count(*) FROM idempotency_keys WHERE tenant_id = $1",
}


async def jepret(c, kunci=None):
    out = {}
    for k, q in SQL_JEPRET.items():
        if kunci and k not in kunci: continue
        r = await c.fetchrow(q, T)
        out[k] = [js(v) for v in r.values()]
    return out


async def rekam_pembayaran(c, payment_id):
    """Rekaman tulisan TANPA uuid acak & cap waktu (dibandingkan lama vs baru, dan pratinjau vs tulis)."""
    p = await c.fetchrow("SELECT * FROM bill_payments_v2 WHERE id = $1", payment_id)
    je = await c.fetchrow("SELECT * FROM journal_entries WHERE id = $1", p["journal_id"])
    jl = await c.fetch("""SELECT jl.line_number, coa.account_code, jl.debit, jl.credit, jl.memo FROM journal_lines jl
                          JOIN chart_of_accounts coa ON coa.id = jl.account_id WHERE jl.journal_id = $1 ORDER BY jl.line_number""", p["journal_id"])
    bt = await c.fetch("SELECT * FROM bank_transactions WHERE journal_id = $1 ORDER BY created_at", p["journal_id"])
    al = await c.fetch("SELECT * FROM bill_payment_allocations WHERE payment_id = $1 ORDER BY created_at", payment_id)
    acak = ("id", "created_at", "updated_at", "posted_at", "trace_id", "journal_id", "source_id", "payment_id", "reference_id",
            "content_hash", "previous_hash", "chain_sequence", "hash", "prev_hash")
    return {"pembayaran": baris_dict(p, acak), "jurnal": baris_dict(je, acak), "baris": [baris_dict(x) for x in jl],
            "bank": [baris_dict(x, acak) for x in bt], "alokasi": [baris_dict(x, acak) for x in al],
            "tagihan": baris_dict(await c.fetchrow("SELECT amount_paid, status FROM bills WHERE tenant_id = $1 AND invoice_number = $2", T, NOMOR_TAGIHAN))}


def rencana_vs_tulis(rencana, tulis, nomor):
    """Baris rencana pratinjau (kode akun, D, K, memo bernomor 'akan diberikan') vs baris tertulis."""
    r = [(x["account_code"], Decimal(str(x["debit"])), Decimal(str(x["credit"])), x["memo"].replace("(nomor saat disimpan)", nomor))
         for x in rencana]
    t = [(x["account_code"], Decimal(x["debit"]), Decimal(x["credit"]), x["memo"]) for x in tulis]
    return r == t, r, t


async def tulis():
    from app.routers import bill_payments as BP
    from app.routers import bills as BR
    from app.schemas.bills import RecordPaymentRequest
    baru = os.path.exists("/app/backend/api_gateway/app/services/bayar_keluar_rencana.py")
    c = await asyncpg.connect(os.environ["DATABASE_URL"])
    pool_ro = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=2,
                                        server_settings={"default_transaction_read_only": "on"})
    bill = await c.fetchrow("SELECT id, vendor_id FROM bills WHERE tenant_id = $1 AND invoice_number = $2", T, NOMOR_TAGIHAN)
    uid = await c.fetchval('SELECT id FROM "User" WHERE "tenantId" = $1 ORDER BY "createdAt" LIMIT 1', T)
    class CM:
        async def __aenter__(s): return c
        async def __aexit__(s, *a): return False
    class Tambal:
        def acquire(s): return CM()
    async def gp_tulis(): return Tambal()
    async def gp_ro(): return pool_ro
    hasil = {}
    badan = RecordPaymentRequest(amount=1000, payment_date=TGL, payment_method="transfer", bank_account_id=REKENING, reference="TES E2E PAY-PREVIEW")

    pv = pv2 = None
    if baru:
        from app.schemas.bill_payments import CreateBillPaymentRequest
        BR.get_pool = gp_ro; BP.get_pool = gp_ro
        async with pool_ro.acquire() as r0: c0 = await jepret(r0, ("seq_bayar", "seq_jurnal", "idem", "jurnal", "bank", "bayar"))
        pv = (await BR.preview_record_payment(req(uid), bill["id"], badan))["data"]
        bp_badan = CreateBillPaymentRequest(vendor_id=str(bill["vendor_id"]), payment_date=TGL, bank_account_id=REKENING, total_amount=2000,
                                            discount_amount=100, reference_number="TES E2E PAY-PREVIEW",
                                            allocations=[{"bill_id": str(bill["id"]), "amount_applied": 1600}])
        pv2 = (await BP.preview_create_bill_payment(req(uid), bp_badan))["data"]
        lebih = RecordPaymentRequest(amount=999999, payment_date=TGL, payment_method="transfer", bank_account_id=REKENING)
        pv3 = (await BR.preview_record_payment(req(uid), bill["id"], lebih))["data"]
        async with pool_ro.acquire() as r0: c1 = await jepret(r0, ("seq_bayar", "seq_jurnal", "idem", "jurnal", "bank", "bayar"))
        cek(c0 == c1, "(c) pratinjau tak menyentuh pencacah nomor / jurnal / bank / pembayaran (koneksi lain, sebelum == sesudah)",
            f"seq_bayar+seq_jurnal BP sama; jurnal {c0['jurnal']} bank {c0['bank']} bayar {c0['bayar']} idem {c0['idem']}" if c0 == c1 else f"{c0} / {c1}")
        cek(pv["can_proceed"] and pv["journals"][0]["journal_number"] is None and pv["after"]["payment_number"] is None,
            "(c) pratinjau tanpa nomor (journal_number & payment_number null)")
        cek(not pv3["can_proceed"] and [b["code"] for b in pv3["blocks"]] == ["MELEBIHI_SISA_TAGIHAN"], "(b3) pratinjau nominal > sisa -> blok MELEBIHI_SISA_TAGIHAN",
            str(pv3["blocks"]))
        if MERAH:
            pv["journals"][0]["lines"][0]["debit"] += 1

    BR.get_pool = gp_tulis; BP.get_pool = gp_tulis
    tx = c.transaction(); await tx.start()
    try:
        if BOCOR:  # kontrol merah kebocoran: koneksi TERPISAH yang COMMIT
            b = await asyncpg.connect(os.environ["DATABASE_URL"])
            await b.execute("INSERT INTO idempotency_keys (key, tenant_id, expires_at) VALUES ($1, $2, NOW() + interval '1 hour')", KUNCI_BOCOR, T)
            await b.close()
        # tiap tulis di savepoint SENDIRI yang di-rollback sebelum langkah berikut -> tiap tulis melihat keadaan YANG SAMA dengan pratinjau
        sp0 = c.transaction(); await sp0.start()
        try:
            r = await BR.record_payment(req(uid), bill["id"], badan)
            hasil["record_payment"] = await rekam_pembayaran(c, r["data"]["id"])
        finally:
            await sp0.rollback()
        if baru:
            rp = hasil["record_payment"]
            nomor = rp["pembayaran"]["payment_number"]
            ok, a, b_ = rencana_vs_tulis(pv["journals"][0]["lines"], rp["baris"], nomor)
            cek(ok, "(b1) /bills/{id}/payments: baris rencana pratinjau == baris tertulis", f"{a} / {b_}")
            cek(nomor.startswith("BP-") and rp["jurnal"]["journal_number"] == nomor, "(b1) nomor jalur lama tetap BP- = nomor jurnal", nomor)
            bt = rp["bank"][0] if rp["bank"] else {}
            cek(len(rp["bank"]) == 1 and Decimal(str(pv["bank_transactions"][0]["amount"])) == Decimal(bt.get("amount", "x"))
                and pv["bank_transactions"][0]["transaction_type"] == bt.get("transaction_type"), "(b1) cermin bank rencana == tertulis",
                f"{pv['bank_transactions']} / {bt.get('amount')} {bt.get('transaction_type')}")
            al = rp["alokasi"][0]
            pb = pv["bills"][0]
            cek(Decimal(str(pb["remaining_before"])) == Decimal(al["remaining_before"]) and Decimal(str(pb["remaining_after"])) == Decimal(al["remaining_after"]),
                "(b1) sisa tagihan sebelum->sesudah rencana == alokasi tertulis", f"{pb} / {al}")
            # (b3) tulis nominal > sisa -> pesan SAMA dengan pratinjau
            from fastapi import HTTPException
            sp = c.transaction(); await sp.start()
            try:
                await BR.record_payment(req(uid), bill["id"], RecordPaymentRequest(amount=999999, payment_date=TGL, payment_method="transfer",
                                                                                  bank_account_id=REKENING))
                cek(False, "(b3) tulis nominal > sisa ditolak")
            except HTTPException as e:
                cek(e.status_code == 400 and e.detail == pv3["blocks"][0]["message"], "(b3) tulis menolak dengan pesan == pratinjau", str(e.detail))
            finally:
                await sp.rollback()
            # (b2) POST /bill-payments
            sp2 = c.transaction(); await sp2.start()
            try:
                r2 = await BP.create_bill_payment(req(uid), bp_badan)
                w2 = await rekam_pembayaran(c, r2["data"]["id"])
            finally:
                await sp2.rollback()
            n2 = w2["pembayaran"]["payment_number"]
            ok2, a2, b2 = rencana_vs_tulis(pv2["journals"][0]["lines"], w2["baris"], n2)
            cek(ok2 and pv2["can_proceed"], "(b2) /bill-payments: baris rencana pratinjau == baris tertulis (Dr Hutang, Dr Uang muka vendor, Cr Bank, Cr Potongan)",
                f"{a2} / {b2}")
            cek(pv2["journals"][0]["description"].replace("(nomor saat disimpan)", n2) == w2["jurnal"]["description"], "(b2) deskripsi jurnal rencana == tertulis")
            bt2 = w2["bank"][0] if w2["bank"] else {}
            cek(len(w2["bank"]) == 1 and Decimal(str(pv2["bank_transactions"][0]["amount"])) == Decimal(bt2.get("amount", "x")),
                "(b2) cermin bank rencana == tertulis", f"{pv2['bank_transactions']} / {bt2.get('amount')}")
            cek([(Decimal(str(x["remaining_before"])), Decimal(str(x["remaining_after"]))) for x in pv2["bills"]]
                == [(Decimal(x["remaining_before"]), Decimal(x["remaining_after"])) for x in w2["alokasi"]], "(b2) alokasi rencana == tertulis")
            cek(Decimal(w2["pembayaran"]["unapplied_amount"]) == Decimal(str(pv2["after"]["unapplied_amount"])) == 500, "(b2) lebih bayar 500 rencana == tertulis")
    finally:
        await tx.rollback(); await c.close(); await pool_ro.close()
    json.dump(hasil, open(f"/out/{NAMA}.json", "w"), indent=1, default=str)
    print(f"RINGKAS gagal={len(gagal)} {gagal[:6]}"); sys.exit(1 if gagal else 0)


async def main():
    if MODE == "JEPRET":
        c = await asyncpg.connect(os.environ["DATABASE_URL"], server_settings={"default_transaction_read_only": "on"})
        json.dump(await jepret(c), open(f"/out/{NAMA}.json", "w"), indent=1, default=str); await c.close()
        print(f"JEPRET {NAMA} ok"); return
    if MODE == "TULIS":
        return await tulis()
    if MODE == "BANDING":
        lama, baru = json.load(open("/out/lama.json")), json.load(open("/out/baru.json"))
        for k in ("pembayaran", "jurnal", "baris", "bank", "alokasi", "tagihan"):
            cek(lama["record_payment"][k] == baru["record_payment"][k], f"(a) record_payment LAMA == BARU: {k}",
                "" if lama["record_payment"][k] == baru["record_payment"][k] else f"{lama['record_payment'][k]} / {baru['record_payment'][k]}")
        s, e = json.load(open("/out/sebelum.json")), json.load(open("/out/sesudah.json"))
        for k in SQL_JEPRET:
            cek(s[k] == e[k], f"NET 0 (sesudah proses selesai): {k}", "" if s[k] == e[k] else f"{s[k]} / {e[k]}")
        print(f"RINGKAS gagal={len(gagal)} {gagal[:6]}"); sys.exit(1 if gagal else 0)

asyncio.run(main())
