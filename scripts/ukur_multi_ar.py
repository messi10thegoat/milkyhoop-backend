"""Kembar AR multi-alokasi: create_receive_payment (2 faktur, 1 pelanggan) + post. ROLLBACK."""
import asyncio
import os
import sys
from datetime import date

sys.path.insert(0, "/app/backend/api_gateway")
import asyncpg  # noqa: E402
from starlette.requests import Request  # noqa: E402

T = "kaos-biru-konveksi"


def baca_id(r):
    for k in (r, getattr(r, "data", None), (r.get("data") if isinstance(r, dict) else None)):
        if isinstance(k, dict) and k.get("id"):
            return k["id"]
        if k is not None and getattr(k, "id", None):
            return k.id
    raise RuntimeError(f"ALAT: id tak terbaca dari {type(r).__name__}")


def req(uid):
    return Request({"type": "http", "method": "POST", "path": "/", "headers": [], "query_string": b"",
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
    from app.routers import receive_payments as rp
    from app.schemas.receive_payments import CreateReceivePaymentRequest as C
    alloc_cls = C.model_fields["allocations"].annotation.__args__[0]
    print("kelas alokasi:", alloc_cls.__name__, list(alloc_cls.model_fields))
    print("medan wajib Create:", [n for n, f in C.model_fields.items() if f.is_required()])
    print("payment_method:", C.model_fields["payment_method"].annotation)

    uid = await conn.fetchval("SELECT created_by FROM bank_transactions WHERE tenant_id=$1 AND created_by IS NOT NULL LIMIT 1", T)
    ba = await conn.fetchrow("SELECT id FROM bank_accounts WHERE tenant_id=$1 AND is_active ORDER BY account_name LIMIT 1", T)
    je0 = await conn.fetchval("SELECT count(*) FROM journal_entries")

    luar = conn.transaction(); await luar.start()
    try:
        cust = await conn.fetchval(
            """SELECT s.customer_id FROM compute_ar_outstanding($1) a JOIN sales_invoices s ON s.id=a.invoice_id
               WHERE a.outstanding >= 1000 GROUP BY s.customer_id HAVING count(*) >= 2 LIMIT 1""", T)
        if not cust:
            print("SUBJEK AR TAK ADA: tak ada pelanggan dgn >=2 faktur outstanding >=1000")
            return
        invs = [r["invoice_id"] for r in await conn.fetch(
            """SELECT a.invoice_id FROM compute_ar_outstanding($1) a JOIN sales_invoices s ON s.id=a.invoice_id
               WHERE s.customer_id=$2 AND a.outstanding >= 1000 ORDER BY a.invoice_id LIMIT 2""", T, cust)]
        s0 = {i: (await conn.fetchval("SELECT amount_paid FROM sales_invoices WHERE id=$1", i),
                  await conn.fetchval("SELECT COALESCE(SUM(outstanding),0) FROM compute_ar_outstanding($1) WHERE invoice_id=$2", T, i)) for i in invs}
        body = C(customer_id=str(cust), payment_date=date.today(), payment_method="bank_transfer",
                 bank_account_id=str(ba["id"]), total_amount=2000,
                 allocations=[alloc_cls(invoice_id=str(i), amount_applied=1000) for i in invs], save_as_draft=False)
        r = await rp.create_receive_payment(req(uid), body)
        pid = baca_id(r)
        st = await conn.fetchrow("SELECT status, journal_id FROM receive_payments WHERE id=$1::uuid", pid)
        print("penerimaan:", pid, dict(st))
        gl = await conn.fetch(
            """SELECT coa.account_type, SUM(jl.debit) d, SUM(jl.credit) k FROM journal_lines jl
               JOIN chart_of_accounts coa ON coa.id=jl.account_id WHERE jl.journal_id=$1 GROUP BY 1 ORDER BY 1""", st["journal_id"])
        print("GL jurnal penerimaan:", [dict(x) for x in gl])
        cte = await conn.fetch(
            """SELECT rpa.invoice_id, COALESCE(SUM(jl.credit),0) AS total_credit_cte
               FROM receive_payment_allocations rpa JOIN receive_payments r ON r.id = rpa.payment_id
               JOIN journal_entries je ON je.id = r.journal_id JOIN journal_lines jl ON jl.journal_id = je.id
               JOIN chart_of_accounts coa ON coa.id = jl.account_id
               WHERE r.tenant_id=$1 AND r.id=$2::uuid AND je.status='POSTED' AND je.reversed_by_id IS NULL
                 AND coa.account_type='RECEIVABLE' AND jl.credit > 0 GROUP BY rpa.invoice_id""", T, pid)
        print("CTE branch 1 utk penerimaan ini:", [dict(x) for x in cte])
        for i in invs:
            ap1 = await conn.fetchval("SELECT amount_paid FROM sales_invoices WHERE id=$1", i)
            o1 = await conn.fetchval("SELECT COALESCE(SUM(outstanding),0) FROM compute_ar_outstanding($1) WHERE invoice_id=$2", T, i)
            print(f"faktur {str(i)[:8]}: CACHE amount_paid {s0[i][0]} -> {ap1} (delta {(ap1 or 0)-(s0[i][0] or 0)}) | "
                  f"FUNGSI outstanding {s0[i][1]} -> {o1} (delta {o1 - s0[i][1]})")
        v = await conn.fetch("SELECT customer_id, canonical_ar, gl_ar, drift, status FROM verify_ar_reconciliation($1) WHERE customer_id=$2", T, str(cust))
        print("verify_ar_reconciliation pelanggan itu:", [dict(x) for x in v])
    finally:
        await luar.rollback()
    je1 = await conn.fetchval("SELECT count(*) FROM journal_entries")
    print(f"nol menetap: jurnal {je0}->{je1}")
    await conn.close()


asyncio.run(main())
