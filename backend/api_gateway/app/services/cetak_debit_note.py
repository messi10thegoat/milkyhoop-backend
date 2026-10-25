"""Cetak Nota Debit (P2P cetak, 25 Okt 2026): pemuat konteks /api/documents/debit_note/{id}/(html|pdf).

Simetri services/nota_kredit_dokumen: angka = KOLOM TERSIMPAN nota debit (dihitung saat dibuat, dasar jurnalnya), tidak
dihitung ulang. Sisa kredit / terpakai / refund TIDAK dicetak: itu keadaan, bukan isi dokumen (Law 1/16).
Ringkasan WAJIB cocok dengan total tersimpan: diskon baris hanya dicetak bila total tersimpan memang menguranginya."""
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException

from .rekap_pesanan import muat_kop

PESAN_TAK_ADA = "Nota debit tidak ditemukan."

ALASAN = {
    "return": "Retur barang",
    "pricing_error": "Koreksi harga",
    "discount": "Diskon / potongan",
    "damaged": "Barang rusak",
    "other": "Lainnya",
}


def _d(v) -> Decimal:
    return Decimal(str(v)) if v is not None else Decimal("0")


def susun_ringkasan(subtotal, diskon_dok, pajak, total, baris: list) -> dict:
    """MURNI. -> {item_discount_total, net_per_baris}. Diskon baris dicetak (Jumlah = neto) hanya bila
    subtotal - Σdiskon baris - diskon dokumen + PPN == total tersimpan; selain itu Jumlah = bruto dan tanpa baris
    diskon item, supaya angka di kertas selalu menjumlah ke total yang dijurnal."""
    diskon_baris = sum((_d(b["discount_amount"]) for b in baris), Decimal("0"))
    pakai = diskon_baris > 0 and abs(_d(subtotal) - diskon_baris - _d(diskon_dok) + _d(pajak) - _d(total)) < Decimal("0.005")
    return {
        "item_discount_total": diskon_baris if pakai else Decimal("0"),
        "net": [_d(b["subtotal"]) - (_d(b["discount_amount"]) if pakai else Decimal("0")) for b in baris],
        "diskon_baris_dicetak": pakai,
    }


async def muat_pdf_debit_note(conn, ctx: dict, vc_id) -> dict:
    """-> {debit_note_data, tenant_info}. 404 bila nota debit tak ada di tenant ini (filter tenant eksplisit)."""
    tid = ctx["tenant_id"]
    try:
        uid = vc_id if isinstance(vc_id, UUID) else UUID(str(vc_id))
    except ValueError:
        raise HTTPException(status_code=404, detail=PESAN_TAK_ADA)
    vc = await conn.fetchrow("SELECT * FROM vendor_credits WHERE id = $1 AND tenant_id = $2", uid, tid)
    if not vc:
        raise HTTPException(status_code=404, detail=PESAN_TAK_ADA)
    items = await conn.fetch(
        """SELECT description, quantity, unit, unit_price, discount_amount, subtotal
           FROM vendor_credit_items WHERE vendor_credit_id = $1 ORDER BY line_number""", uid)
    faktur = None
    if vc["original_bill_id"]:
        faktur = await conn.fetchrow(
            "SELECT invoice_number, issue_date FROM bills WHERE id = $1 AND tenant_id = $2", vc["original_bill_id"], tid)
    r = susun_ringkasan(vc["subtotal"], vc["discount_amount"], vc["tax_amount"], vc["total_amount"], items)
    data = {
        "credit_number": vc["credit_number"],
        "credit_date": vc["credit_date"],
        "status": vc["status"],
        "voided_at": vc["voided_at"],
        "voided_reason": vc["voided_reason"],
        "vendor_name": vc["vendor_name"],
        "original_bill_number": (faktur["invoice_number"] if faktur else None) or vc["original_bill_number"],
        "original_bill_date": faktur["issue_date"] if faktur else None,
        "reason": vc["reason"],
        "reason_label": ALASAN.get(vc["reason"], vc["reason"]),
        "reason_detail": vc["reason_detail"],
        "ref_no": vc["ref_no"],
        "notes": vc["notes"],
        "subtotal": _d(vc["subtotal"]),  # = Σ BRUTO baris
        "discount_amount": _d(vc["discount_amount"]),  # diskon DOKUMEN
        "tax_rate": _d(vc["tax_rate"]),
        "tax_amount": _d(vc["tax_amount"]),
        "total_amount": _d(vc["total_amount"]),
        "items": [{
            "description": i["description"], "quantity": _d(i["quantity"]), "unit": i["unit"],
            "unit_price": _d(i["unit_price"]),
            "discount_amount": _d(i["discount_amount"]) if r["diskon_baris_dicetak"] else Decimal("0"),
            "net": n,
        } for i, n in zip(items, r["net"])],
        "item_discount_total": r["item_discount_total"],
    }
    return {"debit_note_data": data, "tenant_info": await muat_kop(conn, tid)}
