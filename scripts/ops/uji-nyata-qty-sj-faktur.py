"""Harness NYATA qty PDF + SJ-FAKTUR-SO-ID (READ ONLY, 14 Okt 2026): render PDF SJ-2609-0001 + INV-2609-0038 grapgrap
dan SJ/faktur kaos ke /out (pdftotext di host), keluarga Lampiran SO-2609-0031 memuat SJ-2609-0001."""
import asyncio, os, sys
sys.path.insert(0, "/app")
import asyncpg
from backend.api_gateway.app.routers import deliveries as DL, sales_invoices as SI, sales_order_lampiran as SL
from backend.api_gateway.app.services.pdf_service import get_pdf_service

gagal = 0
def cek(ok, label, info=""):
    global gagal
    print(("PASS " if ok else "FAIL ") + label + (f" | {info}" if info else ""))
    gagal += 0 if ok else 1

async def main():
    c = await asyncpg.connect(os.environ["DATABASE_URL"])
    await c.execute("BEGIN READ ONLY")
    ps = get_pdf_service()
    try:
        for t, sj, inv in (("grapgrap-manado", "SJ-2609-0001", "INV-2609-0038"),):
            ctx = {"tenant_id": t, "user_id": None, "role": "OWNER"}
            fid = await c.fetchval("SELECT id FROM invoice_fulfillments WHERE tenant_id=$1 AND fulfillment_number=$2", t, sj)
            m = await DL.muat_pdf_surat_jalan(c, ctx, str(fid))
            open(f"/out/{sj}.pdf", "wb").write(ps.generate_delivery_note_pdf(m["delivery_data"]))
            iid = await c.fetchval("SELECT id FROM sales_invoices WHERE tenant_id=$1 AND invoice_number=$2", t, inv)
            m = await SI.muat_pdf_faktur(c, ctx, iid, None)
            open(f"/out/{inv}.pdf", "wb").write(ps.tulis_pdf(ps.render_sales_invoice(m["invoice_data"], m["tpl"])))
            so = await c.fetchval("SELECT id FROM sales_orders WHERE tenant_id=$1 AND order_number='SO-2609-0031'", t)
            kel = await c.fetch(SL._SQL_KELUARGA.split("SELECT m.jenis")[0] + "SELECT jenis, nomor FROM meta", so, t)
            nomor = {(r["jenis"], r["nomor"]) for r in kel}
            print("JSON", sorted(nomor))
            cek(("delivery", sj) in nomor, f"keluarga SO-2609-0031 memuat {sj}")
    finally:
        await c.execute("ROLLBACK")
        await c.close()
    print(f"RINGKAS gagal={gagal}")

asyncio.run(main())
