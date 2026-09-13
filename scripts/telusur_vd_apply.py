"""TELUSUR (bukan gerbang) jalur terapkan uang muka vendor. Satu transaksi luar, ROLLBACK. Nol tulis bertahan.
argv[1]: 'hidup' (modul hidup) | 'ganti' (salinan dgn kolom hantu diganti: paid_amount->amount_paid,
total_amount->amount, bill_number->invoice_number). Tiap tahap: buat -> posting -> terapkan -> ukur.
Galat bergeser = BERHENTI dan laporkan (jangan tambal berlapis tanpa melihat).
"""
import asyncio
import importlib.util
import os
import sys
import traceback
from datetime import date

sys.path.insert(0, "/app/backend/api_gateway")
import asyncpg  # noqa: E402
from starlette.requests import Request  # noqa: E402

MODE = sys.argv[1]
T = "kaos-biru-konveksi"
SRC = "/app/backend/api_gateway/app/routers/vendor_deposits.py"


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
    src = open(SRC, encoding="utf-8").read()
    if MODE == "ganti":
        for a, b in (("paid_amount", "amount_paid"), ("total_amount", "amount"), ("bill['bill_number']", "bill['invoice_number']"),
                     ('bill["bill_number"]', 'bill["invoice_number"]')):
            print(f"ganti {a!r}: {src.count(a)}x")
            src = src.replace(a, b)
    open("/tmp/vd_telusur.py", "w", encoding="utf-8").write(src)
    import app.routers  # noqa: F401
    spec = importlib.util.spec_from_file_location("app.routers.vd_telusur", "/tmp/vd_telusur.py")
    m = importlib.util.module_from_spec(spec); sys.modules[spec.name] = m; spec.loader.exec_module(m)
    import app.schemas.vendor_deposits as s
    print("VendorDepositCreate fields:", {k: str(v.annotation) for k, v in s.VendorDepositCreate.model_fields.items()})

    uid = await conn.fetchval("SELECT created_by FROM bank_transactions WHERE tenant_id=$1 AND created_by IS NOT NULL LIMIT 1", T)
    rq = lambda: Request({"type": "http", "method": "POST", "path": "/", "headers": [], "query_string": b"",  # noqa: E731
                          "state": {"user": {"tenant_id": T, "user_id": str(uid)}}})
    bill = await conn.fetchrow("""SELECT o.bill_id, o.outstanding, b.vendor_id, b.status, b.status_v2, b.amount, b.amount_paid
        FROM compute_ap_outstanding($1) o JOIN bills b ON b.id=o.bill_id
        WHERE o.bill_status NOT IN ('vendor_credit') AND o.outstanding >= 1000 AND b.status IN ('posted','partial') ORDER BY o.outstanding LIMIT 1""", T)
    ba = await conn.fetchrow("SELECT id, coa_id FROM bank_accounts WHERE tenant_id=$1 AND is_active ORDER BY account_name LIMIT 1", T)
    print("subjek tagihan", dict(bill) if bill else None, "bank", dict(ba) if ba else None)
    if not bill:
        print("TAK SAH: tak ada tagihan ber-outstanding"); sys.exit(2)

    luar = conn.transaction(); await luar.start()
    try:
        async def ap():
            gl = await conn.fetchval("""SELECT COALESCE(SUM(jl.credit-jl.debit),0) FROM journal_lines jl JOIN journal_entries je ON je.id=jl.journal_id
                JOIN chart_of_accounts c ON c.id=jl.account_id WHERE je.tenant_id=$1 AND je.status='POSTED' AND c.account_type='PAYABLE'""", T)
            out = await conn.fetchval("SELECT COALESCE(SUM(outstanding),0) FROM compute_ap_outstanding($1) WHERE bill_id=$2", T, bill["bill_id"])
            b = await conn.fetchrow("SELECT amount, amount_paid, status, status_v2 FROM bills WHERE id=$1", bill["bill_id"])
            return {"gl_ap": gl, "out": out, "bill": tuple(b)}

        kw = dict(deposit_date=date.today(), vendor_id=bill["vendor_id"], amount=5000, payment_method="transfer",
                  bank_account_id=ba["id"], reference="telusur", notes="telusur")
        kw = {k: v for k, v in kw.items() if k in s.VendorDepositCreate.model_fields}
        tahap = "1 buat"
        r = await m.create_vendor_deposit(rq(), s.VendorDepositCreate(**kw))
        did = r.id if hasattr(r, "id") else (r.get("id") or r["data"]["id"])
        print(tahap, "OK", did)
        tahap = "2 posting"
        await m.post_vendor_deposit(rq(), did)
        print(tahap, "OK", dict(await conn.fetchrow("SELECT status, remaining_amount, applied_amount FROM vendor_deposits WHERE id=$1", did)))
        m0 = await ap(); print("ukur sebelum apply", m0)
        tahap = "3 terapkan"
        r = await m.apply_vendor_deposit(rq(), did, s.ApplyDepositRequest(bill_id=bill["bill_id"], amount=1000))
        print(tahap, "OK", r)
        m1 = await ap(); print("ukur sesudah apply", m1)
        print("Δ out", m1["out"] - m0["out"], "Δ GL AP", m1["gl_ap"] - m0["gl_ap"],
              "VD", dict(await conn.fetchrow("SELECT status, remaining_amount, applied_amount FROM vendor_deposits WHERE id=$1", did)))
    except Exception as e:  # noqa: BLE001
        print(f"GALAT di tahap {tahap}: {type(e).__name__}: {getattr(e, 'detail', e)}")
        traceback.print_exc(limit=3)
    finally:
        await luar.rollback()
        print("ROLLBACK; VD telusur tersisa =", await conn.fetchval("SELECT count(*) FROM vendor_deposits WHERE reference='telusur'"))
        await conn.close()


asyncio.run(main())
