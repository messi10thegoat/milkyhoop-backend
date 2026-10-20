"""Harness NYATA pagar NK potongan (21 Okt 2026). kaos SAJA lewat POST /credit-notes/preview (pratinjau SELALU ROLLBACK
sendiri; grapgrap tak disentuh). (1) discount + 1 baris nominal tanpa barang -> ok, total = nominal, jurnal tanpa stok;
(2) discount tanpa items -> 422 CN_BARIS_WAJIB; (3) return tanpa items (dari faktur) -> tetap jalan (perilaku lama)."""
import asyncio, os, sys
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
sys.path.insert(0, "/app"); os.environ.setdefault("OPENAI_API_KEY", "x")
import asyncpg
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
    n1 = await c.fetchval("SELECT count(*) FROM credit_notes WHERE tenant_id=$1", t)
    cek(n0 == n1, "nol NK tertinggal (pratinjau rollback)", f"{n0} -> {n1}")
    await c.close()
    print("RINGKAS gagal=%d" % gagal)

asyncio.run(main())
