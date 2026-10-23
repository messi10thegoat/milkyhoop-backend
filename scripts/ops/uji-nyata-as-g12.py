"""UJI NYATA AS-G12 (23 Okt 2026): SATU pembaca kunci idempotensi (header dulu, badan cadangan, beda -> 422). kaos SAJA, SEMUA di SATU
transaksi luar yang SELALU di-ROLLBACK (get_pool tertambal; skenario yang boleh menulis dua kali di savepoint sendiri).
  F1 fulfill INV-2609-0049 (1 Cotton Combet, stok 42) HANYA header K, dikirim 2x -> TEPAT 1 surat jalan (kiriman ke-2 = replay)
  F2 fulfill header H + badan B beda -> BARU: 422 IDEMPOTENCY_KEY_MISMATCH (LAMA: badan dipakai diam-diam, header diabaikan)
  T1 to-invoice SO-2610-0008 HANYA header, 2x -> TEPAT 1 faktur
  T2 to-invoice header H + badan B beda -> BARU: 422 (LAMA: badan dipakai, tanpa cek beda)
  D1 uang muka SO-2610-0007 HANYA header, 2x -> TEPAT 1 uang muka (perilaku SAMA lama/baru: kontrak lama dipertahankan)
Rekaman per pohon -> /out/<NAMA>.json; BANDING mencetak lama vs baru + menilai pohon BARU. NET 0 (proses terpisah).
MERAH=1: kirim kedua F1 memakai kunci BERBEDA -> F1 WAJIB gagal (2 surat jalan / galat sisa)."""
import asyncio, json, os, sys, uuid
from datetime import date
from decimal import Decimal
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "x")
import asyncpg
from starlette.requests import Request

T = "kaos-biru-konveksi"
FAKTUR, SII = "d1e4e5ca-2e05-44ed-bd95-80d2165908d9", "2d0a5982-5798-4539-a1b8-8052ba486f0e"   # INV-2609-0049
BARANG = "35175eb3-143e-456f-a3b1-1c68bf13c683"
SO_FAKTUR, SO_DP = "4251d52a-214e-4f00-af9e-528cc48a4cde", "57a12737-de89-4821-a81a-fadac754fb61"  # SO-2610-0008 / -0007
MODE, NAMA, MERAH = os.environ["MODE"], os.environ.get("NAMA", ""), os.environ.get("MERAH") == "1"
gagal = []


def cek(ok, l, i=""):
    print(("PASS " if ok else "FAIL ") + l + (f" | {i}" if i else ""), flush=True)
    if not ok: gagal.append(l)


SQL_JEPRET = {
    "sj": "SELECT count(*) FROM invoice_fulfillments WHERE tenant_id = $1",
    "faktur": "SELECT count(*) FROM sales_invoices WHERE tenant_id = $1",
    "dp": "SELECT count(*), COALESCE(sum(amount),0)::text FROM customer_deposits WHERE tenant_id = $1",
    "jurnal": "SELECT count(*), COALESCE(sum(total_debit),0)::text FROM journal_entries WHERE tenant_id = $1",
    "stok": "SELECT count(*), COALESCE(sum(quantity_in - quantity_out),0)::text FROM inventory_ledger WHERE tenant_id = $1",
    "idem": "SELECT count(*) FROM idempotency_keys WHERE tenant_id = $1",
    "seq": "SELECT json_agg(json_build_object('p', prefix, 'n', last_number) ORDER BY prefix, year, month)::text FROM journal_number_sequences WHERE tenant_id = $1",
}


async def jepret(c):
    return {k: [str(v) for v in (await c.fetchrow(q, T)).values()] for k, q in SQL_JEPRET.items()}


def req(uid, headers=None, body=None):
    raw = json.dumps(body, default=str).encode() if body is not None else b""
    async def receive(): return {"type": "http.request", "body": raw, "more_body": False}
    h = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()] + [(b"content-type", b"application/json")]
    r = Request({"type": "http", "method": "POST", "path": "/", "headers": h, "query_string": b""}, receive)
    r.state.user = {"tenant_id": T, "user_id": str(uid)}; return r


async def tulis():
    from fastapi import HTTPException
    from starlette.responses import Response
    from app.routers import sales_invoices as SI, sales_orders as SO, customer_deposits as CD
    from app.schemas.sales_orders import ConvertToInvoiceRequest
    from app.schemas.customer_deposits import CreateCustomerDepositRequest
    c = await asyncpg.connect(os.environ["DATABASE_URL"])
    uid = await c.fetchval('SELECT id FROM "User" WHERE "tenantId" = $1 ORDER BY "createdAt" LIMIT 1', T)
    gudang = await c.fetchval("SELECT warehouse_id FROM warehouse_stock WHERE item_id = $1 AND quantity >= 1 ORDER BY quantity DESC LIMIT 1",
                              uuid.UUID(BARANG))
    so_dp = await c.fetchrow("SELECT customer_id, customer_name, order_number FROM sales_orders WHERE id = $1", uuid.UUID(SO_DP))
    akun = await c.fetchrow("SELECT id, coa_id FROM bank_accounts WHERE tenant_id = $1 AND is_active ORDER BY created_at LIMIT 1", T)

    class Kon:
        def __getattr__(s, n): return getattr(c, n)
        def transaction(s, **kw): return c.transaction()
    k = Kon()
    class CM:
        async def __aenter__(s): return k
        async def __aexit__(s, *a): return False
    class Tambal:
        def acquire(s): return CM()
    async def gp(): return Tambal()
    for M in (SI, SO, CD):
        M.get_pool = gp
    hasil = {}

    async def coba(fn):
        sp = c.transaction(); await sp.start()
        try:
            r = await fn()
            out = ("OK", r)
        except HTTPException as e:
            out = (e.status_code, e.detail if isinstance(e.detail, str) else e.detail.get("code", e.detail.get("message")))
        except Exception as e:  # noqa: BLE001
            out = ("GALAT", f"{type(e).__name__}: {str(e)[:120]}")
        await sp.commit() if out[0] == "OK" else await sp.rollback()
        return out

    def sj_body(kunci=None):
        b = {"warehouse_id": str(gudang), "fulfillment_date": "2026-10-23", "recognize_revenue": True,
             "items": [{"invoice_item_id": SII, "quantity": 1}], "notes": "TES E2E G12"}
        if kunci:
            b["idempotency_key"] = kunci
        return b

    def dp_body():
        return CreateCustomerDepositRequest(customer_id=str(so_dp["customer_id"]) if so_dp["customer_id"] else None, customer_name=so_dp["customer_name"],
                                            amount=Decimal("1000"), deposit_date=date(2026, 10, 23), payment_method="transfer",
                                            account_id=str(akun["coa_id"]), bank_account_id=str(akun["id"]), sales_order_id=SO_DP,
                                            reference=so_dp["order_number"], notes="TES E2E G12")

    tx = c.transaction(); await tx.start()
    try:
        # F2 dulu (savepoint dibatalkan apa pun hasilnya): header H + badan B beda
        sp = c.transaction(); await sp.start()
        try:
            hasil["F2"] = str((await coba(lambda: SI.fulfill_invoice(req(uid, {"X-Idempotency-Key": "TESG12-H"}, sj_body("TESG12-B")), uuid.UUID(FAKTUR))))[0])
        finally:
            await sp.rollback()
        n0 = await c.fetchval("SELECT count(*) FROM invoice_fulfillments WHERE tenant_id = $1", T)
        r1 = await coba(lambda: SI.fulfill_invoice(req(uid, {"X-Idempotency-Key": "TESG12-K"}, sj_body()), uuid.UUID(FAKTUR)))
        k2 = "TESG12-K2" if MERAH else "TESG12-K"
        r2 = await coba(lambda: SI.fulfill_invoice(req(uid, {"X-Idempotency-Key": k2}, sj_body()), uuid.UUID(FAKTUR)))
        n1 = await c.fetchval("SELECT count(*) FROM invoice_fulfillments WHERE tenant_id = $1", T)
        hasil["F1"] = {"r1": str(r1[0]), "r2": str(r2[0]), "tambah": n1 - n0}
        # T2 (savepoint): header H + badan B beda
        sp = c.transaction(); await sp.start()
        try:
            hasil["T2"] = str((await coba(lambda: SO.convert_to_invoice(req(uid, {"X-Idempotency-Key": "TESG12-TH"}), Response(), SO_FAKTUR,
                                                                         ConvertToInvoiceRequest(idempotency_key="TESG12-TB"))))[0])
        finally:
            await sp.rollback()
        f0 = await c.fetchval("SELECT count(*) FROM sales_invoices WHERE tenant_id = $1", T)
        t1 = await coba(lambda: SO.convert_to_invoice(req(uid, {"X-Idempotency-Key": "TESG12-T"}), Response(), SO_FAKTUR, ConvertToInvoiceRequest()))
        t2 = await coba(lambda: SO.convert_to_invoice(req(uid, {"X-Idempotency-Key": "TESG12-T"}), Response(), SO_FAKTUR, ConvertToInvoiceRequest()))
        f1 = await c.fetchval("SELECT count(*) FROM sales_invoices WHERE tenant_id = $1", T)
        hasil["T1"] = {"r1": str(t1[0]), "r2": str(t2[0]), "tambah": f1 - f0}
        d0 = await c.fetchval("SELECT count(*) FROM customer_deposits WHERE tenant_id = $1", T)
        a1 = await coba(lambda: CD.create_customer_deposit(req(uid, {"X-Idempotency-Key": "TESG12-D"}), dp_body()))
        a2 = await coba(lambda: CD.create_customer_deposit(req(uid, {"X-Idempotency-Key": "TESG12-D"}), dp_body()))
        d1 = await c.fetchval("SELECT count(*) FROM customer_deposits WHERE tenant_id = $1", T)
        hasil["D1"] = {"r1": str(a1[0]), "r2": str(a2[0]), "tambah": d1 - d0}
        print(f"REKAM {NAMA} {json.dumps(hasil)}", flush=True)
    finally:
        await tx.rollback(); await c.close()
    json.dump(hasil, open(f"/out/{NAMA}.json", "w"))


async def main():
    if MODE == "JEPRET":
        c = await asyncpg.connect(os.environ["DATABASE_URL"], server_settings={"default_transaction_read_only": "on"})
        json.dump(await jepret(c), open(f"/out/{NAMA}.json", "w")); await c.close(); print(f"JEPRET {NAMA} ok"); return
    if MODE == "TULIS":
        return await tulis()
    lama, baru = json.load(open("/out/lama.json")), json.load(open("/out/baru.json"))
    print(f"LAMA {lama}\nBARU {baru}")
    cek(baru["F1"] == {"r1": "OK", "r2": "OK", "tambah": 1}, "(F1) fulfill HANYA header 2x -> tepat 1 surat jalan (baru)", str(baru["F1"]))
    cek(baru["F2"] == "422", "(F2) fulfill header != badan -> 422 MISMATCH (baru)", f"lama={lama['F2']} baru={baru['F2']}")
    cek(baru["T1"] == {"r1": "OK", "r2": "OK", "tambah": 1}, "(T1) to-invoice HANYA header 2x -> tepat 1 faktur", str(baru["T1"]))
    cek(baru["T2"] == "422", "(T2) to-invoice header != badan -> 422 MISMATCH (baru)", f"lama={lama['T2']} baru={baru['T2']}")
    cek(baru["D1"] == {"r1": "OK", "r2": "OK", "tambah": 1} and lama["D1"] == baru["D1"], "(D1) UM HANYA header 2x -> 1 uang muka; lama == baru",
        f"{lama['D1']} / {baru['D1']}")
    s, e = json.load(open("/out/sebelum.json")), json.load(open("/out/sesudah.json"))
    for kk in SQL_JEPRET:
        cek(s[kk] == e[kk], f"NET 0 (sesudah proses selesai): {kk}", "" if s[kk] == e[kk] else f"{s[kk]} / {e[kk]}")
    print(f"RINGKAS gagal={len(gagal)} {gagal[:6]}"); sys.exit(1 if gagal else 0)

asyncio.run(main())
