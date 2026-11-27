"""Bukti Kas Keluar untuk UANG KELUAR MANUAL (Kas&Bank G30, 27 Nov 2026): GET /api/bank-transactions/{id}/pdf.

Simetri cetak_payment_out (templat kwitansi.html SAMA, label keluar). Sumber baris = KD.SQL_MANUAL (predikat sah SAMA dengan daftar/void);
jumlah = |net jurnal sisi rekening| (angka TERSIMPAN, bukan hitung ulang). Uang MASUK -> 409 (bukan kas keluar). Baca saja; tanpa migrasi."""
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException

from . import kasbank_daftar as KD
from .cetak_payment_out import LABEL as _LABEL_BAYAR
from .rekap_pesanan import muat_kop

PESAN_TAK_ADA = "Mutasi manual tidak ditemukan."
PESAN_BUKAN_KELUAR = "Bukti Kas Keluar hanya untuk Uang Keluar."

LABEL = {
    **_LABEL_BAYAR,
    "subtitle": "Bukti pengeluaran kas/bank",
    "party_label": "Dibayarkan kepada",
    "amount_label": "Jumlah Dikeluarkan",
    "number_label": "No. Transaksi",
    "signature_label": "Dikeluarkan oleh,",
}


async def muat_pdf_kas_keluar(conn, ctx: dict, transaction_id) -> dict:
    """-> {receipt_data, tenant_info}. 404 bila tak ada di tenant ini; 409 bila mutasi itu uang masuk."""
    tid = ctx["tenant_id"]
    try:
        uid = transaction_id if isinstance(transaction_id, UUID) else UUID(str(transaction_id))
    except ValueError:
        raise HTTPException(status_code=404, detail=PESAN_TAK_ADA)
    r = await conn.fetchrow(KD.SQL_MANUAL + " AND bt.id = $2", tid, uid)
    if not r:
        raise HTTPException(status_code=404, detail=PESAN_TAK_ADA)
    net = Decimal(str(r["net"] or 0))
    if net >= 0:
        raise HTTPException(status_code=409, detail=PESAN_BUKAN_KELUAR)
    batal = bool(r["batal"])
    data = {
        "status": "VOIDED" if batal else "POSTED",
        "voided_at": r["voided_at"],
        "void_reason": r["void_reason"],
        "receipt_number": r["nomor"],
        "receipt_date": r["tgl"],
        "payer_name": r["pihak"],
        "amount": -net,
        "method": None,
        "bank_name": r["account_name"],
        "purpose_label": "Akun lawan",
        "purpose_ref": " · ".join(x for x in (r["lawan_kode"], r["lawan_nama"]) if x) or None,
        "remaining": None,
        "notes": r["description"],
        **LABEL,
    }
    return {"receipt_data": data, "tenant_info": await muat_kop(conn, tid)}
