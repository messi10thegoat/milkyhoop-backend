"""TELUSUR tepi merge pelanggan (lapis 1 = dinding deleted_by ditambal di salinan). ROLLBACK per skenario.
Skenario: tenant lain sebagai sasaran · sasaran == sumber · sasaran sudah dihapus · sumber tenant lain · dampak V248 &
AR per pelanggan · snapshot customer_name di dokumen · pesanan/proforma/quote tertinggal · aktivitas & statistik.
"""
import asyncio
import importlib.util
import json
import os
import sys

sys.path.insert(0, "/app/backend/api_gateway")
import asyncpg  # noqa: E402
from starlette.requests import Request  # noqa: E402

T, TB = "kaos-biru-konveksi", "grapgrap-manado"


async def main():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    import app.services.db_pool as dbp

    class FakePool:
        def acquire(self, *a, **k):
            class C:
                async def __aenter__(s):
                    return conn
                async def __aexit__(s, *e):
                    return False
            return C()
        async def release(self, c):
            return None

    async def fake(*a, **k):
        return FakePool()
    dbp.get_db_pool = fake
    import app.routers  # noqa: F401
    spec = importlib.util.spec_from_file_location("app.routers.cust_telusur", "/tmp/cust_telusur.py")  # salinan lapis 1
    m = importlib.util.module_from_spec(spec); sys.modules[spec.name] = m; spec.loader.exec_module(m)
    uid = await conn.fetchval("SELECT created_by FROM bank_transactions WHERE tenant_id=$1 AND created_by IS NOT NULL LIMIT 1", T)

    def rq(body):
        async def recv():
            return {"type": "http.request", "body": json.dumps(body).encode(), "more_body": False}
        return Request({"type": "http", "method": "POST", "path": "/", "headers": [], "query_string": b"",
                        "state": {"user": {"tenant_id": T, "user_id": str(uid)}}}, recv)

    S = "bf12b53a-e42b-4827-beff-ceea3e45cd9e"
    D = "d2d6d24b-1de1-4be0-8ef8-4cd057ca5617"
    lain_tb = str(await conn.fetchval("SELECT id FROM customers WHERE tenant_id=$1 LIMIT 1", TB))

    async def jalan(label, body, ukur=None):
        sp = conn.transaction(); await sp.start()
        try:
            try:
                r = await m.merge_customers(rq(body)); k = ("200", r.get("message"))
            except Exception as e:  # noqa: BLE001
                k = (getattr(e, "status_code", type(e).__name__), str(getattr(e, "detail", e))[:90])
            extra = await ukur() if ukur else ""
            print(f"[{label}] -> {k} {extra}")
        finally:
            await sp.rollback()

    async def cnt(cid, tenant=T):
        return {t: await conn.fetchval(f"SELECT count(*) FROM {t} WHERE customer_id=$1", cid)
                for t in ("sales_invoices", "credit_notes", "customer_deposits", "receive_payments")}

    # 1 sasaran tenant lain
    async def u1():
        return {"dokumen kaos berpindah ke pelanggan grapgrap": await cnt(lain_tb), "sumber aktif?": await conn.fetchval("SELECT is_active FROM customers WHERE id=$1", S)}
    await jalan("sasaran tenant lain", {"source_ids": [S], "target_id": lain_tb}, u1)
    # 2 sasaran == sumber
    async def u2():
        return {"sumber/sasaran aktif?": await conn.fetchval("SELECT is_active FROM customers WHERE id=$1", S), "dokumen": await cnt(S)}
    await jalan("sasaran == sumber", {"source_ids": [S], "target_id": S}, u2)
    # 3 sasaran sudah dihapus
    sp = conn.transaction(); await sp.start()
    await conn.execute("UPDATE customers SET is_active=false, deleted_at=now() WHERE id=$1", D)
    try:
        try:
            r = await m.merge_customers(rq({"source_ids": [S], "target_id": D})); print("[sasaran terhapus] -> 200", r.get("message"), await cnt(D))
        except Exception as e:  # noqa: BLE001
            print("[sasaran terhapus] ->", getattr(e, "status_code", type(e).__name__))
    finally:
        await sp.rollback()
    # 4 sumber tenant lain (id grapgrap sebagai sumber)
    async def u4():
        return {"pelanggan grapgrap dinonaktifkan?": await conn.fetchval("SELECT is_active FROM customers WHERE id=$1", lain_tb)}
    await jalan("sumber tenant lain", {"source_ids": [lain_tb], "target_id": D}, u4)
    # 5 id bukan uuid
    await jalan("sumber bukan uuid", {"source_ids": ["Toko Melati"], "target_id": D})

    # 6 dampak akuntansi & tampilan (merge sah S -> D)
    sp = conn.transaction(); await sp.start()
    try:
        v0 = {r["tenant_id"]: r["verdict"] for r in await conn.fetch("SELECT tenant_id, verdict FROM verify_ar_reconciliation_all()")}
        ar0 = {r["customer_id"]: r["s"] for r in await conn.fetch("SELECT customer_id, SUM(outstanding) s FROM compute_ar_outstanding($1) WHERE customer_id IN ($2,$3) GROUP BY 1", T, S, D)}
        bank0 = await conn.fetchval("SELECT verdict FROM hc_verdict('bank_sync',$1)", T)
        await m.merge_customers(rq({"source_ids": [S], "target_id": D}))
        v1 = {r["tenant_id"]: r["verdict"] for r in await conn.fetch("SELECT tenant_id, verdict FROM verify_ar_reconciliation_all()")}
        ar1 = {r["customer_id"]: r["s"] for r in await conn.fetch("SELECT customer_id, SUM(outstanding) s FROM compute_ar_outstanding($1) WHERE customer_id IN ($2,$3) GROUP BY 1", T, S, D)}
        nama = await conn.fetch("SELECT DISTINCT customer_name FROM sales_invoices WHERE customer_id=$1", D)
        so = await conn.fetchval("SELECT count(*) FROM sales_orders WHERE customer_id=$1", S)
        pr = await conn.fetchval("SELECT count(*) FROM proformas WHERE customer_id=$1", S)
        cda = await conn.fetchval("""SELECT count(*) FROM customer_deposit_applications a JOIN customer_deposits d ON d.id=a.deposit_id
            JOIN sales_invoices s ON s.id=a.invoice_id WHERE d.customer_id <> s.customer_id AND a.status='active'""")
        cnx = await conn.fetchval("SELECT count(*) FROM credit_notes n JOIN sales_invoices s ON s.id=n.original_invoice_id WHERE n.customer_id<>s.customer_id")
        stat = await conn.fetch("SELECT id, nama, total_transaksi FROM customers WHERE id IN ($1,$2)", S, D)
        print("[dampak] V248", v0, "->", v1)
        print("[dampak] AR per pelanggan (compute)", ar0, "->", ar1, "| bank_sync", bank0)
        print("[dampak] nama snapshot di faktur sasaran:", [r["customer_name"] for r in nama])
        print("[dampak] tertinggal di sumber nonaktif: sales_orders", so, "proformas", pr)
        print("[dampak] invariant pihak sesudah merge: DP≠faktur aktif", cda, "CN≠faktur", cnx)
        print("[dampak] statistik pelanggan:", [tuple(x) for x in stat])
    finally:
        await sp.rollback()
    await conn.close()


asyncio.run(main())
