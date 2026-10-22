"""UJI NYATA Nota Debit U3a (22 Okt 2026). Dijalankan TERHADAP DB SALINAN (milkydb_nd = pg_dump PENUH milkydb + V443), bukan produksi:
handler NYATA (routers/vendor_credits) menulis sungguhan ke salinan; tenant kaos. Siklus + akibat jurnal/AP per langkah (kontrak §1):
  A buat+posting ND bebas 10.000 -> jurnal Dr AP / Cr Retur; AP vendor −10.000; sisa_nd 10.000
  B terapkan ke tagihan T (sisa > 10.000) -> TANPA jurnal baru; sisa T −10.000; baris vendor kembali; aplikasi aktif; original_bill_id = T
  C terapkan ulang -> 409 ; pagar DB: UPDATE original_bill_id langsung -> ditolak
  D lepas (alasan) -> T kembali; aplikasi reversed; original_bill_id NULL; tanpa jurnal
  E refund 4.000 HANYA bank_account_id (dulu TypeError) -> jurnal Dr akun bank / Cr AP; bank_transactions deposit +4.000; sisa_nd 6.000; AP vendor −6.000
  F refund 7.000 -> 400 ; terapkan sesudah refund -> 400 ; void ND ber-refund -> 400
  G ND2 bebas + jurnal HPP & kartu stok SINTETIS -> void: jurnal VENDOR_CREDIT & VENDOR_CREDIT_COGS dibalik (asli POSTED+reversed_by_id),
    kartu stok dibalik, DTL dihapus, AP kembali
  H PATCH draf dgn vendor_credit_date (dulu kolom hantu -> 500) -> credit_date berubah
  I ND3 tertaut saat buat dgn total > sisa tagihan -> posting 400 ; tagihan tenant lain -> 400
MERAH=1 -> harapan satu langkah dibalik (wajib gagal)."""
import asyncio, os, sys, uuid
from datetime import date
from decimal import Decimal
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "x")
import asyncpg
from fastapi import HTTPException
from starlette.requests import Request

T = "kaos-biru-konveksi"
MERAH = os.environ.get("MERAH") == "1"
gagal = []
D = Decimal


def cek(ok, l, i=""):
    if MERAH and l.startswith("A "):
        ok = not ok
    print(("PASS " if ok else "FAIL ") + l + (f" | {i}" if i else ""), flush=True)
    if not ok: gagal.append(l)


def req(uid):
    async def receive(): return {"type": "http.request", "body": b"", "more_body": False}
    r = Request({"type": "http", "method": "POST", "path": "/", "headers": [], "query_string": b""}, receive)
    r.state.user = {"tenant_id": T, "user_id": str(uid)}; return r


async def galat(coro):
    try:
        await coro; return None
    except HTTPException as e:
        return e.status_code


async def main():
    from app.routers import vendor_credits as VC
    from app.schemas import vendor_credits as S
    from app.services import nota_debit as ND
    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=3)
    async def gp(): return pool
    VC.get_pool = gp
    c = await pool.acquire()
    try:
        uid = await c.fetchval('SELECT id FROM "User" WHERE "tenantId"=$1 ORDER BY "createdAt" LIMIT 1', T)
        r = req(uid)
        b = await c.fetchrow("""SELECT bill_id, vendor_id, outstanding FROM compute_ap_outstanding($1)
                                WHERE bill_id IS NOT NULL AND vendor_id IS NOT NULL AND outstanding > 20000 ORDER BY outstanding DESC LIMIT 1""", T)
        vname = await c.fetchval("SELECT vendor_name FROM bills WHERE id=$1", b["bill_id"])
        bank = await c.fetchrow("SELECT id, coa_id FROM bank_accounts WHERE tenant_id=$1 AND coa_id IS NOT NULL ORDER BY created_at LIMIT 1", T)

        async def ap():
            rows = await c.fetch("SELECT * FROM compute_ap_outstanding($1)", T)
            return (sum((x["outstanding"] for x in rows if x["bill_id"] is None and x["vendor_id"] == b["vendor_id"]), D(0)),
                    next((x["outstanding"] for x in rows if x["bill_id"] == b["bill_id"]), D(0)))

        def badan(nominal, bill=None, tgl=date(2026, 10, 22)):
            return S.CreateVendorCreditRequest(vendor_id=str(b["vendor_id"]), vendor_name=vname, vendor_credit_date=tgl, reason="return",
                                               original_bill_id=str(bill) if bill else None, notes="TES E2E U3a",
                                               items=[S.VendorCreditItemCreate(description="Retur TES", quantity=D("1"), unit_price=nominal)])

        v0, t0 = await ap()
        # A
        d = await VC.create_vendor_credit(r, badan(10000)); vc1 = uuid.UUID(d["data"]["id"])
        await VC.post_vendor_credit(r, vc1)
        jl = await c.fetch("""SELECT coa.account_type, jl.debit, jl.credit FROM journal_entries je JOIN journal_lines jl ON jl.journal_id = je.id
                              JOIN chart_of_accounts coa ON coa.id = jl.account_id WHERE je.source_id=$1 AND je.source_type='VENDOR_CREDIT'""", vc1)
        v1, t1 = await ap()
        cek(any(x["account_type"] == "PAYABLE" and x["debit"] == 10000 for x in jl) and (v1 - v0, t1 - t0) == (D(-10000), 0)
            and await ND.sisa_nd(c, T, vc1) == 10000, "A posting: Dr AP, AP vendor −10.000, sisa_nd 10.000", f"{v1-v0},{t1-t0}")
        # B
        nj = await c.fetchval("SELECT count(*) FROM journal_entries WHERE tenant_id=$1", T)
        await VC.apply_vendor_credit(r, vc1, S.ApplyVendorCreditRequest(applications=[{"bill_id": str(b["bill_id"]), "amount": 10000}]))
        v2, t2 = await ap()
        st = await c.fetchrow("SELECT original_bill_id, status, amount_applied FROM vendor_credits WHERE id=$1", vc1)
        cek((v2 - v0, t2 - t0) == (0, D(-10000)) and st["original_bill_id"] == b["bill_id"]
            and await c.fetchval("SELECT count(*) FROM journal_entries WHERE tenant_id=$1", T) == nj
            and await c.fetchval("SELECT status FROM vendor_credit_applications WHERE vendor_credit_id=$1", vc1) == "active",
            "B terapkan: tanpa jurnal, tagihan −10.000, vendor 0, aplikasi aktif", f"{v2-v0},{t2-t0},{dict(st)}")
        # C
        e1 = await galat(VC.apply_vendor_credit(r, vc1, S.ApplyVendorCreditRequest(applications=[{"bill_id": str(b["bill_id"]), "amount": 10000}])))
        try:
            await c.execute("UPDATE vendor_credits SET original_bill_id = NULL WHERE id=$1", vc1); pagar = False
        except asyncpg.CheckViolationError:
            pagar = True
        cek(e1 == 409 and pagar, "C terapkan ulang 409 + pagar DB menolak lepas langsung", f"{e1},{pagar}")
        # D
        await VC.unapply_vendor_credit(r, vc1, S.UnapplyVendorCreditRequest(reason="TES salah tagihan"))
        v3, t3 = await ap()
        cek((v3 - v0, t3 - t0) == (D(-10000), 0) and await c.fetchval("SELECT original_bill_id FROM vendor_credits WHERE id=$1", vc1) is None
            and await c.fetchval("SELECT status FROM vendor_credit_applications WHERE vendor_credit_id=$1", vc1) == "reversed",
            "D lepas: tagihan kembali, aplikasi reversed, kaitan NULL", f"{v3-v0},{t3-t0}")
        # E
        await VC.receive_refund(r, vc1, S.ReceiveRefundRequest(amount=4000, refund_date=date(2026, 10, 22), payment_method="transfer",
                                                               bank_account_id=str(bank["id"])))
        v4, _ = await ap()
        bt = await c.fetchrow("SELECT transaction_type, amount FROM bank_transactions WHERE reference_type='vendor_credit_refund' AND tenant_id=$1 ORDER BY created_at DESC LIMIT 1", T)
        dr = await c.fetchval("""SELECT jl.account_id FROM journal_entries je JOIN journal_lines jl ON jl.journal_id=je.id
                                 WHERE je.source_id=$1 AND je.source_type='VENDOR_CREDIT_REFUND' AND jl.debit > 0""", vc1)
        cek(v4 - v0 == D(-6000) and await ND.sisa_nd(c, T, vc1) == 6000 and bt and bt["transaction_type"] == "deposit" and bt["amount"] == 4000
            and dr == bank["coa_id"], "E refund via bank: Dr akun bank, bank_txn +4.000, sisa_nd 6.000, AP vendor −6.000", f"{v4-v0},{dict(bt) if bt else None}")
        # F
        e2 = await galat(VC.receive_refund(r, vc1, S.ReceiveRefundRequest(amount=7000, refund_date=date(2026, 10, 22), payment_method="transfer",
                                                                          bank_account_id=str(bank["id"]))))
        e3 = await galat(VC.apply_vendor_credit(r, vc1, S.ApplyVendorCreditRequest(applications=[{"bill_id": str(b["bill_id"]), "amount": 10000}])))
        e4 = await galat(VC.void_vendor_credit(r, vc1, S.VoidVendorCreditRequest(reason="TES")))
        cek((e2, e3, e4) == (400, 400, 400), "F refund > sisa / terapkan sesudah refund / void ber-refund -> 400", f"{e2},{e3},{e4}")
        # G
        v5, _ = await ap()
        d = await VC.create_vendor_credit(r, badan(5000)); vc2 = uuid.UUID(d["data"]["id"])
        await VC.post_vendor_credit(r, vc2)
        hpp = await c.fetchval("SELECT account_id FROM account_roles WHERE tenant_id=$1 AND role_key='COGS'", T) or \
            await c.fetchval("SELECT id FROM chart_of_accounts WHERE tenant_id=$1 AND account_code='5-10100'", T)
        inv = await c.fetchval("SELECT id FROM chart_of_accounts WHERE tenant_id=$1 AND account_code = '1-10600'", T)
        ws = await c.fetchrow("SELECT item_id, warehouse_id FROM warehouse_stock WHERE tenant_id=$1 AND quantity >= 1 ORDER BY quantity DESC LIMIT 1", T)
        prod, wh = {"id": ws["item_id"]}, ws["warehouse_id"]
        jc = uuid.uuid4()
        await c.execute("""INSERT INTO journal_entries (id, tenant_id, journal_number, journal_date, description, source_type, source_id, status, total_debit, total_credit)
                           VALUES ($1,$2,$3,tanggal_bisnis($2),'TES HPP','VENDOR_CREDIT_COGS',$4,'DRAFT',700,700)""", jc, T, f"TES-COGS-{str(jc)[:6]}", vc2)
        await c.execute("INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit) VALUES ($1,$2,1,$3,700,0),($4,$2,2,$5,0,700)",
                        uuid.uuid4(), jc, hpp, uuid.uuid4(), inv)
        await c.execute("UPDATE journal_entries SET status='POSTED' WHERE id=$1", jc)
        led = uuid.uuid4()
        await c.execute("""INSERT INTO inventory_ledger (id, tenant_id, product_id, product_name, warehouse_id, movement_type, movement_date, source_type,
                           source_id, source_number, quantity_in, quantity_out, quantity_balance, unit_cost, total_cost, journal_id)
                           VALUES ($1,$2,$3,'TES',$4,'PURCHASE_RETURN',tanggal_bisnis($2),'VENDOR_CREDIT',$5,'TES',0,1,0,700,700,$6)""",
                        led, T, prod["id"], wh, vc2, jc)
        await VC.void_vendor_credit(r, vc2, S.VoidVendorCreditRequest(reason="TES void lengkap"))
        v6, _ = await ap()
        asli = await c.fetch("SELECT source_type, status, reversed_by_id FROM journal_entries WHERE source_id=$1 AND reversal_of_id IS NULL", vc2)
        rev = await c.fetchval("SELECT count(*) FROM journal_entries WHERE source_id=$1 AND reversal_of_id IS NOT NULL", vc2)
        kartu = await c.fetchval("SELECT count(*) FROM inventory_ledger WHERE tenant_id=$1 AND source_type='VENDOR_CREDIT_VOID' AND source_id=$2", T, vc2)
        cek(v6 == v5 and len(asli) == 2 and all(a["status"] == "POSTED" and a["reversed_by_id"] for a in asli) and rev == 2 and kartu == 1
            and await c.fetchval("SELECT status FROM vendor_credits WHERE id=$1", vc2) == "void",
            "G void: jurnal ND + HPP dibalik (asli POSTED+reversed_by_id), kartu stok dibalik, AP kembali", f"{v6-v5},{len(asli)},{rev},{kartu}")
        # H
        d = await VC.create_vendor_credit(r, badan(3000)); vc3 = uuid.UUID(d["data"]["id"])
        e5 = await galat(VC.update_vendor_credit(r, vc3, S.UpdateVendorCreditRequest(vendor_credit_date=date(2026, 10, 20))))
        cek(e5 is None and await c.fetchval("SELECT credit_date FROM vendor_credits WHERE id=$1", vc3) == date(2026, 10, 20),
            "H PATCH vendor_credit_date -> credit_date (dulu kolom hantu)", str(e5))
        # I
        _, ti = await ap()
        d = await VC.create_vendor_credit(r, badan(int(ti) + 1, bill=b["bill_id"])); vc4 = uuid.UUID(d["data"]["id"])
        e6 = await galat(VC.post_vendor_credit(r, vc4))
        lain = await c.fetchval("SELECT id FROM bills WHERE tenant_id='grapgrap-manado' LIMIT 1")
        e7 = await galat(VC.create_vendor_credit(r, badan(1000, bill=lain)))
        cek(e6 == 400 and e7 == 400, "I tertaut saat buat > sisa -> posting 400; tagihan tenant lain -> 400", f"{e6},{e7}")
    finally:
        await pool.release(c); await pool.close()
    print("RINGKAS gagal=%d %s" % (len(gagal), gagal[:6])); sys.exit(1 if gagal else 0)

asyncio.run(main())
