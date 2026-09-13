"""Eksekusi dugaan: DP pelanggan A diterapkan ke faktur pelanggan B. ROLLBACK, nol baris menetap.

Sisi:
  KONTROL  pasangan SAMA pelanggan -> 200 (alat & subjek bekerja)
  UJI      pasangan BEDA pelanggan (dibuktikan beda lewat DB, kanonik lower(text)) -> apa yang keluar
  EFEK     bila 200: baris aplikasi, amount_paid faktur B, jurnal DEPOSIT_APPLICATION & akun
           yang disentuh, piutang pelanggan B (compute_ar_outstanding) turun
Hasil di memori Python. Tanpa sleep.
"""
import asyncio
import os
import sys
from datetime import date

sys.path.insert(0, "/app/backend/api_gateway")
import asyncpg  # noqa: E402
from starlette.requests import Request  # noqa: E402

T = "kaos-biru-konveksi"
hasil = []


def catat(uji, nilai, ket=""):
    hasil.append((uji, nilai, str(ket)[:300]))


def req(user_id):
    return Request({"type": "http", "method": "POST", "path": "/", "headers": [], "query_string": b"",
                    "state": {"user": {"tenant_id": T, "user_id": str(user_id)}}})


async def main():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    import app.services.db_pool as dbp

    class FakePool:
        def acquire(self, *a, **k):
            class Ctx:
                async def __aenter__(s):
                    return conn
                async def __aexit__(s, *e):
                    return False
            return Ctx()
        async def release(self, c):
            return None

    async def fake(*a, **k):
        return FakePool()
    dbp.get_db_pool = fake

    from app.routers import customer_deposits as cd
    from app.schemas.customer_deposits import ApplyCustomerDepositRequest, ApplyDepositItem

    user_id = await conn.fetchval(
        "SELECT created_by FROM bank_transactions WHERE tenant_id=$1 AND created_by IS NOT NULL LIMIT 1", T)
    je0 = await conn.fetchval("SELECT count(*) FROM journal_entries")
    app0 = await conn.fetchval("SELECT count(*) FROM customer_deposit_applications")

    luar = conn.transaction(); await luar.start()
    try:
        base = """
          SELECT d.id AS dep, d.customer_id AS dep_cust, s.id AS inv, s.customer_id AS inv_cust,
                 s.invoice_number, a.outstanding
          FROM customer_deposits d
          JOIN sales_invoices s ON s.tenant_id = d.tenant_id AND s.journal_id IS NOT NULL
          JOIN compute_ar_outstanding($1) a ON a.invoice_id = s.id
          WHERE d.tenant_id = $1 AND d.status IN ('posted','partial')
            AND d.amount - COALESCE(d.amount_applied,0) - COALESCE(d.amount_refunded,0) >= 1000
            AND a.outstanding >= 1000
            AND NOT EXISTS (SELECT 1 FROM customer_deposit_applications x WHERE x.deposit_id = d.id AND x.invoice_id = s.id)
        """
        sama = await conn.fetchrow(base + " AND lower(d.customer_id::text) = lower(s.customer_id::text) ORDER BY d.id, s.id LIMIT 1", T)
        beda = await conn.fetchrow(base + " AND lower(d.customer_id::text) <> lower(s.customer_id::text) ORDER BY d.id, s.id LIMIT 1", T)
        catat("subjek SAMA ada", sama is not None, sama and dict(sama))
        catat("subjek BEDA ada", beda is not None, beda and dict(beda))

        async def coba(p):
            sp = conn.transaction(); await sp.start()
            ar0 = await conn.fetchval("SELECT COALESCE(SUM(outstanding),0) FROM compute_ar_outstanding($1) WHERE invoice_id=$2", T, p["inv"])
            ip0 = await conn.fetchval("SELECT amount_paid FROM sales_invoices WHERE id=$1", p["inv"])
            try:
                body = ApplyCustomerDepositRequest(applications=[ApplyDepositItem(invoice_id=str(p["inv"]), amount=1000)])
                await cd.apply_customer_deposit(req(user_id), p["dep"], body)
                kode, ket = 200, "OK"
            except Exception as e:  # noqa: BLE001
                kode, ket = getattr(e, "status_code", type(e).__name__), getattr(e, "detail", str(e))
            efek = {}
            if kode == 200:
                ar1 = await conn.fetchval("SELECT COALESCE(SUM(outstanding),0) FROM compute_ar_outstanding($1) WHERE invoice_id=$2", T, p["inv"])
                ip1 = await conn.fetchval("SELECT amount_paid FROM sales_invoices WHERE id=$1", p["inv"])
                jr = await conn.fetch(
                    """SELECT je.source_type, coa.account_code, coa.account_type, jl.debit, jl.credit
                       FROM customer_deposit_applications x
                       JOIN journal_entries je ON je.id = x.journal_id
                       JOIN journal_lines jl ON jl.journal_id = je.id
                       JOIN chart_of_accounts coa ON coa.id = jl.account_id
                       WHERE x.deposit_id = $1 AND x.invoice_id = $2 ORDER BY jl.line_number""", p["dep"], p["inv"])
                efek = {"outstanding_faktur": f"{ar0}->{ar1}", "amount_paid_faktur": f"{ip0}->{ip1}",
                        "jurnal": [dict(r) for r in jr]}
            await sp.rollback()
            return kode, ket, efek

        if sama:
            k, t, e = await coba(sama)
            catat("KONTROL pelanggan SAMA -> 200", k == 200, f"{k} {t} {e}")
        if beda:
            k, t, e = await coba(beda)
            catat("UJI pelanggan BEDA: kode", k, f"{t}")
            if k == 200:
                catat("UJI pelanggan BEDA: efek", True, e)
    finally:
        await luar.rollback()

    je1 = await conn.fetchval("SELECT count(*) FROM journal_entries")
    app1 = await conn.fetchval("SELECT count(*) FROM customer_deposit_applications")
    catat("KONTROL nol baris menetap (jurnal, aplikasi)", je0 == je1 and app0 == app1, f"je {je0}->{je1} app {app0}->{app1}")
    await conn.close()
    for u, n, k in hasil:
        print(f"{u:48} = {n}  | {k}")


asyncio.run(main())
