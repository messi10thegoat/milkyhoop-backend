"""Harness NYATA pagar NK potongan (21 Okt 2026). kaos SAJA lewat POST /credit-notes/preview (pratinjau SELALU ROLLBACK
sendiri; grapgrap tak disentuh). (1) discount + 1 baris nominal tanpa barang -> ok, total = nominal, jurnal tanpa stok;
(2) discount tanpa items -> 422 CN_BARIS_WAJIB; (3) return tanpa items (dari faktur) -> tetap jalan (perilaku lama).
PUTUSAN PEMILIK 21 Okt (buat+posting+terapkan NYATA di kaos dalam transaksi yang SELALU ROLLBACK): (4) potongan = sisa ->
Dr 4-10200 Diskon / Cr Piutang, faktur Lunas, nol stok; (5) potongan = sisa + 1 -> posting 400 "Melebihi sisa faktur" +
pratinjau blok CN_MELEBIHI_SISA; (6) NK bebas potongan diterapkan > sisa -> 400; (7) retur > sisa -> tetap 4-10300 + KRD."""
import asyncio, os, sys
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
sys.path.insert(0, "/app"); os.environ.setdefault("OPENAI_API_KEY", "x")
import asyncpg
from uuid import UUID
from fastapi import HTTPException
from backend.api_gateway.app.routers import credit_notes as CN

gagal = 0


def cek(ok, l, i=""):
    global gagal
    print(("PASS " if ok else "FAIL ") + l + (f" | {i}" if i else "")); gagal += 0 if ok else 1


async def main():
    c = await asyncpg.connect(os.environ["DATABASE_URL"])
    class Pool:
        def acquire(self_):
            class A:
                async def __aenter__(s): return c
                async def __aexit__(s, *a): return False
            return A()
    async def gp(): return Pool()
    CN.get_pool = gp
    t = "kaos-biru-konveksi"
    u = await c.fetchval('SELECT id FROM "User" WHERE "tenantId"=$1 ORDER BY "createdAt" LIMIT 1', t)
    req = SimpleNamespace(state=SimpleNamespace(user={"tenant_id": t, "user_id": str(u)}), headers={})
    n0 = await c.fetchval("SELECT count(*) FROM credit_notes WHERE tenant_id=$1", t)
    inv = await c.fetchrow(
        """SELECT si.* FROM sales_invoices si JOIN compute_ar_outstanding($1) a ON a.invoice_id = si.id
           WHERE si.tenant_id=$1 AND a.outstanding > 0 AND si.customer_id IS NOT NULL ORDER BY a.outstanding LIMIT 1""", t)
    sisa = await c.fetchval("SELECT outstanding FROM compute_ar_outstanding($1) WHERE invoice_id=$2", t, inv["id"])
    dasar = dict(customer_id=str(inv["customer_id"]), customer_name=inv["customer_name"], credit_note_date=date(2026, 10, 21),
                 original_invoice_id=str(inv["id"]))
    # 1
    b = CN.CreditNotePreviewRequest(reason="discount", reason_detail="Potongan keterlambatan", **dasar,
                                    items=[CN.CreditNoteItemCreate(description="Potongan keterlambatan", quantity=Decimal("1"),
                                                                   unit_price=Decimal(str(sisa)), item_id=None)])
    d = (await CN.preview_credit_note(req, b))["data"]
    tot = (d.get("payload") or {}).get("items") and sum(Decimal(str(i["unit_price"])) for i in d["payload"]["items"])
    cek(d["ok"] and not d["stock_returned_on_post"] and len(d["payload"]["items"]) == 1 and tot == sisa,
        f"(1) discount + 1 baris nominal {sisa} pada {inv['invoice_number']}: ok, tanpa stok", f"blocks={d['blocks']}")
    # 2
    try:
        await CN.preview_credit_note(req, CN.CreditNotePreviewRequest(reason="discount", **dasar))
        cek(False, "(2) discount tanpa items -> 422")
    except HTTPException as e:
        cek(e.status_code == 422 and e.detail["code"] == "CN_BARIS_WAJIB", "(2) discount tanpa items -> 422 CN_BARIS_WAJIB", e.detail["message"])
    # 3
    d3 = (await CN.preview_credit_note(req, CN.CreditNotePreviewRequest(reason="return", **dasar)))["data"]
    cek(d3.get("payload") is not None and len((d3["payload"] or {}).get("items") or []) >= 1,
        "(3) return tanpa items (dari faktur) tetap jalan", f"blocks={d3['blocks']}")
    # ---- 4..7: tulis NYATA dalam transaksi ROLLBACK
    SI = __import__("backend.api_gateway.app.routers.sales_invoices", fromlist=["x"])
    SI.get_pool = gp
    ctx = {"tenant_id": t, "user_id": u}
    part = await c.fetchrow(
        """SELECT si.* FROM sales_invoices si JOIN compute_ar_outstanding($1) a ON a.invoice_id = si.id
           WHERE si.tenant_id=$1 AND a.outstanding > 0 AND a.outstanding < si.total_amount AND si.customer_id IS NOT NULL
           ORDER BY a.outstanding LIMIT 1""", t)

    def badan(f, alasan, nominal, tautan=True, item_id=None):
        return CN.CreateCreditNoteRequest(customer_id=str(f["customer_id"]), customer_name=f["customer_name"],
                                          credit_note_date=date(2026, 10, 21), original_invoice_id=str(f["id"]) if tautan else None,
                                          reason=alasan, reason_detail="TES E2E", notes="TES E2E (ROLLBACK)",
                                          items=[CN.CreditNoteItemCreate(description="TES E2E potongan", quantity=Decimal("1"),
                                                                         unit_price=Decimal(str(nominal)), item_id=item_id)])

    async def jurnal(cid):
        return [(r["account_code"], Decimal(str(r["debit"])), Decimal(str(r["credit"]))) for r in await c.fetch(
            """SELECT coa.account_code, jl.debit, jl.credit FROM journal_entries je JOIN journal_lines jl ON jl.journal_id = je.id
               JOIN chart_of_accounts coa ON coa.id = jl.account_id WHERE je.tenant_id=$1 AND je.source_id=$2""", t, cid)]

    async def tx(fn):
        tr = c.transaction(); await tr.start()
        try:
            return await fn()
        finally:
            await tr.rollback()

    async def k4():
        s0 = await c.fetchval("SELECT outstanding FROM compute_ar_outstanding($1) WHERE invoice_id=$2", t, part["id"])
        st0 = await c.fetchval("SELECT count(*) FROM inventory_ledger WHERE tenant_id=$1", t)
        cid = UUID((await CN.buat_nota_kredit(c, ctx, badan(part, "discount", s0)))["data"]["id"])
        await CN.posting_nota_kredit(c, ctx, cid)
        j = await jurnal(cid)
        det = (await SI.get_invoice(req, part["id"]))["data"]
        st1 = await c.fetchval("SELECT count(*) FROM inventory_ledger WHERE tenant_id=$1", t)
        krd = await c.fetchval("SELECT created_deposit_id FROM credit_notes WHERE id=$1", cid)
        cek(sorted(j) == sorted([("4-10200", s0, Decimal("0")), ("1-10400", Decimal("0"), s0)]) and det["position_text"] == "Lunas"
            and det["amount_due"] == 0 and st1 == st0 and krd is None,
            f"(4) potongan = sisa {s0} ({part['invoice_number']}): Dr 4-10200 / Cr 1-10400, Lunas, nol stok, tanpa KRD", str(j))
    await tx(k4)

    async def k5():
        s0 = await c.fetchval("SELECT outstanding FROM compute_ar_outstanding($1) WHERE invoice_id=$2", t, part["id"])
        r = await CN._rencana_nota_kredit(c, ctx, badan(part, "discount", s0 + 1))
        kode = [b["code"] for b in r["blocks"]]
        cid = UUID((await CN.buat_nota_kredit(c, ctx, badan(part, "discount", s0 + 1)))["data"]["id"])
        try:
            await CN.posting_nota_kredit(c, ctx, cid)
            cek(False, "(5) potongan = sisa + 1 -> posting 400")
        except HTTPException as e:
            cek(e.status_code == 400 and str(e.detail).startswith("Melebihi sisa faktur") and "CN_MELEBIHI_SISA" in kode,
                "(5) potongan = sisa + 1 -> posting 400 + pratinjau CN_MELEBIHI_SISA", str(e.detail))
    await tx(k5)

    async def k6():
        s0 = await c.fetchval("SELECT outstanding FROM compute_ar_outstanding($1) WHERE invoice_id=$2", t, part["id"])
        cid = UUID((await CN.buat_nota_kredit(c, ctx, badan(part, "discount", s0 + 1, tautan=False)))["data"]["id"])
        await CN.posting_nota_kredit(c, ctx, cid)
        try:
            await CN.apply_nota_kredit_core(c, ctx, cid, CN.ApplyCreditNoteRequest(applications=[{"invoice_id": str(part["id"]), "amount": s0 + 1}]))
            cek(False, "(6) NK bebas potongan diterapkan > sisa -> 400")
        except HTTPException as e:
            cek(e.status_code == 400 and str(e.detail).startswith("Melebihi sisa faktur"), "(6) NK bebas potongan diterapkan > sisa -> 400", str(e.detail))
    await tx(k6)

    async def k7():
        s0 = await c.fetchval("SELECT outstanding FROM compute_ar_outstanding($1) WHERE invoice_id=$2", t, part["id"])
        cid = UUID((await CN.buat_nota_kredit(c, ctx, badan(part, "other", s0 + 1000)))["data"]["id"])
        await CN.posting_nota_kredit(c, ctx, cid)
        j = await jurnal(cid)
        krd = await c.fetchval("SELECT created_deposit_id FROM credit_notes WHERE id=$1", cid)
        cek(("4-10300", s0 + 1000, Decimal("0")) in j and krd is not None and not any(a == "4-10200" for a, _, _ in j),
            "(7) NK alasan lain > sisa: tetap 4-10300 + kelebihan jadi KRD (putusan 30 Sep tetap)", str(j))
    await tx(k7)

    n1 = await c.fetchval("SELECT count(*) FROM credit_notes WHERE tenant_id=$1", t)
    cek(n0 == n1, "nol NK tertinggal (pratinjau + transaksi uji ROLLBACK)", f"{n0} -> {n1}")
    await c.close()
    print("RINGKAS gagal=%d" % gagal)

asyncio.run(main())
