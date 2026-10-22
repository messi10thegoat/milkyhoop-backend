"""UJI NYATA penentu aksi Faktur Pembelian (22 Okt 2026): POST /bills/{id}/post|void|delete/preview == tulisan post_bill/void_bill/delete_bill.
SATU transaksi SELALU ROLLBACK, tenant kaos, baris sintetis 'TES E2E' (nol tersisa).
  P1 post/preview draf -> ok; baris jurnal pratinjau == baris jurnal yang DITULIS post_bill (akun, debit, kredit)
  P2 post/preview tagihan posted -> BUKAN_DRAF; post_bill menolak dengan pesan == blok pertama
  H1 delete/preview draf -> ok;  H2 posted -> BUKAN_DRAF == pesan delete_bill
  V1 void/preview posted + UM vendor diterapkan -> ok, deposits_released 1; void_bill: jurnal pembalik == pratinjau, penerapan 'reversed'
     (putusan pemilik: UM dilepas otomatis), respons deposits_released 1
  V2 void/preview tagihan berbayar -> ADA_PEMBAYARAN (nomor BP + nominal) == pesan void_bill
  V3 void/preview tagihan ber-ND aktif -> ADA_NOTA_DEBIT == pesan void_bill (putusan pemilik: blok)
  X  id asing -> 404
MERAH=1 : pratinjau posting disabotase (debit<->kredit ditukar) -> P1 WAJIB gagal."""
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


def cek(ok, l, i=""):
    print(("PASS " if ok else "FAIL ") + l + (f" | {i}" if i else ""), flush=True)
    if not ok: gagal.append(l)


def D(v): return Decimal(str(v if v is not None else 0)).quantize(Decimal("0.01"))


async def main():
    from app.routers import bills as BR, bill_payments as BPR, vendor_credits as VCR, vendor_deposits as VDR
    from app.schemas import bills as S, bill_payments as SBP, vendor_credits as SV, vendor_deposits as SD
    from app.services import bills_service as BS, tagihan_aksi as TA
    c = await asyncpg.connect(os.environ["DATABASE_URL"])
    tx = c.transaction(); await tx.start()
    class CM:
        async def __aenter__(s): return c
        async def __aexit__(s, *a): return False
    class P:
        def acquire(s): return CM()
    async def gp(): return P()
    for m in (BR, BPR, VCR, VDR):
        m.get_pool = gp
    svc = BS.BillsService(P())
    if MERAH:
        _b = TA._bernama
        async def rusak(conn, tid, baris):
            return await _b(conn, tid, [{**x, "debit": x["credit"], "credit": x["debit"]} for x in baris])
        TA._bernama = rusak
    try:
        uid = await c.fetchval('SELECT id FROM "User" WHERE "tenantId" = $1 ORDER BY "createdAt" LIMIT 1', T)
        v = await c.fetchrow("SELECT id, name FROM vendors WHERE tenant_id = $1 ORDER BY created_at LIMIT 1", T)
        ba = await c.fetchval("SELECT id FROM bank_accounts WHERE tenant_id = $1 AND is_active ORDER BY is_default DESC LIMIT 1", T)

        def req():
            async def receive(): return {"type": "http.request", "body": b"", "more_body": False}
            r = Request({"type": "http", "method": "POST", "path": "/", "headers": [], "query_string": b""}, receive)
            r.state.user = {"tenant_id": T, "user_id": str(uid)}; return r

        async def draf(nominal=100000):
            cr = await svc.create_bill_v2(T, S.CreateBillRequestV2(vendor_id=str(v["id"]), due_date=date(2026, 11, 22), issue_date=date(2026, 10, 22),
                                                                   notes="TES E2E aksi", items=[S.BillItemRequestV2(product_name="TES A", qty=1, price=nominal)]).model_dump(), uid)
            return uuid.UUID(cr["data"]["id"])

        async def baris_jurnal(jid):
            return sorted((str(r["account_id"]), str(D(r["debit"])), str(D(r["credit"]))) for r in await c.fetch(
                "SELECT account_id, debit, credit FROM journal_lines WHERE journal_id = $1", jid))

        def baris_pv(lines):
            return sorted((x["account_id"], str(D(x["debit"])), str(D(x["credit"]))) for x in lines)

        # P1 / H1
        a = await draf()
        pv = (await BR.preview_post_bill(req(), a))["data"]
        hv = (await BR.preview_delete_bill(req(), a))["data"]
        cek(hv["ok"], "H1 delete/preview draf -> ok", str(hv["blocks"]))
        hp = await svc.post_bill(T, a, uid)
        jid = await c.fetchval("SELECT journal_id FROM bills WHERE id = $1", a)
        cek(pv["ok"] and hp.get("success") and baris_pv(pv["journal_lines"]) == await baris_jurnal(jid),
            "P1 post/preview == jurnal yang DITULIS post_bill", f"pv {baris_pv(pv['journal_lines'])} tulis {await baris_jurnal(jid)}")
        # P2 / H2
        pv2 = (await BR.preview_post_bill(req(), a))["data"]
        hp2 = await svc.post_bill(T, a, uid)
        cek(not pv2["ok"] and pv2["blocks"][0]["code"] == "BUKAN_DRAF" and hp2["message"] == pv2["blocks"][0]["message"],
            "P2 posted: BUKAN_DRAF == pesan post_bill", f"{pv2['blocks']} / {hp2.get('message')}")
        hv2 = (await BR.preview_delete_bill(req(), a))["data"]
        hd = await svc.delete_bill(T, a, uid)
        cek(not hv2["ok"] and hd["message"] == hv2["blocks"][0]["message"], "H2 delete posted: blok == pesan delete_bill", f"{hv2['blocks']} / {hd.get('message')}")
        # V1: UM vendor diterapkan -> dilepas otomatis
        vd = await VDR.create_vendor_deposit(req(), SD.VendorDepositCreate(deposit_date=date(2026, 10, 22), vendor_id=v["id"], amount="1000",
                                                                            bank_account_id=ba, reference="TES E2E aksi", simpan_dan_posting=True))
        did = uuid.UUID(str(vd["id"]))
        ap = await VDR.apply_vendor_deposit(req(), did, SD.ApplyDepositRequest(bill_id=a, amount="1000"))
        app_id = uuid.UUID(str(ap["application_id"]))
        vv = (await BR.preview_void_bill(req(), a, {"reason": "TES E2E void"}))["data"]
        hv3 = await svc.void_bill(T, a, {"reason": "TES E2E void"}, uid)
        rev = await c.fetchval("SELECT reversed_by_id FROM journal_entries WHERE id = $1", jid)
        st_app = await c.fetchval("SELECT status FROM vendor_deposit_applications WHERE id = $1", app_id)
        cek(vv["ok"] and len(vv["deposits_released"]) == 1 and hv3.get("success") and rev is not None
            and baris_pv(vv["journal_lines"]) == await baris_jurnal(rev) and st_app == "reversed" and len(hv3["data"]["deposits_released"]) == 1,
            "V1 void: jurnal pembalik == pratinjau; UM dilepas otomatis (reversed)", f"ok={vv['ok']} rel={vv['deposits_released']} app={st_app} {hv3.get('message')}")
        # V2: tagihan berbayar
        b = await draf(50000); await svc.post_bill(T, b, uid)
        await BPR.create_bill_payment(req(), SBP.CreateBillPaymentRequest(vendor_id=str(v["id"]), payment_date=date(2026, 10, 22), bank_account_id=str(ba),
                                                                           total_amount=20000, allocations=[SBP.BillAllocationInput(bill_id=str(b), amount_applied=20000)]))
        vv = (await BR.preview_void_bill(req(), b, None))["data"]
        hv4 = await svc.void_bill(T, b, {"reason": "TES"}, uid)
        cek(not vv["ok"] and vv["blocks"][0]["code"] == "ADA_PEMBAYARAN" and "Rp" in vv["blocks"][0]["message"] and hv4["message"] == vv["blocks"][0]["message"],
            "V2 berbayar: ADA_PEMBAYARAN (nomor+nominal) == pesan void_bill", f"{vv['blocks']} / {hv4.get('message')}")
        # V3: nota debit aktif
        cbill = await draf(80000); await svc.post_bill(T, cbill, uid)
        nd = await VCR.create_vendor_credit(req(), SV.CreateVendorCreditRequest(vendor_id=str(v["id"]), vendor_name=v["name"], vendor_credit_date=date(2026, 10, 22),
                                                                                reason="discount", notes="TES E2E aksi",
                                                                                items=[SV.VendorCreditItemCreate(description="TES ND", quantity=Decimal("1"), unit_price=5000)]))
        ndid = uuid.UUID(nd["data"]["id"])
        await VCR.post_vendor_credit(req(), ndid)
        await VCR.apply_vendor_credit(req(), ndid, SV.ApplyVendorCreditRequest(applications=[SV.ApplyVendorCreditItem(bill_id=str(cbill), amount=5000)]))
        vv = (await BR.preview_void_bill(req(), cbill, None))["data"]
        hv5 = await svc.void_bill(T, cbill, {"reason": "TES"}, uid)
        cek(not vv["ok"] and vv["blocks"][0]["code"] == "ADA_NOTA_DEBIT" and hv5["message"] == vv["blocks"][0]["message"],
            "V3 ND aktif: ADA_NOTA_DEBIT == pesan void_bill", f"{vv['blocks']} / {hv5.get('message')}")
        try:
            await BR.preview_post_bill(req(), uuid.uuid4()); st = 200
        except HTTPException as e:
            st = e.status_code
        cek(st == 404, "X id asing -> 404", str(st))
    finally:
        await tx.rollback(); await c.close()
    print("RINGKAS gagal=%d %s" % (len(gagal), gagal[:6])); sys.exit(1 if gagal else 0)

asyncio.run(main())
