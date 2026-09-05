#!/usr/bin/env python3
"""Gerbang: sales_order_id/number di respons detail, dan "" -> NULL untuk PO/DO."""
import os, sys
sys.path.insert(0, "/app/backend/api_gateway")
import asyncio, asyncpg
from app.schemas.sales_invoices import UpdateInvoiceRequest, InvoiceDetail

TENANT = "kaos-biru-konveksi"
gagal = []

def cek(ok, pesan):
    print(f"  {'OK  ' if ok else 'GAGAL'} {pesan}")
    if not ok:
        gagal.append(pesan)

async def utama():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])

    print('== 1. "" dan null sama-sama mengosongkan ==')
    for nilai in ("", "   ", None):
        d = UpdateInvoiceRequest(purchase_order_no=nilai, delivery_order_no=nilai).model_dump(exclude_unset=True)
        cek(d == {"purchase_order_no": None, "delivery_order_no": None},
            f"{nilai!r} -> {d}")
    d = UpdateInvoiceRequest(purchase_order_no="PO-9").model_dump(exclude_unset=True)
    cek(d == {"purchase_order_no": "PO-9"}, f"nilai nyata tetap utuh: {d}")
    # KONTROL MERAH: ref_no TIDAK punya validator ini, jadi "" harus tetap ""
    dr = UpdateInvoiceRequest(ref_no="").model_dump(exclude_unset=True)
    cek(dr == {"ref_no": ""},
        f"KONTROL: ref_no tanpa validator tetap '' -> {dr} (membuktikan yang mengubah adalah validator, bukan Pydantic)")

    print("== 2. response_model meneruskan tautan Pesanan ==")
    d = InvoiceDetail(id="1", invoice_number="INV-1", customer_name="X",
                      invoice_date="2026-09-05", due_date="2026-09-19",
                      subtotal=1, total_amount=1, status="draft",
                      created_at="2026-09-05T00:00:00", updated_at="2026-09-05T00:00:00",
                      sales_order_id="abc", sales_order_number="SO-001").model_dump()
    cek(d.get("sales_order_id") == "abc" and d.get("sales_order_number") == "SO-001",
        "InvoiceDetail meneruskan sales_order_id + sales_order_number")

    print("== 3. kueri detail: dengan SO dan tanpa SO ==")
    Q = """SELECT si.id, si.sales_order_id, so.order_number AS sales_order_number
             FROM sales_invoices si
             LEFT JOIN sales_orders so ON so.id = si.sales_order_id
            WHERE si.tenant_id=$1 AND si.sales_order_id IS %s NULL
            LIMIT 1"""
    dgn = await conn.fetchrow(Q % "NOT", TENANT)
    tanpa = await conn.fetchrow(Q % "", TENANT)
    if dgn:
        cek(dgn["sales_order_number"] is not None,
            f"faktur dari SO -> nomor {dgn['sales_order_number']!r}")
    else:
        cek(False, "tak ada faktur ber-SO untuk diuji")
    if tanpa:
        cek(tanpa["sales_order_number"] is None,
            "KONTROL POSITIF: faktur tanpa SO -> nomor NULL")
    else:
        print("  (tak ada faktur tanpa SO di tenant ini)")

    await conn.close()
    if gagal:
        print("\nGAGAL:")
        for x in gagal:
            print("  - " + x)
        return 1
    print("\nOK: hijau.")
    return 0

if __name__ == "__main__":
    sys.exit(asyncio.run(utama()))
