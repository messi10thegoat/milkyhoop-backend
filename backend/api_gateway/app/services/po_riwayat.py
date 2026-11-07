"""Riwayat PESANAN PEMBELIAN (22 Okt 2026, permintaan WORKSPACE): bentuk SAMA dengan GET /sales-orders/{id}/history (services/so_riwayat),
supaya petaRiwayatPolish FE dipakai apa adanya. Ekor bersama (_selesaikan: gabung audit_logs, nama aktor satu kueri, zona tenant, terbaru dulu).

Sumber kejadian:
  kolom siklus PO (beraktor): dibuat (created_by), dikirim (sent_by), dibatalkan (cancelled_by + alasan), ditutup (closed_by)
  audit_logs beraktor (catat_riwayat di router PO, kejadian TANPA kolom): PO_UPDATED (medan), PO_RECEIVED (penerimaan)
  tagihan bertaut (bills.purchase_order_id ATAU baris bill_items.purchase_order_item_id -- data BUG-036) tampil bila izin baca
  purchase_invoice; kejadiannya = tagihan_riwayat.bagian_tagihan (dibuat/diposting/dibatalkan + bayar keluar/UM vendor/nota debit, BUG-112 d,
  SATU sumber dengan riwayat faktur pembelian). Tanpa izin -> `omitted`.
None = PO tak ada di tenant ini (-> 404)."""
from typing import Awaitable, Callable, Optional

from .so_riwayat import _Kumpul, _rp, _selesaikan
from .tagihan_riwayat import MODUL_TAGIHAN, SQL_TAGIHAN, bagian_tagihan

MODUL_PO = {"purchase_order": "purchase_order", **MODUL_TAGIHAN}


async def tagihan_po(conn, tenant_id: str, po_id) -> list:
    """Tagihan bertaut PO: kepala bills.purchase_order_id ATAU baris bill_items.purchase_order_item_id (padanan po_daftar.po_terkait_bill)."""
    return await conn.fetch(
        f"""{SQL_TAGIHAN}
            WHERE b.tenant_id = $1 AND (b.purchase_order_id = $2 OR b.id IN (
                SELECT bi.bill_id FROM bill_items bi JOIN purchase_order_items i ON i.id = bi.purchase_order_item_id WHERE i.po_id = $2))
            ORDER BY b.created_at""", tenant_id, po_id)


async def riwayat_po(conn, tenant_id: str, po_id, boleh: Callable[[str], Awaitable[bool]], limit: int = 200) -> Optional[dict]:
    po = await conn.fetchrow(
        """SELECT id, po_number, total_amount, status, created_at, created_by, sent_at, sent_by,
                  cancelled_at, cancelled_by, cancelled_reason, closed_at, closed_by
           FROM purchase_orders WHERE id = $1 AND tenant_id = $2""", po_id, tenant_id)
    if not po:
        return None
    izin = {}
    for jenis, modul in MODUL_PO.items():
        if modul not in izin:
            izin[modul] = bool(await boleh(modul))
    lihat = {j: izin[m] for j, m in MODUL_PO.items()}
    omitted = sorted({m for m in izin if not izin[m]})

    k = _Kumpul()
    n = po["po_number"]
    k.tambah(po["created_at"], "PO_DIBUAT", f"Pesanan pembelian {n} {_rp(po['total_amount'])} dibuat", po["created_by"], "purchase_order", po["id"], n)
    k.tambah(po["sent_at"], "PO_DIKIRIM", f"Pesanan pembelian {n} ditandai terkirim ke vendor", po["sent_by"], "purchase_order", po["id"], n)
    alasan = f": {po['cancelled_reason']}" if po["cancelled_reason"] else ""
    k.tambah(po["cancelled_at"], "PO_DIBATALKAN", f"Pesanan pembelian {n} dibatalkan{alasan}", po["cancelled_by"], "purchase_order", po["id"], n)
    k.tambah(po["closed_at"], "PO_DITUTUP", f"Pesanan pembelian {n} ditutup", po["closed_by"], "purchase_order", po["id"], n)
    bills = await tagihan_po(conn, tenant_id, po["id"]) if lihat["bill"] else []
    await bagian_tagihan(conn, tenant_id, k, bills, lihat)
    keluar, total = await _selesaikan(conn, tenant_id, k, {"purchase_order": [po["id"]], "bill": [b["id"] for b in bills]}, lihat, limit)
    return {"purchase_order_id": str(po["id"]), "po_number": n, "events": keluar, "total": total, "omitted": omitted}
