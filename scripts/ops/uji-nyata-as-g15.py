"""UJI NYATA AS-G15 (24 Okt 2026): penawaran -> faktur BERSAMAAN, tulisan TER-COMMIT 'TES E2E' di kaos (aturan tetap pemilik 30 Sep: kaos boleh
ditulisi uji ber-penanda 'TES E2E'; pola AS-GP2). Mode: baru [merah_kunci]
(1) penawaran TES E2E terkirim -> DUA koneksi to-invoice bersamaan -> TEPAT 1 faktur ber-quote_id; jawaban lain already_converted -> faktur SAMA.
(2) penawaran TES E2E ke-2 -> to-order x to-invoice bersamaan -> TEPAT 1 konversi; yang lain 400 status.
(3) kunci idem sama 2x berurutan (penawaran ke-3) -> X-Idempotent-Replay.
Bersih-bersih lewat APLIKASI di finally: faktur draf dihapus (delete_invoice), SO draf dihapus (delete_sales_order); penawaran TES E2E tetap
'converted' (tautan menggantung = AS-G15a, diterima MASTER) -> dilaporkan sebagai sisa_manual. GL/AR per akun kaos sebelum == sesudah (draf = nol jurnal).
Kontrol merah (WAJIB FAIL): merah_kunci = kode NYATA convert_to_invoice dari SALINAN pohon yang disabotase (scripts/ops/sabotase-as-g15.py:
kunci QUOTE + FOR UPDATE dicabut, jeda 0,8 dtk) -> DUA faktur. Harness TIDAK meniru handler; runner memasang salinan itu."""
import asyncio, json, os, re, sys
from decimal import Decimal as D
from uuid import UUID, uuid4
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "boneka")
import asyncpg
from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import Response

T = "kaos-biru-konveksi"
MERAH = sys.argv[2] if len(sys.argv) > 2 else ""  # hanya label; sabotase ada di salinan pohon yang dipasang runner
gagal = []
SQL_GL = """SELECT jl.account_id::text, SUM(jl.debit - jl.credit) FROM journal_lines jl JOIN journal_entries je ON je.id = jl.journal_id
            WHERE je.tenant_id = $1 AND je.status = 'POSTED' GROUP BY 1"""


def cek(nama, ok, rinci=""):
    print(("PASS " if ok else "FAIL ") + nama + ("" if ok else f"  {str(rinci)[:300]}"), flush=True)
    if not ok:
        gagal.append(nama)


async def main():
    from app.routers import quotes as RQ, sales_invoices as RSI, sales_orders as RSO
    from app.schemas.quotes import CreateQuoteRequest, ConvertToInvoiceRequest
    from app.utils.tanggal_tenant import tanggal_dokumen
    pool = await asyncpg.create_pool(os.environ["URL"], min_size=3, max_size=6)
    c = await asyncpg.connect(os.environ["URL"])
    if not await c.fetchval('SELECT 1 FROM "Tenant" WHERE id = $1', T):
        sys.exit(f"TENANT TAK ADA: {T}")

    class Kon:
        def __init__(s, k): s.k = k
        def __getattr__(s, n): return getattr(s.k, n)

    class CM:
        async def __aenter__(s):
            s.k = await pool.acquire()
            return Kon(s.k)
        async def __aexit__(s, *a):
            await pool.release(s.k)
            return False

    class P:
        def acquire(s): return CM()
    async def gp(): return P()
    for m in (RQ, RSI, RSO):
        m.get_pool = gp
    import app.services.db_pool as DBP
    DBP.get_db_pool = gp
    uid = await c.fetchval("SELECT created_by FROM sales_orders WHERE tenant_id = $1 AND created_by IS NOT NULL LIMIT 1", T)

    def req(kunci=None):
        async def receive(): return {"type": "http.request", "body": b"", "more_body": False}
        hdr = [(b"x-idempotency-key", kunci.encode())] if kunci else []
        r = Request({"type": "http", "method": "POST", "path": "/", "headers": hdr, "query_string": b""}, receive)
        r.state.user = {"tenant_id": T, "user_id": str(uid), "role": "OWNER", "username": "TES E2E"}
        return r

    async def galat(coro):
        try:
            return None, await coro
        except HTTPException as e:
            return e, None

    def dt(x): return x.model_dump(mode="json") if hasattr(x, "model_dump") else x
    gl0 = dict(await c.fetch(SQL_GL, T))
    hari = await tanggal_dokumen(c, T)
    cust = await c.fetchrow("SELECT id::text, nama FROM customers WHERE tenant_id = $1 AND is_active ORDER BY created_at LIMIT 1", T)
    dibuat = {"faktur": set(), "so": set(), "penawaran": []}

    async def penawaran_terkirim(no):
        r = dt(await RQ.create_quote(req(), CreateQuoteRequest(quote_date=hari, customer_id=cust["id"], customer_name=cust["nama"],
                                                               subject=f"TES E2E AS-G15 {no}", notes="TES E2E AS-G15",
                                                               items=[{"description": "TES E2E barang", "quantity": D("2"), "unit_price": D("1000")}])))
        qid = str(r["data"]["id"] if "id" in r["data"] else r["data"]["quote_id"])
        await RQ.send_quote(req(), qid)
        dibuat["penawaran"].append(qid)
        return qid

    async def faktur_dari(qid):
        return [str(x) for x in await c.fetch("SELECT id FROM sales_invoices WHERE tenant_id = $1 AND quote_id = $2", T, UUID(qid))]

    try:
        # (1) to-invoice x to-invoice BERSAMAAN
        q1 = await penawaran_terkirim(1)
        hasil = await asyncio.gather(galat(RQ.convert_to_invoice(req(), q1, ConvertToInvoiceRequest())),
                                     galat(RQ.convert_to_invoice(req(), q1, ConvertToInvoiceRequest())))
        fk = await faktur_dari(q1)
        dibuat["faktur"].update(fk)
        dat = [dt(r)["data"] for e, r in hasil if r is not None]
        cek(f"(1) dua to-invoice BERSAMAAN = TEPAT 1 faktur ({len(fk)}); yang lain already_converted -> faktur SAMA",
            len(fk) == 1 and len(dat) == 2 and {d["invoice_id"] for d in dat} == set(fk) and sum(bool(d.get("already_converted")) for d in dat) == 1,
            ([(e and e.detail) for e, r in hasil], dat, fk))
        # (2) to-order x to-invoice BERSAMAAN
        q2 = await penawaran_terkirim(2)
        h2 = await asyncio.gather(galat(RQ.convert_to_sales_order(req(), q2, None)), galat(RQ.convert_to_invoice(req(), q2, ConvertToInvoiceRequest())))
        fk2 = await faktur_dari(q2)
        dibuat["faktur"].update(fk2)
        so2 = [str(x) for x in await c.fetch("SELECT id FROM sales_orders WHERE tenant_id = $1 AND quote_id = $2", T, UUID(q2))]
        dibuat["so"].update(so2)
        cek(f"(2) to-order x to-invoice BERSAMAAN = TEPAT 1 konversi (faktur {len(fk2)} + SO {len(so2)}); yang lain 400",
            len(fk2) + len(so2) == 1 and sum(e is not None and e.status_code == 400 for e, r in h2) == 1, ([(e and e.detail) for e, r in h2], fk2, so2))
        # (3) idem kunci sama berurutan
        q3 = await penawaran_terkirim(3)
        kunci, hh = f"TES-E2E-{uuid4()}", Response()
        e1, r1 = await galat(RQ.convert_to_invoice(req(kunci), q3, ConvertToInvoiceRequest(), Response()))
        e2, r2 = await galat(RQ.convert_to_invoice(req(kunci), q3, ConvertToInvoiceRequest(), hh))
        fk3 = await faktur_dari(q3)
        dibuat["faktur"].update(fk3)
        cek("(3) kunci sama 2x = X-Idempotent-Replay + 1 faktur", r1 is not None and r2 is not None and hh.headers.get("X-Idempotent-Replay") == "true"
            and len(fk3) == 1, (e1 and e1.detail, e2 and e2.detail, fk3))
    finally:
        sisa = []
        for fid in sorted(dibuat["faktur"]):
            e, _ = await galat(RSI.delete_invoice(req(), UUID(fid)))
            if e is not None:
                sisa.append(f"faktur {fid}: {e.detail}")
        for sid in sorted(dibuat["so"]):
            e, _ = await galat(RSO.delete_sales_order(req(), sid))
            if e is not None:
                sisa.append(f"SO {sid}: {e.detail}")
        nf = await c.fetchval("SELECT count(*) FROM sales_invoices WHERE tenant_id = $1 AND id = ANY($2::uuid[])", T, [UUID(x) for x in dibuat["faktur"]])
        gl1 = dict(await c.fetch(SQL_GL, T))
        beda = {k: (gl0.get(k), gl1.get(k)) for k in set(gl0) | set(gl1) if gl0.get(k) != gl1.get(k)}
        cek(f"bersih: faktur draf TES E2E terhapus ({nf} sisa), GL kaos per akun sebelum == sesudah", nf == 0 and not beda, (sisa, beda))
        print("INFO sisa_manual: penawaran TES E2E 'converted' (tautan menggantung, AS-G15a): " + json.dumps(dibuat["penawaran"]))
        if sisa:
            print("INFO sisa_manual gagal dibersihkan: " + json.dumps(sisa))
        await c.close()
        await pool.close()
    print(f"SELESAI gagal={len(gagal)}")
    sys.exit(1 if gagal else 0)


asyncio.run(main())
