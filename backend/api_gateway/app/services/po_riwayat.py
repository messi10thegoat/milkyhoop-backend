"""Riwayat PESANAN PEMBELIAN (22 Okt 2026, permintaan WORKSPACE): bentuk SAMA dengan GET /sales-orders/{id}/history (services/so_riwayat),
supaya petaRiwayatPolish FE dipakai apa adanya. Ekor bersama (_selesaikan: gabung audit_logs, nama aktor satu kueri, zona tenant, terbaru dulu).

Sumber kejadian:
  kolom siklus PO (beraktor): dibuat (created_by), dikirim (sent_by), dibatalkan (cancelled_by + alasan), ditutup (closed_by)
  audit_logs beraktor (catat_riwayat di router PO, kejadian TANPA kolom): PO_UPDATED (medan), PO_RECEIVED (penerimaan), PO_BILLED (tagihan dari PO)
  tagihan bertaut (bills.purchase_order_id) tampil bila izin baca purchase_invoice; tanpa izin -> `omitted`.
None = PO tak ada di tenant ini (-> 404)."""
from typing import Awaitable, Callable, Optional

from .so_riwayat import _Kumpul, _rp, _selesaikan

MODUL_PO = {"purchase_order": "purchase_order", "bill": "purchase_invoice"}


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
    if lihat["bill"]:
        for b in await conn.fetch(
            """SELECT id, invoice_number, grand_total, created_at, created_by FROM bills
               WHERE tenant_id = $1 AND purchase_order_id = $2 ORDER BY created_at""", tenant_id, po_id):
            k.tambah(b["created_at"], "TAGIHAN_DIBUAT", f"Tagihan {b['invoice_number']} {_rp(b['grand_total'])} dibuat dari pesanan {n}",
                     b["created_by"], "bill", b["id"], b["invoice_number"])
    keluar, total = await _selesaikan(conn, tenant_id, k, {"purchase_order": [po["id"]]}, lihat, limit)
    return {"purchase_order_id": str(po["id"]), "po_number": n, "events": keluar, "total": total, "omitted": omitted}
