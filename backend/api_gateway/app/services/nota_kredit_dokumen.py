"""Dokumen Nota Kredit (U5-B, 4 Okt 2026): pemuat konteks PDF/HTML untuk jenis P5 'credit_note'.

SATU sumber seperti pemuat dokumen SO lain (muat_pdf_*): rute /api/documents/credit_note/{id}/(html|pdf) dan tautan publik
memanggil pemuat ini lalu PDFService.render_credit_note. Angka = KOLOM TERSIMPAN dokumen itu (subtotal, diskon, PPN, total
-- dihitung saat NK dibuat), TIDAK dihitung ulang di sini. Sengaja TIDAK mencetak sisa kredit / jumlah terpakai: itu keadaan
(amount_applied/refunded), bukan isi dokumen yang dikirim ke pelanggan, dan kolomnya bukan sumber kebenaran (Law 1/16)."""
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException

from .rekap_pesanan import muat_kop

ALASAN = {
    "return": "Retur barang",
    "pricing_error": "Koreksi harga",
    "discount": "Diskon / potongan",
    "damaged": "Barang rusak",
    "other": "Lainnya",
}


def _d(v) -> Decimal:
    return Decimal(str(v)) if v is not None else Decimal("0")


async def muat_pdf_nota_kredit(conn, ctx: dict, cn_id) -> dict:
    """-> {credit_note_data, tenant_info}. 404 bila NK tak ada di tenant ini (filter tenant eksplisit)."""
    tid = ctx["tenant_id"]
    try:
        uid = cn_id if isinstance(cn_id, UUID) else UUID(str(cn_id))
    except ValueError:
        raise HTTPException(status_code=404, detail="Nota kredit tidak ditemukan")
    cn = await conn.fetchrow("SELECT * FROM credit_notes WHERE id = $1 AND tenant_id = $2", uid, tid)
    if not cn:
        raise HTTPException(status_code=404, detail="Nota kredit tidak ditemukan")
    items = await conn.fetch(
        """SELECT description, quantity, unit, unit_price, discount_amount, subtotal
           FROM credit_note_items WHERE credit_note_id = $1 ORDER BY line_number""", uid)
    # Faktur asal + pesanan induknya (bila faktur berasal dari SO): untuk baris "Faktur asal" / "Sales Order" di meta.
    faktur = None
    if cn["original_invoice_id"]:
        faktur = await conn.fetchrow(
            """SELECT si.invoice_number, si.invoice_date, si.sales_order_id, so.order_number
               FROM sales_invoices si
               LEFT JOIN sales_orders so ON so.id = si.sales_order_id AND so.tenant_id = si.tenant_id
               WHERE si.id = $1 AND si.tenant_id = $2""", cn["original_invoice_id"], tid)
    from .kode_order import label_cetak
    kode = await label_cetak(conn, tid, faktur["sales_order_id"] if faktur else None)
    data = {
        "credit_note_number": cn["credit_note_number"],
        "credit_note_date": cn["credit_note_date"],
        "status": cn["status"],
        "voided_at": cn["voided_at"],
        "voided_reason": cn["voided_reason"],
        "customer_name": cn["customer_name"],
        "original_invoice_number": (faktur["invoice_number"] if faktur else None) or cn["original_invoice_number"],
        "original_invoice_date": faktur["invoice_date"] if faktur else None,
        "sales_order_number": faktur["order_number"] if faktur else None,
        "order_code_cetak": kode["order_code_cetak"],
        "reason": cn["reason"],
        "reason_label": ALASAN.get(cn["reason"], cn["reason"]),
        "reason_detail": cn["reason_detail"],
        "ref_no": cn["ref_no"],
        "notes": cn["notes"],
        "subtotal": _d(cn["subtotal"]),  # = Sigma BRUTO baris
        "discount_amount": _d(cn["discount_amount"]),  # diskon DOKUMEN (diskon baris terpisah di item_discount_total)
        "tax_rate": _d(cn["tax_rate"]),
        "tax_amount": _d(cn["tax_amount"]),
        "total_amount": _d(cn["total_amount"]),
        "items": [{
            "description": i["description"], "quantity": _d(i["quantity"]), "unit": i["unit"],
            "unit_price": _d(i["unit_price"]), "discount_amount": _d(i["discount_amount"]),
            # subtotal tersimpan = BRUTO baris (qty x harga); neto = subtotal - diskon baris (identitas dijamin
            # services/sales_doc_calc.line_net). Kolom "Jumlah" di dokumen = NETO.
            "net": _d(i["subtotal"]) - _d(i["discount_amount"]),
        } for i in items],
        "item_discount_total": sum((_d(i["discount_amount"]) for i in items), Decimal("0")),
    }
    return {"credit_note_data": data, "tenant_info": await muat_kop(conn, tid)}
