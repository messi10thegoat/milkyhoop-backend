"""G-A (lebih bayar) + G-C (diskon/biaya): debit AP / kredit AR jurnal vs Σ amount_applied. ROLLBACK."""
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
    from app.routers import bill_payments as bp, receive_payments as rp
    from app.schemas.bill_payments import CreateBillPaymentRequest as BP, BillAllocationInput as BA
    from app.schemas.receive_payments import CreateReceivePaymentRequest as RC
    RA = RC.model_fields["allocations"].annotation.__args__[0]

    uid = await conn.fetchval("SELECT created_by FROM bank_transactions WHERE tenant_id=$1 AND created_by IS NOT NULL LIMIT 1", T)
    ba = await conn.fetchval("SELECT id FROM bank_accounts WHERE tenant_id=$1 AND is_active ORDER BY account_name LIMIT 1", T)
    je0 = await conn.fetchval("SELECT count(*) FROM journal_entries")

    async def baris_jurnal(jid):
        return [dict(x) for x in await conn.fetch(
            """SELECT coa.account_code, coa.account_type, jl.debit, jl.credit FROM journal_lines jl
               JOIN chart_of_accounts coa ON coa.id=jl.account_id WHERE jl.journal_id=$1 ORDER BY jl.line_number""", jid)]

    luar = conn.transaction(); await luar.start()
    try:
        vend = await conn.fetchval(
            """SELECT b.vendor_id FROM compute_ap_outstanding($1) a JOIN bills b ON b.id=a.bill_id
               WHERE a.bill_id IS NOT NULL AND a.outstanding >= 1000 GROUP BY b.vendor_id HAVING count(*) >= 2 LIMIT 1""", T)
        bills = [r["bill_id"] for r in await conn.fetch(
            """SELECT a.bill_id FROM compute_ap_outstanding($1) a JOIN bills b ON b.id=a.bill_id
               WHERE b.vendor_id=$2 AND a.outstanding >= 1000 ORDER BY a.bill_id LIMIT 2""", T, vend)]
        cust = await conn.fetchval(
            """SELECT s.customer_id FROM compute_ar_outstanding($1) a JOIN sales_invoices s ON s.id=a.invoice_id
               WHERE a.outstanding >= 1000 GROUP BY s.customer_id HAVING count(*) >= 2 LIMIT 1""", T)
        invs = [r["invoice_id"] for r in await conn.fetch(
            """SELECT a.invoice_id FROM compute_ar_outstanding($1) a JOIN sales_invoices s ON s.id=a.invoice_id
               WHERE s.customer_id=$2 AND a.outstanding >= 1000 ORDER BY a.invoice_id LIMIT 2""", T, cust)]

        kasus_ap = [
            ("AP lebih bayar: total 3000, alokasi 2x1000", dict(total_amount=3000)),
            ("AP diskon 100: total 1900, diskon 100, alokasi 2x1000", dict(total_amount=1900, discount_amount=100)),
            ("AP biaya bank 50: total 2000, fee 50, alokasi 2x1000", dict(total_amount=2000, bank_fee_amount=50)),
        ]
        for label, extra in kasus_ap:
            sp = conn.transaction(); await sp.start()
            try:
                body = BP(vendor_id=str(vend), payment_date=date.today(), bank_account_id=str(ba),
                          allocations=[BA(bill_id=str(b), amount_applied=1000) for b in bills], save_as_draft=True, **extra)
                pid = baca_id(await bp.create_bill_payment(req(uid), body))
                await bp.post_bill_payment(req(uid), str(pid))
                jid = await conn.fetchval("SELECT journal_id FROM bill_payments_v2 WHERE id=$1::uuid", pid)
                rows = await baris_jurnal(jid)
                d_ap = sum(r["debit"] for r in rows if r["account_type"] == "PAYABLE")
                print(f"\n{label}\n  debit PAYABLE jurnal = {d_ap}  | Σ amount_applied = 2000\n  baris: {rows}")
            except Exception as e:  # noqa: BLE001
                print(f"\n{label}\n  DITOLAK/GAGAL: {getattr(e,'status_code',type(e).__name__)} {getattr(e,'detail',e)}")
            await sp.rollback()

        kasus_ar = [
            ("AR lebih bayar: total 3000, alokasi 2x1000", dict(total_amount=3000)),
            ("AR diskon 100: total 1900, diskon 100, alokasi 2x1000", dict(total_amount=1900, discount_amount=100)),
        ]
        for label, extra in kasus_ar:
            sp = conn.transaction(); await sp.start()
            try:
                body = RC(customer_id=str(cust), payment_date=date.today(), payment_method="bank_transfer",
                          bank_account_id=str(ba), allocations=[RA(invoice_id=str(i), amount_applied=1000) for i in invs],
                          save_as_draft=False, **extra)
                pid = baca_id(await rp.create_receive_payment(req(uid), body))
                jid = await conn.fetchval("SELECT journal_id FROM receive_payments WHERE id=$1::uuid", pid)
                rows = await baris_jurnal(jid)
                k_ar = sum(r["credit"] for r in rows if r["account_type"] == "RECEIVABLE")
                print(f"\n{label}\n  kredit RECEIVABLE jurnal = {k_ar}  | Σ amount_applied = 2000\n  baris: {rows}")
            except Exception as e:  # noqa: BLE001
                print(f"\n{label}\n  DITOLAK/GAGAL: {getattr(e,'status_code',type(e).__name__)} {getattr(e,'detail',e)}")
            await sp.rollback()
    finally:
        await luar.rollback()
    print(f"\nnol menetap: jurnal {je0}->{await conn.fetchval('SELECT count(*) FROM journal_entries')}")
    await conn.close()


asyncio.run(main())
