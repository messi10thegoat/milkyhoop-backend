import os, sys
sys.path.insert(0, "/app/backend/api_gateway"); sys.path.insert(0, "/w/scripts")
import asyncio, asyncpg
from weasyprint import CSS, HTML
from app.services.pdf_service import TEMPLATE_DIR, get_pdf_service
from gate_tpl import konteks

ALAMAT_T = ("Banyuurip Timur RT 02/RW 04 No. 258, Kel. Banyuurip, Kec. Temanggung, "
            "Kab. Temanggung, Jawa Tengah 56211, Indonesia")
ALAMAT_C = ("Jl. Raya Industri Kawasan Berikat Blok C-12 No. 45, Desa Sukamaju, "
            "Kec. Cikarang Selatan, Kab. Bekasi, Jawa Barat 17530")

async def main():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"]); svc = get_pdf_service()
    row = await conn.fetchrow("""SELECT si.id FROM sales_invoices si
        JOIN sales_invoice_items sii ON sii.invoice_id=si.id
        WHERE si.tenant_id='kaos-biru-konveksi' AND si.status<>'void'
        GROUP BY si.id ORDER BY count(sii.id) DESC LIMIT 1""")
    ctx = await konteks(conn, row["id"])
    tpl = svc.jinja_env.get_template("sales_invoice_b.html")
    css = [CSS(filename=str(TEMPLATE_DIR / "invoice_b.css"))]
    it = ctx["items"][0]
    kasus = {
        "kop sepi (data nyata)": ctx,
        "kop PENUH + alamat pelanggan panjang": dict(
            ctx, customer_address=ALAMAT_C, customer_npwp="00.112.303.5-665.100",
            tenant=dict(ctx["tenant"], address=ALAMAT_T, phone="(0293) 491234 - 491235",
                        tax_id="0011230356651000")),
    }
    hasil = {}
    for label, c in kasus.items():
        n = 12
        while n < 40:
            c2 = dict(c, items=[dict(it) for _ in range(n)])
            if len(HTML(string=tpl.render(**svc._konteks_faktur(c2))).render(stylesheets=css).pages) > 1:
                break
            n += 1
        hasil[label] = n - 1
        print(f"  {label}: maksimal {n-1} baris")
    aman = min(hasil.values())
    print(f"==> BARIS_TETAP AMAN = {aman} (minimum lintas kasus); luber dijamin pada >= {aman+2} item")
    await conn.close()
asyncio.run(main())
