"""PDF PENERIMAAN BARANG (25 Okt 2026, audit simetri Pembelian WORKSPACE): pemuat konteks /documents/goods_receipt/{id}/html|pdf.
Kembaran Surat Jalan (deliveries.muat_pdf_surat_jalan): dokumen GUDANG -- barang, qty, satuan, tanpa harga (acuan NetSuite Item Receipt /
SAP Goods Receipt slip). Baris = keterangan BARIS PO (yang dipesan & dicetak di PO), bukan nama master. Qty diretur dicetak hanya bila ada.
GRN batal -> stempel DIBATALKAN (voided_at/voided_reason). Tenant SELALU eksplisit; item dari id GRN yang SUDAH lolos filter tenant."""
from fastapi import HTTPException

from .po_cetak import alamat_vendor
from .rekap_pesanan import muat_kop


async def muat_pdf_goods_receipt(conn, ctx, gr_id) -> dict:
    """-> {gr_data, tenant_info}. 404 bila GRN tak ada di tenant ini."""
    tid = ctx["tenant_id"]
    gr = await conn.fetchrow(
        """SELECT g.id, g.receipt_number, g.receipt_date, g.status, g.notes, g.voided_at, g.voided_reason, g.vendor_id,
                  po.po_number, po.ref_no AS po_ref_no, COALESCE(v.name, po.vendor_name) AS vendor_name, w.name AS warehouse_name
           FROM goods_receipts g
           JOIN purchase_orders po ON po.id = g.purchase_order_id AND po.tenant_id = g.tenant_id
           LEFT JOIN vendors v ON v.id = g.vendor_id AND v.tenant_id = g.tenant_id
           LEFT JOIN warehouses w ON w.id = g.warehouse_id AND w.tenant_id = g.tenant_id
           WHERE g.id = $1 AND g.tenant_id = $2""", gr_id, tid)
    if not gr:
        raise HTTPException(status_code=404, detail="Penerimaan barang tidak ditemukan.")
    vendor = await conn.fetchrow(
        """SELECT address, city, province, postal_code, phone, mobile_phone FROM vendors WHERE id = $1 AND tenant_id = $2""",
        gr["vendor_id"], tid) if gr["vendor_id"] else None
    items = await conn.fetch(
        """SELECT i.quantity, i.quantity_returned, poi.description, poi.item_code, poi.unit
           FROM goods_receipt_items i JOIN purchase_order_items poi ON poi.id = i.purchase_order_item_id
           WHERE i.goods_receipt_id = $1 ORDER BY i.line_number""", gr["id"])
    baris = [{"description": i["description"], "item_code": i["item_code"], "quantity": i["quantity"], "unit": i["unit"],
              "quantity_returned": i["quantity_returned"] or 0} for i in items]
    gr_data = {
        "id": str(gr["id"]), "receipt_number": gr["receipt_number"],
        "receipt_date": gr["receipt_date"].isoformat() if gr["receipt_date"] else None,
        "status": gr["status"], "notes": gr["notes"], "voided_at": gr["voided_at"], "voided_reason": gr["voided_reason"],
        "po_number": gr["po_number"], "po_ref_no": gr["po_ref_no"], "warehouse_name": gr["warehouse_name"],
        "vendor_name": gr["vendor_name"], "vendor_alamat": alamat_vendor(vendor),
        "vendor_telepon": (vendor["phone"] or vendor["mobile_phone"]) if vendor else None,
        "items": baris,
        "ada_retur": any(b["quantity_returned"] for b in baris),
    }
    return {"gr_data": gr_data, "tenant_info": await muat_kop(conn, tid)}
