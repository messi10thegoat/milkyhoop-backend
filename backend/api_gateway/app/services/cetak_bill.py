"""Cetak Faktur Pembelian (P2P cetak, 25 Okt 2026): pemuat konteks untuk /api/documents/bill/{id}/(html|pdf).

SATU sumber dengan rute lama /api/bills/{id}/pdf: data = BillsService.get_bill_v2 (di koneksi pemanggil lewat
get_bill_v2_conn, tanpa acquire bersarang -- Law 32), kop = rekap_pesanan.muat_kop. Terbayar/sisa = turunan jurnal
(compute_ap_rincian, Law 1/16) persis seperti layar detail; angka dokumen = kolom tersimpan, tidak dihitung ulang."""
from uuid import UUID

from fastapi import HTTPException

from .bills_service import PESAN_TAGIHAN_TAK_ADA, BillsService
from .rekap_pesanan import muat_kop


async def muat_pdf_bill(conn, ctx: dict, bill_id) -> dict:
    """-> {bill}. bill["tenant"] = kop (bentuk yang dibaca bill_invoice.html). 404 bila tak ada di tenant ini."""
    try:
        uid = bill_id if isinstance(bill_id, UUID) else UUID(str(bill_id))
    except ValueError:
        raise HTTPException(status_code=404, detail=PESAN_TAGIHAN_TAK_ADA)
    bill = await BillsService(None).get_bill_v2_conn(conn, ctx["tenant_id"], uid)
    if not bill:
        raise HTTPException(status_code=404, detail=PESAN_TAGIHAN_TAK_ADA)
    bill["tenant"] = await muat_kop(conn, ctx["tenant_id"])
    return {"bill": bill}
