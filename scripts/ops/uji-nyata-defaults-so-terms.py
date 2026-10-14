"""Harness NYATA DEFAULTS-SO-TERMS (READ ONLY / ROLLBACK, 14 Okt 2026): GET /sales-invoices/defaults?sales_order_id=
== due_date yang dibuat jalur /to-invoice/preview (dijalankan di transaksi yang SELALU di-ROLLBACK, sama dengan rute
pratinjau) pada tanggal faktur yang sama. Plus: SO tenant lain -> None (404)."""
import asyncio, os, sys
from datetime import date
sys.path.insert(0, "/app")
import asyncpg
from backend.api_gateway.app.services.default_dokumen import default_faktur
from backend.api_gateway.app.routers import sales_orders as SO
from backend.api_gateway.app.schemas.sales_orders import ConvertToInvoiceRequest

TGL = date(2026, 10, 14)
KASUS = [("grapgrap-manado", "SO-2610-0014", "default"), ("kaos-biru-konveksi", "SO-2609-0146", "so_terms"),
         ("kaos-biru-konveksi", "SO-2609-0003", "customer_terms"), ("kaos-biru-konveksi", "SO-2609-0273", "customer_terms")]
gagal = 0
def cek(ok, label, info=""):
    global gagal
    print(("PASS " if ok else "FAIL ") + label + (f" | {info}" if info else ""))
    gagal += 0 if ok else 1

async def main():
    c = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        for t, no, harap in KASUS:
            so = await c.fetchrow("SELECT id, created_by, payment_terms FROM sales_orders WHERE tenant_id=$1 AND order_number=$2", t, no)
            tr = c.transaction(); await tr.start()
            try:
                d = await default_faktur(c, t, TGL, None, str(so["id"]))
                h = await SO._to_invoice_dalam_tx(c, {"tenant_id": t, "user_id": str(so["created_by"])}, str(so["id"]),
                                                  ConvertToInvoiceRequest(invoice_date=TGL))
                jt = await c.fetchval("SELECT due_date FROM sales_invoices WHERE tenant_id=$1 AND id=$2", t,
                                      h["invoice_id"] if isinstance(h, dict) and "invoice_id" in h else h.get("id"))
            finally:
                await tr.rollback()
            print("JSON", t, no, repr(so["payment_terms"]), d["due_date"], "pratinjau", jt)
            cek(d["due_date"]["value"] == jt.isoformat(), f"{t} {no} defaults == to-invoice/preview due_date")
            cek(d["due_date"]["source"] == harap, f"{t} {no} source {harap}", d["due_date"]["source"])
        lain = await c.fetchval("SELECT id FROM sales_orders WHERE tenant_id='grapgrap-manado' LIMIT 1")
        cek(await default_faktur(c, "kaos-biru-konveksi", TGL, None, str(lain)) is None, "SO tenant lain -> None (404)")
    finally:
        await c.close()
    print(f"RINGKAS gagal={gagal}")

asyncio.run(main())
