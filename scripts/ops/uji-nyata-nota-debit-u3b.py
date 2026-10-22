"""UJI NYATA Nota Debit U3b (22 Okt 2026; putusan pemilik: potongan -> 5-10200 PURCHASE_DISCOUNT, tanpa stok, blok > sisa tagihan).
DB SALINAN PENUH milkydb (handler nyata), tenant kaos. Barang TERLACAK berstok dipakai di kedua nota debit.
  P1 potongan (discount) bebas 10.000 + barang terlacak -> Cr 5-10200, NOL kartu stok, NOL jurnal VENDOR_CREDIT_COGS; AP vendor −10.000
  P2 koreksi harga (pricing_error) -> Cr 5-10200 juga
  P3 retur (return) barang sama -> Cr 5-10300 + kartu stok keluar (perilaku lama tetap)
  P4 potongan tertaut tagihan > sisa -> posting 400 (blok)
MERAH=1 -> harapan P1 dibalik."""
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
    if MERAH and l.startswith("P1"):
        ok = not ok
    print(("PASS " if ok else "FAIL ") + l + (f" | {i}" if i else ""), flush=True)
    if not ok: gagal.append(l)


def req(uid):
    async def receive(): return {"type": "http.request", "body": b"", "more_body": False}
    r = Request({"type": "http", "method": "POST", "path": "/", "headers": [], "query_string": b""}, receive)
    r.state.user = {"tenant_id": T, "user_id": str(uid)}; return r


async def main():
    from app.routers import vendor_credits as VC
    from app.schemas import vendor_credits as S
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
        prod = await c.fetchrow("""SELECT p.id FROM products p JOIN warehouse_stock ws ON ws.item_id = p.id AND ws.quantity >= 2
                                   WHERE p.tenant_id = $1 AND p.track_inventory ORDER BY ws.quantity DESC LIMIT 1""", T)
        akun = {r_["role_key"]: r_["account_code"] for r_ in await c.fetch(
            "SELECT ar.role_key, coa.account_code FROM account_roles ar JOIN chart_of_accounts coa ON coa.id = ar.account_id WHERE ar.tenant_id=$1", T)}

        async def nd(reason, nominal, bill=None):
            body = S.CreateVendorCreditRequest(vendor_id=str(b["vendor_id"]), vendor_name=vname, vendor_credit_date=date(2026, 10, 22), reason=reason,
                                               original_bill_id=str(bill) if bill else None, notes="TES E2E U3b",
                                               items=[S.VendorCreditItemCreate(description="TES U3b", quantity=Decimal("1"), unit_price=nominal,
                                                                               item_id=str(prod["id"]) if prod else None)])
            d = await VC.create_vendor_credit(r, body)
            return uuid.UUID(d["data"]["id"])

        async def akibat(vcid):
            kr = await c.fetch("""SELECT coa.account_code FROM journal_entries je JOIN journal_lines jl ON jl.journal_id = je.id
                                  JOIN chart_of_accounts coa ON coa.id = jl.account_id
                                  WHERE je.source_id=$1 AND je.source_type='VENDOR_CREDIT' AND jl.credit > 0""", vcid)
            kartu = await c.fetchval("SELECT count(*) FROM inventory_ledger WHERE tenant_id=$1 AND source_type='VENDOR_CREDIT' AND source_id=$2", T, vcid)
            hpp = await c.fetchval("SELECT count(*) FROM journal_entries WHERE source_id=$1 AND source_type='VENDOR_CREDIT_COGS'", vcid)
            return sorted(x["account_code"] for x in kr), kartu, hpp

        print(f"  barang terlacak: {bool(prod)}; peran PURCHASE_DISCOUNT={akun.get('PURCHASE_DISCOUNT')} COGS_PURCHASE_RETURN={akun.get('COGS_PURCHASE_RETURN')}")
        for nama, alasan in (("P1 potongan (discount)", "discount"), ("P2 koreksi harga (pricing_error)", "pricing_error")):
            v = await nd(alasan, 10000); await VC.post_vendor_credit(r, v)
            kr, kartu, hpp = await akibat(v)
            cek(kr == [akun["PURCHASE_DISCOUNT"]] and kartu == 0 and hpp == 0, f"{nama}: Cr {akun['PURCHASE_DISCOUNT']}, tanpa kartu stok/jurnal HPP",
                f"kredit {kr} kartu {kartu} hpp {hpp}")
        v = await nd("return", 10000); await VC.post_vendor_credit(r, v)
        kr, kartu, hpp = await akibat(v)
        cek(kr == [akun["COGS_PURCHASE_RETURN"]] and (kartu >= 1 if prod else True), f"P3 retur: Cr {akun['COGS_PURCHASE_RETURN']} + kartu stok keluar",
            f"kredit {kr} kartu {kartu} hpp {hpp}")
        sisa = await c.fetchval("SELECT outstanding FROM compute_ap_rincian($1) WHERE bill_id=$2", T, b["bill_id"])
        v = await nd("discount", int(sisa) + 1, bill=b["bill_id"])
        try:
            await VC.post_vendor_credit(r, v); e = None
        except HTTPException as x:
            e = x.status_code
        cek(e == 400, "P4 potongan tertaut > sisa tagihan -> 400", str(e))
    finally:
        await pool.release(c); await pool.close()
    print("RINGKAS gagal=%d %s" % (len(gagal), gagal[:6])); sys.exit(1 if gagal else 0)

asyncio.run(main())
