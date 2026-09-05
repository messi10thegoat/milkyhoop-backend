"""Render contoh faktur NYATA kaos-biru dengan template A dan B.

Nilai kop (alamat/telepon/NPWP) disuntikkan ke KONTEKS saja, TIDAK ditulis ke
basis data: contoh visual tidak sepadan dengan risiko meninggalkan profil
tenant nyata dalam keadaan salah kalau langkah pemulihan gagal.
"""
import os, sys
sys.path.insert(0, "/app/backend/api_gateway"); sys.path.insert(0, "/w/scripts")
import asyncio, asyncpg
from app.services.pdf_service import get_pdf_service
from gate_tpl import konteks

KOP = {
    "address": "Jl. Raya Temanggung KM 4, Banyuurip, Kab. Temanggung, Jawa Tengah 56211",
    "phone": "(0293) 491234",
    "tax_id": "00.112.303.5-665.100",
}

async def main():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"]); svc = get_pdf_service()
    row = await conn.fetchrow("""SELECT si.id, si.invoice_number, count(sii.id) AS n
        FROM sales_invoices si JOIN sales_invoice_items sii ON sii.invoice_id=si.id
        WHERE si.tenant_id='kaos-biru-konveksi' AND si.status<>'void'
        GROUP BY si.id, si.invoice_number ORDER BY count(sii.id) DESC LIMIT 1""")
    ctx = await konteks(conn, row["id"])
    print(f"faktur: {row['invoice_number']} ({row['n']} item)")
    print(f"kop dari DB (TIDAK diubah): address={ctx['tenant']['address']!r} tax_id={ctx['tenant']['tax_id']!r}")
    ctx_contoh = dict(ctx, tenant=dict(ctx["tenant"], **KOP),
                      purchase_order_no="PO/TM/2026/0451",
                      delivery_order_no="SJ-2609-0002")
    for t in ("a", "b"):
        p = f"/w/contoh-template-{t}.pdf"
        open(p, "wb").write(svc.generate_sales_invoice_pdf(ctx_contoh, template=t))
        print(f"  {p}: {os.path.getsize(p)} byte")
    # bukti bahwa DB memang tak tersentuh, dibaca ULANG setelah render
    ulang = await conn.fetchrow('SELECT address, tax_id, pdf_template FROM "Tenant" WHERE id=$1', "kaos-biru-konveksi")
    print(f"DB sesudah render: address={ulang['address']!r} tax_id={ulang['tax_id']!r} pdf_template={ulang['pdf_template']!r}")
    await conn.close()
asyncio.run(main())
