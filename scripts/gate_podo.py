#!/usr/bin/env python3
"""Gerbang purchase_order_no / delivery_order_no.

CAKUPAN JUJUR: jalur HTTP POST/PATCH TIDAK diuji di sini -- akun uji
Collaborator dapat 403 (`required_action: C`). Yang diuji: skema Pydantic
(mekanisme persis yang dipakai pembangun UPDATE dinamis PATCH), response_model,
kolom DB (dalam transaksi yang DIPUTAR BALIK), penurunan nomor surat jalan, dan
kedua template.
"""
import os, sys
sys.path.insert(0, "/app/backend/api_gateway"); sys.path.insert(0, "/w/scripts")
import asyncio, asyncpg
from app.schemas.sales_invoices import (
    CreateInvoiceRequest, UpdateInvoiceRequest, InvoiceDetail,
)
from app.services.pdf_service import get_pdf_service
from gate_tpl import konteks

TENANT = "kaos-biru-konveksi"
gagal = []


def cek(ok, pesan):
    print(f"  {'OK  ' if ok else 'GAGAL'} {pesan}")
    if not ok:
        gagal.append(pesan)


async def utama():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    svc = get_pdf_service()

    print("== 1. semantik absen / null / nilai (mesin PATCH) ==")
    absen = UpdateInvoiceRequest(customer_name="X").model_dump(exclude_unset=True)
    cek("purchase_order_no" not in absen and "delivery_order_no" not in absen,
        "absen -> tidak masuk UPDATE (kolom tak tersentuh)")
    kosong = UpdateInvoiceRequest(purchase_order_no=None, delivery_order_no=None).model_dump(exclude_unset=True)
    cek(kosong == {"purchase_order_no": None, "delivery_order_no": None},
        f"null -> masuk UPDATE sebagai NULL: {kosong}")
    isi = UpdateInvoiceRequest(purchase_order_no="PO-9").model_dump(exclude_unset=True)
    cek(isi == {"purchase_order_no": "PO-9"}, f"satu medan -> hanya itu yang di-UPDATE: {isi}")
    cek("ref_no" not in absen and "ref_no" not in kosong and "ref_no" not in isi,
        "ref_no TIDAK ikut tersentuh (kontrol: jalur chat tak boleh berubah)")

    print("== 2. response_model tidak MEMBUANG medan ==")
    d = InvoiceDetail(id="1", invoice_number="INV-1", customer_name="X",
                      invoice_date="2026-09-05", due_date="2026-09-19",
                      subtotal=1000, total_amount=1000, status="draft",
                      created_at="2026-09-05T00:00:00", updated_at="2026-09-05T00:00:00",
                      purchase_order_no="PO-9", delivery_order_no="SJ-1").model_dump()
    cek(d.get("purchase_order_no") == "PO-9" and d.get("delivery_order_no") == "SJ-1",
        "InvoiceDetail meneruskan keduanya")
    cek(CreateInvoiceRequest.model_fields.keys() >= {"purchase_order_no", "delivery_order_no"},
        "CreateInvoiceRequest menerima keduanya")

    print("== 3. kolom DB bolak-balik (transaksi DIPUTAR BALIK) ==")
    row = await conn.fetchrow(
        """SELECT si.id FROM sales_invoices si JOIN sales_invoice_items sii ON sii.invoice_id=si.id
           WHERE si.tenant_id=$1 AND si.status<>'void'
           GROUP BY si.id ORDER BY count(sii.id) DESC LIMIT 1""", TENANT)
    inv_id = row["id"]
    tr = conn.transaction(); await tr.start()
    await conn.execute("UPDATE sales_invoices SET purchase_order_no=$2, delivery_order_no=$3 WHERE id=$1",
                       inv_id, "PO-PELANGGAN-77", "SJ-UJI-01")
    balik = await conn.fetchrow("SELECT purchase_order_no, delivery_order_no FROM sales_invoices WHERE id=$1", inv_id)
    cek(balik["purchase_order_no"] == "PO-PELANGGAN-77" and balik["delivery_order_no"] == "SJ-UJI-01",
        f"tulis lalu baca persis: {dict(balik)}")
    await conn.execute("UPDATE sales_invoices SET delivery_order_no=NULL WHERE id=$1", inv_id)
    n = await conn.fetchval("SELECT delivery_order_no FROM sales_invoices WHERE id=$1", inv_id)
    cek(n is None, "NULL benar-benar NULL")
    await tr.rollback()
    sesudah = await conn.fetchrow("SELECT purchase_order_no, delivery_order_no FROM sales_invoices WHERE id=$1", inv_id)
    cek(sesudah["purchase_order_no"] is None and sesudah["delivery_order_no"] is None,
        f"PUTAR BALIK bersih, faktur nyata tak berubah: {dict(sesudah)}")

    print("== 4. nomor surat jalan diturunkan saat cetak ==")
    f = await conn.fetchrow(
        """SELECT invoice_id, fulfillment_number FROM invoice_fulfillments
           WHERE tenant_id=$1 AND voided_at IS NULL ORDER BY created_at DESC LIMIT 1""", TENANT)
    if not f:
        cek(False, "tak ada pengiriman untuk diuji")
    else:
        q = """SELECT fulfillment_number FROM invoice_fulfillments
                WHERE invoice_id=$1 AND tenant_id=$2 AND voided_at IS NULL
                ORDER BY fulfillment_date DESC, created_at DESC LIMIT 1"""
        got = await conn.fetchval(q, f["invoice_id"], TENANT)
        cek(got == f["fulfillment_number"], f"kueri turunan menemukan {got!r}")
        lain = await conn.fetchval(q, f["invoice_id"], "tenant-yang-tak-ada")
        cek(lain is None, "KONTROL: tenant lain tidak mendapat nomor DO ini")

    print("== 5. template B menampilkan keduanya; baris tetap ada walau kosong ==")
    ctx = await konteks(conn, inv_id)
    ctx["ref_no"] = "SO-KITA-123"
    tb = svc.jinja_env.get_template("sales_invoice_b.html")
    h_isi = tb.render(**svc._konteks_faktur(dict(ctx, purchase_order_no="PO-PELANGGAN-77", delivery_order_no="SJ-UJI-01")))
    h_kosong = tb.render(**svc._konteks_faktur(dict(ctx, purchase_order_no=None, delivery_order_no=None)))
    cek("PO-PELANGGAN-77" in h_isi and "SJ-UJI-01" in h_isi, "nilai muncul di B")
    cek("Delivery Order No." in h_kosong and "Purchase Order No." in h_kosong,
        "baris TETAP tampil saat kosong")
    cek("SO-KITA-123" not in h_isi,
        "KONTROL: ref_no ('No. Order' kita) TIDAK dicetak sebagai nomor PO pelanggan")

    print("== 6. template A tidak berubah sedikit pun ==")
    from jinja2 import Environment, FileSystemLoader
    prod = Environment(loader=FileSystemLoader("/prod/app/templates/pdf"),
                       autoescape=True)
    prod.filters.update(svc.jinja_env.filters)
    a_uji = svc.jinja_env.get_template("sales_invoice.html").render(**svc._konteks_faktur(ctx))
    a_prod = prod.get_template("sales_invoice.html").render(**svc._konteks_faktur(ctx))
    cek(a_uji == a_prod, f"HTTP A uji vs A produksi: {len(a_uji)} vs {len(a_prod)} karakter, diff kosong={a_uji == a_prod}")

    await conn.close()
    if gagal:
        print("\nGAGAL:")
        for g in gagal:
            print("  - " + g)
        return 1
    print("\nOK: semua gerbang PO/DO hijau.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(utama()))
