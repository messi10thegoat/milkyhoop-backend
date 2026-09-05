"""Contoh faktur B dengan JUMLAH BARIS berbeda-beda.

Ada supaya tonjolan di bawah dasar tabel diuji pada tabel yang PENUH dan yang
MELUBER, bukan cuma pada faktur 1 baris. Cacat yang hanya muncul saat baris
penuh tak akan terlihat pada satu contoh saja.
"""
import base64
import os
import sys

sys.path.insert(0, "/app/backend/api_gateway")
sys.path.insert(0, "/w/scripts")
import asyncio  # noqa: E402

import asyncpg  # noqa: E402

from app.services.pdf_service import get_pdf_service  # noqa: E402
from gate_tpl import konteks  # noqa: E402

TENANT = "kaos-biru-konveksi"
LOGO_DIR = "/logos"
KOP = {
    "address": "Jl. Raya Temanggung KM 4, Banyuurip, Kab. Temanggung, Jawa Tengah 56211",
    "phone": "(0293) 491234",
    "tax_id": "00.112.303.5-665.100",
}


def logo_data(nama):
    # Disalin dari contoh2.py, BUKAN diimpor: modul itu menjalankan
    # asyncio.run() di tingkat modul, jadi mengimpornya = menjalankannya.
    if not nama:
        return None
    p = os.path.join(LOGO_DIR, nama)
    if not os.path.exists(p):
        return None
    with open(p, "rb") as f:
        return "data:image/png;base64," + base64.b64encode(f.read()).decode()


async def main():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    svc = get_pdf_service()
    row = await conn.fetchrow(
        "SELECT id, invoice_number FROM sales_invoices "
        "WHERE tenant_id=$1 AND payment_bank_name IS NOT NULL AND status<>$2 "
        "ORDER BY created_at DESC LIMIT 1",
        TENANT, "void",
    )
    ctx = await konteks(conn, row["id"])
    ld = logo_data(ctx["tenant"]["logo_url"])
    base = dict(ctx, tenant=dict(ctx["tenant"], logo_data=ld, **KOP),
                purchase_order_no="PO/TM/2026/0451",
                delivery_order_no="SJ-2609-0002")
    for n in (1, 5, 21, 24):
        c = dict(base, items=(ctx["items"] * 30)[:n])
        p = f"/w/n{n}.pdf"
        open(p, "wb").write(svc.generate_sales_invoice_pdf(c, template="b"))
        print(f"  {p}: {n} baris, {os.path.getsize(p)} byte")
    await conn.close()


asyncio.run(main())
