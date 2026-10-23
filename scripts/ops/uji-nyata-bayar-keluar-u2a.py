"""UJI NYATA U2a (23 Okt 2026) -- kaos SAJA, tagihan uji 'UJI Maklun 3B09D3' (BILL-2609-0003 posted, BILL-2609-0007 DRAF); SEMUA tulisan di
SATU transaksi luar yang SELALU di-ROLLBACK (get_pool tertambal -> transaksi handler = savepoint; nol commit). grapgrap tak disentuh.
MODE (env), tiap MODE proses terpisah:
  JEPRET  keadaan BER-COMMIT (READ ONLY) -> /out/<NAMA>.json (jurnal, baris, bank, pembayaran, alokasi, PPh, cache tagihan, hutang, pencacah, idem)
  TULIS   di pohon yang di-mount:
    (p) BYTE-COMPARE dua jalur: A = POST /bill-payments buat-posted (PPh23 2%: pph 20, total 1.000) vs B = POST /bill-payments draf lalu
        POST /bill-payments/{id}/post, badan SAMA, masing-masing di savepoint sendiri dari keadaan yang sama -> jurnal + baris, cermin bank,
        rekaman PPh, alokasi, cache tagihan WAJIB identik (tanpa uuid & cap waktu; nomor PAY-/jurnal sama karena savepoint di-rollback)
    (r) cek ulang sisa: draf 1.000 ke BILL-2609-0003, lalu pembayaran posted lain melunasi seluruh sisa, lalu posting draf -> 400
        "Amount exceeds remaining" (pesan == buat-posted)
    (q) record_payment atas BILL-2609-0007 (DRAF) -> 422 "Tagihan masih draf…"; pratinjau /bills/{id}/payments/preview -> blok TAGIHAN_DRAF
  BANDING /out/sebelum.json == /out/sesudah.json (NET 0 sesudah proses selesai)
KONTROL MERAH = MODE TULIS di pohon UTAMA LAMA (sebelum U2a): (p) WAJIB gagal (posting draf tanpa cermin bank/PPh), (r) gagal (draf lolos),
(q) gagal (tagihan draf diterima). .sh menjalankannya dengan MERAH_LAMA=1."""
import asyncio, json, os, sys
from datetime import date
from decimal import Decimal
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "x")
import asyncpg
from starlette.requests import Request

T = "kaos-biru-konveksi"
TAGIHAN, DRAF = "BILL-2609-0003", "BILL-2609-0007"
REKENING = "56842900-0ae4-4c25-8ad5-d0726a36e21b"  # BCA Operasional kaos
TGL = date(2026, 10, 23)
MODE, NAMA = os.environ["MODE"], os.environ.get("NAMA", "")
gagal = []


def cek(ok, l, i=""):
    print(("PASS " if ok else "FAIL ") + l + (f" | {i}" if i else ""), flush=True)
    if not ok: gagal.append(l)


def js(v):
    if isinstance(v, Decimal): return str(v.normalize()) if v == v.to_integral() else str(v.normalize())
    if isinstance(v, date): return v.isoformat()
    return str(v) if v is not None and not isinstance(v, (int, float, str, bool)) else v


ACAK = ("id", "created_at", "updated_at", "posted_at", "trace_id", "journal_id", "source_id", "payment_id", "reference_id", "document_id",
        "content_hash", "previous_hash", "chain_sequence", "hash", "prev_hash")


def bd(r): return {k: js(v) for k, v in dict(r).items() if k not in ACAK}


def req(uid):
    async def receive(): return {"type": "http.request", "body": b"", "more_body": False}
    r = Request({"type": "http", "method": "POST", "path": "/", "headers": [], "query_string": b""}, receive)
    r.state.user = {"tenant_id": T, "user_id": str(uid)}; return r


SQL_JEPRET = {
    "jurnal": "SELECT count(*), COALESCE(sum(total_debit),0) FROM journal_entries WHERE tenant_id = $1",
    "baris": "SELECT count(*), COALESCE(sum(jl.debit),0) FROM journal_lines jl JOIN journal_entries je ON je.id = jl.journal_id WHERE je.tenant_id = $1",
    "bank": "SELECT count(*), COALESCE(sum(amount),0) FROM bank_transactions WHERE tenant_id = $1",
    "bayar": "SELECT count(*), COALESCE(sum(total_amount),0) FROM bill_payments_v2 WHERE tenant_id = $1",
    "alokasi": "SELECT count(*) FROM bill_payment_allocations a JOIN bill_payments_v2 p ON p.id = a.payment_id WHERE p.tenant_id = $1",
    "pph": "SELECT count(*), COALESCE(sum(tax_amount),0) FROM withholding_tax_records WHERE tenant_id = $1",
    "tagihan": "SELECT json_agg(json_build_object('n', invoice_number, 'p', amount_paid, 's', status, 'u', updated_at) ORDER BY invoice_number) FROM bills WHERE tenant_id = $1 AND invoice_number IN ('" + TAGIHAN + "','" + DRAF + "')",
    "hutang": "SELECT COALESCE(sum(outstanding),0) FROM compute_ap_outstanding($1)",
    "seq_bayar": "SELECT json_agg(json_build_object('ym', year_month, 'n', last_number) ORDER BY year_month) FROM bill_payment_sequences WHERE tenant_id = $1",
    "seq_jurnal": "SELECT json_agg(json_build_object('p', prefix, 'y', year, 'm', month, 'n', last_number) ORDER BY prefix, year, month) FROM journal_number_sequences WHERE tenant_id = $1",
    "idem": "SELECT count(*) FROM idempotency_keys WHERE tenant_id = $1",
}


async def jepret(c):
    return {k: [js(v) for v in (await c.fetchrow(q, T)).values()] for k, q in SQL_JEPRET.items()}


async def rekam(c, pid):
    p = await c.fetchrow("SELECT * FROM bill_payments_v2 WHERE id = $1", pid)
    je = await c.fetchrow("SELECT * FROM journal_entries WHERE id = $1", p["journal_id"]) if p["journal_id"] else None
    jl = await c.fetch("""SELECT jl.line_number, coa.account_code, jl.debit, jl.credit, jl.memo FROM journal_lines jl JOIN chart_of_accounts coa
                          ON coa.id = jl.account_id WHERE jl.journal_id = $1 ORDER BY jl.line_number""", p["journal_id"]) if p["journal_id"] else []
    bt = await c.fetch("SELECT * FROM bank_transactions WHERE reference_id = $1 AND tenant_id = $2 ORDER BY created_at", pid, T)
    wh = await c.fetch("SELECT * FROM withholding_tax_records WHERE payment_id = $1 ORDER BY created_at", pid)
    al = await c.fetch("SELECT * FROM bill_payment_allocations WHERE payment_id = $1 ORDER BY created_at", pid)
    tg = await c.fetchrow("SELECT amount_paid, status FROM bills WHERE tenant_id = $1 AND invoice_number = $2", T, TAGIHAN)
    return {"jurnal": bd(je) if je else None, "baris": [bd(x) for x in jl], "bank": [bd(x) for x in bt], "pph": [bd(x) for x in wh],
            "alokasi": [bd(x) for x in al], "tagihan": bd(tg), "nomor": p["payment_number"], "status": p["status"]}


async def tulis():
    from fastapi import HTTPException
    from app.routers import bill_payments as BP
    from app.routers import bills as BR
    from app.schemas.bill_payments import CreateBillPaymentRequest
    from app.schemas.bills import RecordPaymentRequest
    c = await asyncpg.connect(os.environ["DATABASE_URL"])
    bill = await c.fetchrow("SELECT id, vendor_id FROM bills WHERE tenant_id = $1 AND invoice_number = $2", T, TAGIHAN)
    draf = await c.fetchrow("SELECT id FROM bills WHERE tenant_id = $1 AND invoice_number = $2", T, DRAF)
    pph_tc = await c.fetchval("SELECT id FROM tax_codes WHERE (tenant_id = $1 OR tenant_id IS NULL) AND code = 'PPH23-2' ORDER BY tenant_id NULLS LAST LIMIT 1", T)
    assert pph_tc, "kode PPH23-2 tak ada"
    uid = await c.fetchval('SELECT id FROM "User" WHERE "tenantId" = $1 ORDER BY "createdAt" LIMIT 1', T)
    class CM:
        async def __aenter__(s): return c
        async def __aexit__(s, *a): return False
    class Tambal:
        def acquire(s): return CM()
    async def gp(): return Tambal()
    BP.get_pool = gp; BR.get_pool = gp

    def badan(draft, total=1000, alok=1000, pph=20):
        return CreateBillPaymentRequest(vendor_id=str(bill["vendor_id"]), payment_date=TGL, bank_account_id=REKENING, total_amount=total,
                                        pph_tax_code_id=str(pph_tc) if pph else None, pph_amount=pph, reference_number="TES E2E U2a",
                                        save_as_draft=draft, allocations=[{"bill_id": str(bill["id"]), "amount_applied": alok}])
    tx = c.transaction(); await tx.start()
    try:
        async def di_savepoint(fn):
            sp = c.transaction(); await sp.start()
            try:
                return await fn()
            finally:
                await sp.rollback()

        async def jalur_a():
            r = await BP.create_bill_payment(req(uid), badan(False))
            return await rekam(c, r["data"]["id"])

        async def jalur_b():
            r = await BP.create_bill_payment(req(uid), badan(True))
            pid = r["data"]["id"]
            await BP.post_bill_payment(req(uid), pid)
            return await rekam(c, pid)
        a = await di_savepoint(jalur_a)
        b = await di_savepoint(jalur_b)
        cek(a["status"] == b["status"] == "posted" and a["nomor"] == b["nomor"], "(p) kedua jalur posted, nomor sama", f"{a['nomor']} / {b['nomor']} {b['status']}")
        for k in ("jurnal", "baris", "bank", "pph", "alokasi", "tagihan"):
            cek(a[k] == b[k] and (k not in ("bank", "pph") or len(a[k]) == 1), f"(p) buat-posted == draf->posting: {k}",
                "" if a[k] == b[k] else f"A {a[k]} / B {b[k]}")

        async def jalur_r():
            r = await BP.create_bill_payment(req(uid), badan(True, pph=0))
            sisa = await c.fetchval("SELECT outstanding FROM compute_ap_rincian($1) WHERE bill_id = $2", T, bill["id"])
            await BP.create_bill_payment(req(uid), badan(False, total=int(sisa), alok=int(sisa), pph=0))
            try:
                await BP.post_bill_payment(req(uid), r["data"]["id"])
                return "LOLOS"
            except HTTPException as e:
                return (e.status_code, e.detail)
        hr = await di_savepoint(jalur_r)
        cek(hr == (400, "Amount exceeds remaining"), "(r) posting draf sesudah tagihan lunas ditolak 400 (pesan == buat-posted)", str(hr))

        async def jalur_q():
            try:
                await BR.record_payment(req(uid), draf["id"], RecordPaymentRequest(amount=1000, payment_date=TGL, payment_method="transfer",
                                                                                   bank_account_id=REKENING))
                return "LOLOS"
            except HTTPException as e:
                return (e.status_code, e.detail)
        hq = await di_savepoint(jalur_q)
        cek(isinstance(hq, tuple) and hq[0] == 422 and "draf" in str(hq[1]).lower(), "(q) record_payment tagihan DRAF -> 422 pesan jelas", str(hq))
        if hasattr(BR, "preview_record_payment"):
            pool_ro = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=1,
                                                server_settings={"default_transaction_read_only": "on"})
            async def gp_ro(): return pool_ro
            BR.get_pool = gp_ro
            pv = (await BR.preview_record_payment(req(uid), draf["id"], RecordPaymentRequest(amount=1000, payment_date=TGL,
                                                                                            payment_method="transfer", bank_account_id=REKENING)))["data"]
            BR.get_pool = gp; await pool_ro.close()
            cek(not pv["can_proceed"] and pv["blocks"][0]["code"] == "TAGIHAN_DRAF", "(q) pratinjau tagihan draf -> blok TAGIHAN_DRAF", str(pv["blocks"]))
    finally:
        await tx.rollback(); await c.close()
    print(f"RINGKAS gagal={len(gagal)} {gagal[:6]}"); sys.exit(1 if gagal else 0)


async def main():
    if MODE == "JEPRET":
        c = await asyncpg.connect(os.environ["DATABASE_URL"], server_settings={"default_transaction_read_only": "on"})
        json.dump(await jepret(c), open(f"/out/{NAMA}.json", "w"), default=str); await c.close(); print(f"JEPRET {NAMA} ok"); return
    if MODE == "TULIS":
        return await tulis()
    if MODE == "BANDING":
        s, e = json.load(open("/out/sebelum.json")), json.load(open(f"/out/{NAMA}.json"))
        for k in SQL_JEPRET:
            cek(s[k] == e[k], f"NET 0 ({NAMA}, sesudah proses selesai): {k}", "" if s[k] == e[k] else f"{s[k]} / {e[k]}")
        print(f"RINGKAS gagal={len(gagal)} {gagal[:6]}"); sys.exit(1 if gagal else 0)

asyncio.run(main())
