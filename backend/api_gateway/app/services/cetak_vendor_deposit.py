"""PDF UANG MUKA PEMBELIAN (25 Okt 2026, audit simetri Pembelian WORKSPACE): pemuat konteks /documents/vendor_deposit/{id}/html|pdf.
Kembaran kwitansi uang muka pelanggan (customer_deposits.muat_pdf_kwitansi_uang_muka): templat SAMA (kwitansi.html, A5 melintang), label arah
KELUAR -- "Bukti Pembayaran Uang Muka", dibayarkan kepada vendor, stempel DIBAYAR. Acuan isi: NetSuite Vendor Prepayment (vendor, jumlah,
PO, metode/rekening, memo).

Angka: jumlah = nilai TERSIMPAN dokumen; saldo uang muka = TURUNAN JURNAL (uang_muka_vendor_daftar.fakta_vd, Law 1/16), bukan kolom cache
remaining_amount. Draf/batal: baris saldo tidak dicetak. Tanggal batal = reversed_at jurnal asli (vendor_deposits tak punya voided_at).
Tenant SELALU eksplisit."""
from fastapi import HTTPException

from ..utils.terbilang import terbilang
from . import uang_muka_vendor_daftar as UMD
from .rekap_pesanan import muat_kop

TEKS = {
    "title": "Bukti Pembayaran Uang Muka",
    "subtitle": "Uang Muka Pembelian",
    "party_label": "Dibayarkan Kepada",
    "amount_label": "Jumlah Dibayar",
    "number_label": "No. Uang Muka",
    "purpose_label": "Pesanan Pembelian",
    "source_label": "Referensi",
    "remaining_label": "Saldo Uang Muka",
    "signature_label": "Pembayar,",
    "stamp_label": "DIBAYAR",
}


def label_metode(payment_method) -> str:
    """Sama dengan kwitansi uang muka pelanggan: cash -> Tunai, selain itu Transfer Bank."""
    return "Tunai" if (payment_method or "").lower() == "cash" else "Transfer Bank"


async def muat_pdf_vendor_deposit(conn, ctx, deposit_id) -> dict:
    """-> {receipt_data, tenant_info}. 404 bila uang muka tak ada di tenant ini."""
    tid = ctx["tenant_id"]
    vd = await conn.fetchrow(
        """SELECT vd.*, v.name AS vendor_name, po.po_number,
                  ba.account_name AS bank_account_name, ba.bank_name,
                  je.reversed_at
           FROM vendor_deposits vd
           LEFT JOIN vendors v ON v.id = vd.vendor_id AND v.tenant_id = vd.tenant_id
           LEFT JOIN purchase_orders po ON po.id = vd.purchase_order_id AND po.tenant_id = vd.tenant_id
           LEFT JOIN bank_accounts ba ON ba.id = vd.bank_account_id AND ba.tenant_id = vd.tenant_id
           LEFT JOIN journal_entries je ON je.id = vd.journal_id AND je.tenant_id = vd.tenant_id
           WHERE vd.id = $1 AND vd.tenant_id = $2""", deposit_id, tid)
    if not vd:
        raise HTTPException(status_code=404, detail="Uang muka vendor tidak ditemukan.")
    akun = await UMD.akun_baca(conn, tid)
    sisa = (await UMD.fakta_vd(conn, tid, akun, [vd["id"]])).get(vd["id"], {}).get("sisa")
    receipt_data = {
        **TEKS,
        "status": vd["status"],
        "voided_at": vd["reversed_at"] if vd["status"] == "void" else None,
        "voided_reason": None,
        "receipt_number": vd["deposit_number"],
        "receipt_date": vd["deposit_date"].isoformat() if vd["deposit_date"] else None,
        "payer_name": vd["vendor_name"],
        "amount": vd["amount"],
        "amount_words": terbilang(int(vd["amount"] or 0)),
        "method": label_metode(vd["payment_method"]),
        "bank_name": vd["bank_account_name"] or vd["bank_name"],
        "purpose_ref": vd["po_number"],
        "source_ref": vd["reference"],
        "remaining": sisa,
        "notes": vd["notes"],
    }
    return {"receipt_data": receipt_data, "tenant_info": await muat_kop(conn, tid)}
