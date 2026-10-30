"""Cetak Bukti Kas Keluar (dulu "Bukti Pembayaran") ke pemasok (P2P cetak, 25 Okt 2026): pemuat konteks /api/documents/payment_out/{id}/(html|pdf).

Simetri kwitansi penerimaan (receive_payments.muat_pdf_kwitansi_penerimaan): templat kwitansi.html yang SAMA dengan
label keluar. Jumlah = total_amount tersimpan (kas keluar yang dijurnal); faktur = alokasi pembayaran ini; sisa hanya
bila SATU faktur (snapshot remaining_after saat pembayaran, sama dengan kwitansi penerimaan)."""
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException

from ..utils.metode_pembayaran import label_metode
from ..utils.terbilang import terbilang
from .rekap_pesanan import muat_kop

PESAN_TAK_ADA = "Pembayaran tidak ditemukan."

LABEL = {
    "title": "Bukti Kas Keluar",
    "subtitle": "Pemberitahuan pembayaran ke pemasok",
    "party_label": "Dibayarkan Kepada",
    "amount_label": "Jumlah Dibayar",
    "number_label": "No. Pembayaran",
    "remaining_label": "Sisa Faktur",
    "signature_label": "Dibayar oleh,",
    "hide_stamp": True,  # BKK (30 Okt 2026): pola remittance advice / payment voucher -- tanpa stempel; batal tetap DIBATALKAN
}


async def muat_pdf_payment_out(conn, ctx: dict, payment_id) -> dict:
    """-> {receipt_data, tenant_info} (kunci yang dibaca kwitansi.html). 404 bila tak ada di tenant ini."""
    tid = ctx["tenant_id"]
    try:
        uid = payment_id if isinstance(payment_id, UUID) else UUID(str(payment_id))
    except ValueError:
        raise HTTPException(status_code=404, detail=PESAN_TAK_ADA)
    pay = await conn.fetchrow("SELECT * FROM bill_payments_v2 WHERE id = $1 AND tenant_id = $2", uid, tid)
    if not pay:
        raise HTTPException(status_code=404, detail=PESAN_TAK_ADA)
    bank_name = pay["bank_account_name"]
    if not bank_name and pay["bank_account_id"]:
        bank = await conn.fetchrow(
            """SELECT account_name, bank_name FROM bank_accounts
               WHERE tenant_id = $2 AND (id = $1 OR coa_id = $1) ORDER BY (id = $1) DESC LIMIT 1""",
            pay["bank_account_id"], tid)
        if bank:
            bank_name = bank["account_name"] or bank["bank_name"]
    alokasi = await conn.fetch(
        """SELECT b.invoice_number, bpa.remaining_after
           FROM bill_payment_allocations bpa
           JOIN bills b ON b.id = bpa.bill_id AND b.tenant_id = $2
           WHERE bpa.payment_id = $1 ORDER BY bpa.created_at""", uid, tid)
    nomor = [a["invoice_number"] for a in alokasi if a["invoice_number"]]
    sisa = Decimal(alokasi[0]["remaining_after"]) if len(alokasi) == 1 and alokasi[0]["remaining_after"] is not None else None
    jumlah = Decimal(str(pay["total_amount"] or 0))
    data = {
        "status": pay["status"],
        "voided_at": pay["voided_at"],
        "void_reason": pay["void_reason"],
        "receipt_number": pay["payment_number"] or pay["reference_number"],
        "receipt_date": pay["payment_date"],
        "payer_name": pay["vendor_name"],
        "amount": jumlah,
        "amount_words": terbilang(jumlah),
        "method": label_metode(pay["payment_method"]),
        "bank_name": bank_name,
        "purpose_label": "Faktur Pembelian",
        "purpose_ref": ", ".join(nomor) or None,
        "remaining": sisa,
        "notes": pay["notes"],
        **LABEL,
    }
    return {"receipt_data": data, "tenant_info": await muat_kop(conn, tid)}
