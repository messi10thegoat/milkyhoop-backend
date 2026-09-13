"""Multi-alokasi: AP (bill_payments) dan kembar AR (receive_payments). ROLLBACK, baca-saja efek.

Per sisi, dipisah per LAPIS:
  GL      debit/kredit akun AP/AR pada jurnal pembayaran (harus = total dibayar)
  CACHE   bills.amount_paid / sales_invoices.amount_paid per dokumen
  FUNGSI  compute_ap/ar_outstanding per dokumen
  CTE     payment_debits / payment_credits dijalankan TERPISAH (disalin dari definisi hidup)
  PEMBACA hc_verdict('ap_invariant') (check_7/V242) + drift R8 (GL efektif - Σ fungsi)
Pembaca respons handler: SATU pembantu (baca_id) — kelas "dict vs atribut" dua kali menggigit hari ini.
"""
import asyncio
import os
import sys
from datetime import date

sys.path.insert(0, "/app/backend/api_gateway")
import asyncpg  # noqa: E402
from starlette.requests import Request  # noqa: E402

T = "kaos-biru-konveksi"


def baca_id(r):
    """Satu cara membaca id dari respons handler yang dipanggil langsung (dict ATAU model)."""
    for kandidat in (r, getattr(r, "data", None), (r.get("data") if isinstance(r, dict) else None)):
        if isinstance(kandidat, dict) and kandidat.get("id"):
            return kandidat["id"]
        if kandidat is not None and getattr(kandidat, "id", None):
            return kandidat.id
    raise RuntimeError(f"ALAT: id tak terbaca dari respons bertipe {type(r).__name__}")


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
    import app.schemas.receive_payments as rps

    uid = await conn.fetchval("SELECT created_by FROM bank_transactions WHERE tenant_id=$1 AND created_by IS NOT NULL LIMIT 1", T)
    ba = await conn.fetchrow("SELECT id, coa_id FROM bank_accounts WHERE tenant_id=$1 AND is_active ORDER BY account_name LIMIT 1", T)

    fdef_ap = await conn.fetchval("SELECT pg_get_functiondef(p.oid) FROM pg_proc p WHERE proname='compute_ap_outstanding'")
    fdef_ar = await conn.fetchval("SELECT pg_get_functiondef(p.oid) FROM pg_proc p WHERE proname='compute_ar_outstanding'")

    async def r8(tipe, fn):
        gl = await conn.fetchval(
            f"""SELECT COALESCE(SUM({'jl.credit-jl.debit' if tipe=='PAYABLE' else 'jl.debit-jl.credit'}),0)
                FROM journal_lines jl JOIN journal_entries je ON je.id=jl.journal_id
                JOIN chart_of_accounts coa ON coa.id=jl.account_id
                WHERE coa.account_type=$2 AND is_effective_journal(je.id) AND je.tenant_id=$1""", T, tipe)
        sub = await conn.fetchval(f"SELECT COALESCE(SUM(outstanding),0) FROM {fn}($1)", T)
        return gl, sub, gl - sub

    luar = conn.transaction(); await luar.start()
    try:
        # ================= AP =================
        print("\n==================== AP (bill_payments) ====================")
        print("definisi hidup, CTE payment_debits:")
        blok = fdef_ap[fdef_ap.find("payment_debits AS"):fdef_ap.find("vc_applied_debits AS")]
        print(blok)
        vend = await conn.fetchrow(
            """SELECT b.vendor_id FROM compute_ap_outstanding($1) a JOIN bills b ON b.id=a.bill_id
               WHERE a.bill_id IS NOT NULL AND a.outstanding >= 1000 GROUP BY b.vendor_id HAVING count(*) >= 2 LIMIT 1""", T)
        if not vend:
            print("SUBJEK AP TAK ADA: tak ada vendor dgn >=2 tagihan outstanding")
        else:
            bills = [r["bill_id"] for r in await conn.fetch(
                """SELECT a.bill_id FROM compute_ap_outstanding($1) a JOIN bills b ON b.id=a.bill_id
                   WHERE b.vendor_id=$2 AND a.outstanding >= 1000 ORDER BY a.bill_id LIMIT 2""", T, vend["vendor_id"])]
            sp = conn.transaction(); await sp.start()
            s0 = {b: (await conn.fetchval("SELECT amount_paid FROM bills WHERE id=$1", b),
                      await conn.fetchval("SELECT COALESCE(SUM(outstanding),0) FROM compute_ap_outstanding($1) WHERE bill_id=$2", T, b)) for b in bills}
            r80 = await r8("PAYABLE", "compute_ap_outstanding")
            v70 = await conn.fetchrow("SELECT drift, member_count, verdict FROM hc_verdict('ap_invariant', $1)", T)
            body = BP(vendor_id=str(vend["vendor_id"]), payment_date=date.today(), bank_account_id=str(ba["id"]),
                      total_amount=2000, allocations=[BA(bill_id=str(b), amount_applied=1000) for b in bills], save_as_draft=True)
            pid = baca_id(await bp.create_bill_payment(req(uid), body))
            await bp.post_bill_payment(req(uid), str(pid))
            jid = await conn.fetchval("SELECT journal_id FROM bill_payments_v2 WHERE id=$1::uuid", pid)
            gl = await conn.fetch(
                """SELECT coa.account_type, SUM(jl.debit) d, SUM(jl.credit) k FROM journal_lines jl
                   JOIN chart_of_accounts coa ON coa.id=jl.account_id WHERE jl.journal_id=$1 GROUP BY 1 ORDER BY 1""", jid)
            print("GL jurnal pembayaran (per account_type):", [dict(x) for x in gl])
            cte = await conn.fetch(
                """SELECT bpa.bill_id, COALESCE(SUM(jl.debit),0) AS total_debit_cte, count(*) AS baris_join
                   FROM bill_payment_allocations bpa
                   JOIN bill_payments_v2 bpv2 ON bpv2.id = bpa.payment_id
                   JOIN journal_entries je ON je.id = bpv2.journal_id
                   JOIN journal_lines jl ON jl.journal_id = je.id
                   JOIN chart_of_accounts coa ON coa.id = jl.account_id
                   WHERE bpv2.tenant_id=$1 AND bpv2.id=$2::uuid AND je.status='POSTED' AND je.reversed_by_id IS NULL
                     AND coa.account_type='PAYABLE' AND jl.debit > 0
                   GROUP BY bpa.bill_id""", T, pid)
            print("CTE payment_debits utk pembayaran ini (salinan persis syaratnya):", [dict(x) for x in cte])
            alok = await conn.fetch("SELECT bill_id, amount_applied FROM bill_payment_allocations WHERE payment_id=$1::uuid", pid)
            print("alokasi sebenarnya:", [dict(x) for x in alok])
            for b in bills:
                ap1 = await conn.fetchval("SELECT amount_paid FROM bills WHERE id=$1", b)
                o1 = await conn.fetchval("SELECT COALESCE(SUM(outstanding),0) FROM compute_ap_outstanding($1) WHERE bill_id=$2", T, b)
                print(f"tagihan {str(b)[:8]}: CACHE amount_paid {s0[b][0]} -> {ap1} (delta {(ap1 or 0)-(s0[b][0] or 0)}) | "
                      f"FUNGSI outstanding {s0[b][1]} -> {o1} (delta {o1 - s0[b][1]})")
            r81 = await r8("PAYABLE", "compute_ap_outstanding")
            v71 = await conn.fetchrow("SELECT drift, member_count, verdict FROM hc_verdict('ap_invariant', $1)", T)
            print(f"R8 AP (GL efektif, Σfungsi, drift): {r80} -> {r81}")
            print(f"check_7 hc_verdict ap_invariant: {dict(v70)} -> {dict(v71)}")
            await sp.rollback()

        # ================= AR =================
        print("\n==================== AR (receive_payments) ====================")
        blok = fdef_ar[fdef_ar.find("payment_credits"):fdef_ar.find("payment_credits") + 1400]
        print("definisi hidup, sekitar payment_credits:")
        print(blok)
        cust = await conn.fetchrow(
            """SELECT s.customer_id FROM compute_ar_outstanding($1) a JOIN sales_invoices s ON s.id=a.invoice_id
               WHERE a.outstanding >= 1000 GROUP BY s.customer_id HAVING count(*) >= 2 LIMIT 1""", T)
        if not cust:
            print("SUBJEK AR TAK ADA: tak ada pelanggan dgn >=2 faktur outstanding")
        else:
            invs = [r["invoice_id"] for r in await conn.fetch(
                """SELECT a.invoice_id FROM compute_ar_outstanding($1) a JOIN sales_invoices s ON s.id=a.invoice_id
                   WHERE s.customer_id=$2 AND a.outstanding >= 1000 ORDER BY a.invoice_id LIMIT 2""", T, cust["customer_id"])]
            print("model create receive payment:", [n for n in dir(rps) if n.endswith("Create") or "Create" in n][:8])
            Create = getattr(rps, "ReceivePaymentCreate", None) or getattr(rps, "CreateReceivePaymentRequest", None)
            AllocM = getattr(rps, "ReceivePaymentAllocationCreate", None) or getattr(rps, "AllocationInput", None)
            if not Create or not AllocM:
                print("ALAT: model create/alokasi penerimaan tak dikenali; medan:",
                      {n: list(getattr(rps, n).model_fields) for n in dir(rps) if hasattr(getattr(rps, n), "model_fields")})
            else:
                print("medan Create:", list(Create.model_fields), "| medan alokasi:", list(AllocM.model_fields))
    finally:
        await luar.rollback()
    await conn.close()


asyncio.run(main())
