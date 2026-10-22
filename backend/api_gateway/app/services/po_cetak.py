"""PDF PESANAN PEMBELIAN (22 Okt 2026, tiket WORKSPACE "PDF PO"): pemuat konteks /documents/purchase_order/{id}/html|pdf.
Pola = PDF penawaran (templat ringkas penawaran.css; kop usaha muat_kop bersama). Acuan isi: NetSuite/QBO Purchase Order
(nomor, tanggal, tgl kirim diharapkan, kirim ke, vendor, baris, subtotal/diskon/pajak/total, catatan, tanda tangan pemesan).

Angka = nilai TERSIMPAN dokumen (dicetak apa adanya, bukan dihitung ulang): baris "Jumlah" = subtotal - diskon baris (neto,
sama dengan dasar subtotal header po_hitung.hitung_header) sehingga baris menjumlah ke Subtotal. Tenant SELALU eksplisit."""
from fastapi import HTTPException

from .rekap_pesanan import muat_kop


def _tgl(v):
    return v.isoformat() if v else None


def alamat_vendor(v) -> str | None:
    """Alamat satu-dua baris: jalan, lalu kota/provinsi/kode pos (bagian kosong dilewati)."""
    if not v:
        return None
    kota = ", ".join(x for x in (v["city"], v["province"]) if x)
    if v["postal_code"]:
        kota = f"{kota} {v['postal_code']}".strip()
    baris = [x.strip() for x in (v["address"], kota) if x and x.strip()]
    return "\n".join(baris) or None


def label_pajak(tax_rate) -> str:
    try:
        t = float(tax_rate or 0)
    except (TypeError, ValueError):
        t = 0
    return f"Pajak {t:g}%" if t > 0 else "Pajak"


async def muat_pdf_po(conn, ctx, po_id) -> dict:
    """-> {po_data, tenant_info}. 404 bila PO tak ada di tenant ini."""
    from .pdf_service import PDFService
    tid = ctx["tenant_id"]
    po = await conn.fetchrow(
        """SELECT po.*, w.name AS warehouse_name
           FROM purchase_orders po LEFT JOIN warehouses w ON w.id = po.warehouse_id AND w.tenant_id = po.tenant_id
           WHERE po.id = $1 AND po.tenant_id = $2""", po_id, tid)
    if not po:
        raise HTTPException(status_code=404, detail="Pesanan pembelian tidak ditemukan.")
    vendor = await conn.fetchrow(
        """SELECT name, address, city, province, postal_code, phone, mobile_phone, email, tax_id
           FROM vendors WHERE id = $1 AND tenant_id = $2""", po["vendor_id"], tid) if po["vendor_id"] else None
    items = await conn.fetch(
        """SELECT description, item_code, quantity, unit, unit_price, discount_percent, discount_amount, subtotal, total
           FROM purchase_order_items WHERE po_id = $1 ORDER BY line_number, created_at, id""", po["id"])
    baris = []
    for i in items:
        neto = (i["subtotal"] or 0) - (i["discount_amount"] or 0)
        baris.append({"description": i["description"], "item_code": i["item_code"], "quantity": i["quantity"], "unit": i["unit"],
                      "unit_price": i["unit_price"], "discount_percent": i["discount_percent"], "line_total": neto})
    uang = [po["subtotal"], po["discount_amount"], po["tax_amount"], po["total_amount"]] + \
           [b["unit_price"] for b in baris] + [b["line_total"] for b in baris]
    po_data = {
        "id": str(po["id"]), "po_number": po["po_number"], "status": po["status"],
        "po_date": _tgl(po["po_date"]), "expected_date": _tgl(po["expected_date"]),
        "ref_no": po["ref_no"], "ship_to_address": po["ship_to_address"], "warehouse_name": po["warehouse_name"],
        "notes": po["notes"], "cancelled_at": po["cancelled_at"], "cancelled_reason": po["cancelled_reason"],
        "vendor_name": (vendor["name"] if vendor else None) or po["vendor_name"],
        "vendor_alamat": alamat_vendor(vendor),
        "vendor_telepon": (vendor["phone"] or vendor["mobile_phone"]) if vendor else None,
        "vendor_npwp": vendor["tax_id"] if vendor else None,
        "items": baris,
        "subtotal": po["subtotal"], "discount_percent": po["discount_percent"], "discount_amount": po["discount_amount"],
        "tax_amount": po["tax_amount"], "label_pajak": label_pajak(po["tax_rate"]), "total_amount": po["total_amount"],
        "has_cents": PDFService.money_has_cents(*uang),
    }
    return {"po_data": po_data, "tenant_info": await muat_kop(conn, tid)}
