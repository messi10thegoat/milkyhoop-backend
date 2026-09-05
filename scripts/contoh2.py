"""Contoh faktur A/B — versi yang JUJUR soal logo, rekening, dan satuan.

Versi pertama menggambarkan produk LEBIH BURUK dari kenyataan: harness-ku
hanya meneruskan `logo_url`, padahal rute PDF sungguhan mengubahnya jadi data
URI (`logo_data`). Logo yang hilang di contoh pertama adalah cacat PERKAKASKU,
bukan cacat template. Di sini logo dibaca dari volume yang sama dengan
produksi: /root/milkyhoop-dev/data/logos -> app/static/logos.
"""
import base64, os, sys
sys.path.insert(0, "/app/backend/api_gateway"); sys.path.insert(0, "/w/scripts")
import asyncio, asyncpg
from app.services.pdf_service import get_pdf_service
from gate_tpl import konteks

TENANT = "kaos-biru-konveksi"
LOGO_DIR = "/logos"
KOP = {
    "address": "Jl. Raya Temanggung KM 4, Banyuurip, Kab. Temanggung, Jawa Tengah 56211",
    "phone": "(0293) 491234",
    "tax_id": "00.112.303.5-665.100",
}


def logo_data(nama):
    if not nama:
        return None
    p = os.path.join(LOGO_DIR, nama)
    if not os.path.exists(p):
        print(f"  ! logo {nama} tidak ada di {LOGO_DIR} — contoh akan TANPA logo")
        return None
    with open(p, "rb") as f:
        return "data:image/png;base64," + base64.b64encode(f.read()).decode()


async def main():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"]); svc = get_pdf_service()
    # Faktur yang PUNYA rekening pembayaran, supaya blok bank kiri-bawah terbukti.
    row = await conn.fetchrow(
        """SELECT id, invoice_number, payment_bank_name FROM sales_invoices
            WHERE tenant_id=$1 AND payment_bank_name IS NOT NULL AND status<>'void'
            ORDER BY created_at DESC LIMIT 1""", TENANT)
    ctx = await konteks(conn, row["id"])
    print(f"faktur: {row['invoice_number']} (rekening: {row['payment_bank_name']})")
    print(f"  item: {[(i['description'], i['quantity'], i['unit']) for i in ctx['items']]}")
    ld = logo_data(ctx["tenant"]["logo_url"])
    print(f"  logo_data: {'ADA (' + str(len(ld)) + ' char)' if ld else 'TIDAK ADA'}")
    ctx_contoh = dict(
        ctx,
        tenant=dict(ctx["tenant"], logo_data=ld, **KOP),
        purchase_order_no="PO/TM/2026/0451",
        delivery_order_no="SJ-2609-0002",
    )
    for t in ("a", "b"):
        p = f"/w/contoh-template-{t}.pdf"
        open(p, "wb").write(svc.generate_sales_invoice_pdf(ctx_contoh, template=t))
        print(f"  {p}: {os.path.getsize(p)} byte")
    await conn.close()


# Penjaga __main__: tanpa ini, MENGIMPOR modul ini berarti MENJALANKANNYA —
# ia menembak basis data dan menulis PDF sebagai efek samping impor. Aku kena
# saat menulis contoh_n.py dan mengimpor KOP/logo_data dari sini.
if __name__ == "__main__":
    asyncio.run(main())
