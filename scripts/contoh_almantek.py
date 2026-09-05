import base64, os, sys
sys.path.insert(0, "/app/backend/api_gateway"); sys.path.insert(0, "/w/scripts")
import asyncio, asyncpg
from app.services.pdf_service import get_pdf_service
from gate_tpl import konteks

TENANT = "adhita-ariyani"
LOGO_DIR = "/logos"

def logo_data(nama):
    if not nama:
        return None
    p = os.path.join(LOGO_DIR, nama)
    if not os.path.exists(p):
        print(f"  ! logo {nama} tak ada di {LOGO_DIR}")
        return None
    return "data:image/png;base64," + base64.b64encode(open(p, "rb").read()).decode()

async def main():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"]); svc = get_pdf_service()
    row = await conn.fetchrow(
        "SELECT id, invoice_number FROM sales_invoices WHERE invoice_number='INV-2609-0001' AND tenant_id=$1", TENANT)
    ctx = await konteks(conn, row["id"])
    # NPWP pelanggan: faktur menang, kartu pelanggan mengisi kekosongan --
    # sama persis dengan rute PDF.
    if not ctx.get("customer_npwp"):
        ctx["customer_npwp"] = await conn.fetchval(
            "SELECT c.tax_id FROM customers c JOIN sales_invoices si ON si.customer_id=c.id WHERE si.id=$1", row["id"])
    ctx["tenant"] = dict(ctx["tenant"], logo_data=logo_data(ctx["tenant"]["logo_url"]))
    print(f"faktur: {row['invoice_number']} ({TENANT})")
    print(f"  alamat pelanggan: {ctx['customer_address']!r}")
    print(f"  telepon         : {ctx['customer_phone']!r}")
    print(f"  NPWP            : {ctx['customer_npwp']!r}")
    print(f"  is_pkp          : {ctx['tenant']['is_pkp']}")
    open("/w/contoh-almantek.pdf", "wb").write(svc.generate_sales_invoice_pdf(ctx, template="b"))
    print(f"  /w/contoh-almantek.pdf: {os.path.getsize('/w/contoh-almantek.pdf')} byte")
    await conn.close()
asyncio.run(main())
