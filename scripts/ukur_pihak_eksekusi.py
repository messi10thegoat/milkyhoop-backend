"""Eksekusi dua sisa sapuan pihak. ROLLBACK, nol menetap.

E1 GET /api/customers/{id}/journal-entries (customers.py get_customer_journal_entries): satu $n dipakai
   untuk kolom uuid (rp, si) DAN varchar (cd, cn). Dipanggil untuk pelanggan yang PUNYA DP/CN dan satu yang tidak.
E2 POST /api/vendor-deposits/{id}/apply (vendor_deposits.py ~718): UPDATE bills memakai paid_amount/total_amount
   yang tak ada di bills. Subjek: DP vendor posted/partial + tagihan vendor sama ber-outstanding; bila tak ada DP
   vendor, dibuat SINTETIS di savepoint (dicatat sebagai sintetis).
"""
import asyncio
import inspect
import os
import sys
import traceback
from datetime import date

sys.path.insert(0, "/app/backend/api_gateway")
import asyncpg  # noqa: E402
from starlette.requests import Request  # noqa: E402

T = "kaos-biru-konveksi"


def req(uid, path="/"):
    return Request({"type": "http", "method": "GET", "path": path, "headers": [], "query_string": b"",
                    "state": {"user": {"tenant_id": T, "user_id": str(uid)}}})


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
    uid = await conn.fetchval("SELECT created_by FROM bank_transactions WHERE tenant_id=$1 AND created_by IS NOT NULL LIMIT 1", T)
    je0 = await conn.fetchval("SELECT count(*) FROM journal_entries")

    luar = conn.transaction(); await luar.start()
    try:
        # ---------------- E1 ----------------
        from app.routers import customers as cu
        fn = cu.get_customer_journal_entries
        print("E1 tanda tangan:", inspect.signature(fn))
        punya = await conn.fetchval(
            "SELECT customer_id FROM customer_deposits WHERE tenant_id=$1 AND customer_id IS NOT NULL AND customer_id <> '' LIMIT 1", T)
        tanpa = await conn.fetchval(
            "SELECT id::text FROM customers c WHERE tenant_id=$1 AND NOT EXISTS (SELECT 1 FROM customer_deposits d WHERE d.customer_id = c.id::text) LIMIT 1", T)
        for label, cid in (("pelanggan PUNYA DP", punya), ("pelanggan TANPA DP", tanpa)):
            sp = conn.transaction(); await sp.start()
            try:
                kw = {}
                for n, p in inspect.signature(fn).parameters.items():
                    if n == "request":
                        kw[n] = req(uid, f"/api/customers/{cid}/journal-entries")
                    elif n == "customer_id":
                        ann = p.annotation
                        kw[n] = ann(cid) if ann not in (inspect._empty, str) and callable(ann) else cid
                    elif p.default is not inspect._empty:
                        d = p.default
                        kw[n] = getattr(d, "default", d)
                r = await fn(**kw)
                data = r if isinstance(r, dict) else getattr(r, "__dict__", {})
                print(f"E1 {label} ({cid}): 200; kunci={list(data.keys())[:6]}")
            except Exception as e:  # noqa: BLE001
                print(f"E1 {label} ({cid}): GAGAL {getattr(e,'status_code',type(e).__name__)} {getattr(e,'detail',e)}")
            await sp.rollback()

        # ---------------- E2 ----------------
        from app.routers import vendor_deposits as vd
        print("\nE2 tanda tangan:", inspect.signature(vd.apply_vendor_deposit))
        dep = await conn.fetchrow(
            "SELECT id, vendor_id, amount, status FROM vendor_deposits WHERE tenant_id=$1 AND status IN ('posted','partial') ORDER BY id LIMIT 1", T)
        print("E2 DP vendor nyata posted/partial:", dep and dict(dep))
        print("E2 cacah vendor_deposits per status:", [dict(x) for x in await conn.fetch(
            "SELECT status, count(*) FROM vendor_deposits WHERE tenant_id=$1 GROUP BY 1", T)])
        print("E2 vendor_deposit_applications pernah:", await conn.fetchval("SELECT count(*) FROM vendor_deposit_applications"))
        body_cls = inspect.signature(vd.apply_vendor_deposit).parameters["body"].annotation
        print("E2 medan body:", {n: f.is_required() for n, f in body_cls.model_fields.items()})
    except Exception:
        traceback.print_exc()
    finally:
        await luar.rollback()
    print(f"\nnol menetap: jurnal {je0}->{await conn.fetchval('SELECT count(*) FROM journal_entries')}")
    await conn.close()


asyncio.run(main())
