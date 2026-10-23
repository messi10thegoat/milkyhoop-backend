"""UJI NYATA idempotensi Nota Debit (23 Okt 2026, putusan pemilik LANGSUNG). kaos SAJA; ND sintetis 'TES E2E' lewat HANDLER NYATA di SATU
transaksi luar yang SELALU di-ROLLBACK; net 0 sesudah proses (JEPRET sebelum/sesudah). Per aksi (post, receive-refund, apply, unapply, void):
  KUNCI SAMA dua kali -> jawaban kedua == jawaban pertama (replay) dan tulisan HANYA sekali (hitungan jurnal/refund/aplikasi/bank)
  KUNCI SAMA isi beda -> 409 IDEMPOTENCY_KEY_REUSED; TANPA kunci -> perilaku lama (refund ganda sebagian tetap lolos = dua refund)
MERAH=1 : header kunci tak dikirim pada uji replay -> refund ganda tercatat -> WAJIB gagal."""
import asyncio, json, os, sys
from datetime import date
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "x")
import asyncpg
from starlette.requests import Request

T, TAGIHAN, TGL = "kaos-biru-konveksi", "BILL-2609-0003", date(2026, 10, 23)
REKENING = "56842900-0ae4-4c25-8ad5-d0726a36e21b"
MODE, NAMA = os.environ["MODE"], os.environ.get("NAMA", "")
MERAH = os.environ.get("MERAH") == "1"
gagal = []


def cek(ok, l, i=""):
    print(("PASS " if ok else "FAIL ") + l + (f" | {i}" if i else ""), flush=True)
    if not ok: gagal.append(l)


def req(uid, kunci=None):
    async def receive(): return {"type": "http.request", "body": b"", "more_body": False}
    h = [(b"x-idempotency-key", kunci.encode())] if kunci and not MERAH else []
    r = Request({"type": "http", "method": "POST", "path": "/", "headers": h, "query_string": b""}, receive)
    r.state.user = {"tenant_id": T, "user_id": str(uid)}; return r


SQL_JEPRET = {
    "vc": "SELECT count(*) FROM vendor_credits WHERE tenant_id = $1",
    "vca": "SELECT count(*) FROM vendor_credit_applications WHERE tenant_id = $1",
    "vcr": "SELECT count(*) FROM vendor_credit_refunds WHERE tenant_id = $1",
    "jurnal": "SELECT count(*) FROM journal_entries WHERE tenant_id = $1",
    "bank": "SELECT count(*) FROM bank_transactions WHERE tenant_id = $1",
    "idem": "SELECT count(*) FROM idempotency_keys WHERE tenant_id = $1",
    "seq_jurnal": "SELECT json_agg(json_build_object('p', prefix, 'y', year, 'm', month, 'n', last_number) ORDER BY prefix, year, month) FROM journal_number_sequences WHERE tenant_id = $1",
}


async def hitung(c, vid):
    return {"jurnal": await c.fetchval("SELECT count(*) FROM journal_entries WHERE tenant_id = $1 AND source_id = $2", T, vid),
            "refund": await c.fetchval("SELECT count(*) FROM vendor_credit_refunds WHERE vendor_credit_id = $1", vid),
            "aplikasi": await c.fetchval("SELECT count(*) FROM vendor_credit_applications WHERE vendor_credit_id = $1", vid),
            "bank": await c.fetchval("SELECT count(*) FROM bank_transactions WHERE tenant_id = $1 AND reference_number = (SELECT credit_number FROM vendor_credits WHERE id = $2)", T, vid)}


async def tulis():
    from fastapi import HTTPException
    from app.routers import vendor_credits as VR
    from app.schemas.vendor_credits import (CreateVendorCreditRequest, ApplyVendorCreditRequest, UnapplyVendorCreditRequest,
                                            ReceiveRefundRequest, VoidVendorCreditRequest)
    c = await asyncpg.connect(os.environ["DATABASE_URL"])
    bill = await c.fetchrow("SELECT id, vendor_id, vendor_name FROM bills WHERE tenant_id = $1 AND invoice_number = $2", T, TAGIHAN)
    uid = await c.fetchval('SELECT id FROM "User" WHERE "tenantId" = $1 ORDER BY "createdAt" LIMIT 1', T)
    class CM:
        async def __aenter__(s): return c
        async def __aexit__(s, *a): return False
    class P:
        def acquire(s): return CM()
    async def gp(): return P()
    VR.get_pool = gp
    tx = c.transaction(); await tx.start()
    try:
        async def buat(n):
            r = await VR.create_vendor_credit(req(uid), CreateVendorCreditRequest(
                vendor_id=str(bill["vendor_id"]), vendor_name=bill["vendor_name"], vendor_credit_date=TGL, reason="other", ref_no="TES E2E idem",
                notes="TES E2E idem (rollback)", items=[{"description": "TES E2E", "quantity": 1, "unit_price": n}]))
            return r["data"]["id"] if isinstance(r, dict) else r.data["id"]

        async def dua_kali(label, vid, fn, *args):
            h0 = await hitung(c, vid)
            r1 = await fn(req(uid, f"k-{label}"), vid, *args)
            h1 = await hitung(c, vid)
            r2 = await fn(req(uid, f"k-{label}"), vid, *args)
            h2 = await hitung(c, vid)
            j = lambda r: json.dumps(r if isinstance(r, dict) else r.model_dump(), sort_keys=True, default=str)  # noqa: E731
            cek(j(r1) == j(r2) and h1 == h2 and h1 != h0, f"({label}) kunci sama dua kali -> replay, tulisan sekali", f"{h0} -> {h1} -> {h2}")

        v1 = await buat(3000)
        await dua_kali("post", v1, VR.post_vendor_credit)
        rb = ReceiveRefundRequest(amount=1000, refund_date=TGL, payment_method="transfer", bank_account_id=REKENING, reference="TES E2E idem")
        await dua_kali("receive-refund", v1, VR.receive_refund, rb)
        try:
            await VR.receive_refund(req(uid, "k-receive-refund"), v1, ReceiveRefundRequest(amount=500, refund_date=TGL, payment_method="transfer",
                                                                                            bank_account_id=REKENING))
            h409 = "LOLOS"
        except HTTPException as e:
            h409 = (e.status_code, (e.detail or {}).get("code") if isinstance(e.detail, dict) else e.detail)
        cek(MERAH or h409 == (409, "IDEMPOTENCY_KEY_REUSED"), "(receive-refund) kunci sama isi beda -> 409 IDEMPOTENCY_KEY_REUSED", str(h409))
        h0 = await hitung(c, v1)
        await VR.receive_refund(req(uid), v1, rb)
        cek((await hitung(c, v1))["refund"] == h0["refund"] + 1, "(receive-refund) TANPA kunci -> perilaku lama (refund baru tercatat)")
        v2 = await buat(1000)
        await VR.post_vendor_credit(req(uid), v2)
        ab = ApplyVendorCreditRequest(applications=[{"bill_id": str(bill["id"]), "amount": 1000}], application_date=TGL)
        await dua_kali("apply", v2, VR.apply_vendor_credit, ab)
        await dua_kali("unapply", v2, VR.unapply_vendor_credit, UnapplyVendorCreditRequest(reason="TES E2E idem"))
        await dua_kali("void", v2, VR.void_vendor_credit, VoidVendorCreditRequest(reason="TES E2E idem"))
    finally:
        await tx.rollback(); await c.close()
    print(f"RINGKAS gagal={len(gagal)} {gagal[:6]}"); sys.exit(1 if gagal else 0)


async def main():
    if MODE == "TULIS":
        return await tulis()
    if MODE == "JEPRET":
        c = await asyncpg.connect(os.environ["DATABASE_URL"], server_settings={"default_transaction_read_only": "on"})
        json.dump({k: [str(v) for v in (await c.fetchrow(q, T)).values()] for k, q in SQL_JEPRET.items()}, open(f"/out/{NAMA}.json", "w"))
        await c.close(); print(f"JEPRET {NAMA} ok"); return
    s, e = json.load(open("/out/sebelum.json")), json.load(open("/out/sesudah.json"))
    for k in SQL_JEPRET:
        cek(s[k] == e[k], f"NET 0 (sesudah proses selesai): {k}", "" if s[k] == e[k] else f"{s[k]} / {e[k]}")
    print(f"RINGKAS gagal={len(gagal)} {gagal[:6]}"); sys.exit(1 if gagal else 0)

asyncio.run(main())
