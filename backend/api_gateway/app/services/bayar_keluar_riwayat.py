"""Riwayat PEMBAYARAN KELUAR (bill_payments_v2) -- kontrak seragam 22 Okt 2026 (celah WORKSPACE no. 2): bentuk SAMA dengan riwayat SO/PO/
tagihan (services/so_riwayat._selesaikan: gabung audit_logs, nama aktor satu kueri, zona tenant, terbaru dulu).

Sumber kejadian (kolom beraktor; tak ditebak):
  pembayaran : dibuat (created_by) · diposting/dibayar (posted_by) · dibatalkan (voided_by + void_reason)
  alokasi    : per tagihan teralokasi, saat pembayaran diposting (tabel alokasi tanpa aktor/status -> aktor = posted_by)   [izin purchase_invoice]
  audit_logs : entitas bill_payments (ENTITAS_AUDIT)
None = pembayaran tak ada di tenant ini (-> 404)."""
from typing import Awaitable, Callable, Optional

from .so_riwayat import _Kumpul, _rp, _selesaikan

MODUL_BAYAR = {"payment_out": "send_payment", "bill": "purchase_invoice"}


async def riwayat_bayar_keluar(conn, tenant_id: str, payment_id, boleh: Callable[[str], Awaitable[bool]], limit: int = 200) -> Optional[dict]:
    p = await conn.fetchrow(
        """SELECT id, payment_number, total_amount, created_at, created_by, posted_at, posted_by, voided_at, voided_by, void_reason
           FROM bill_payments_v2 WHERE id = $1 AND tenant_id = $2""", payment_id, tenant_id)
    if not p:
        return None
    izin = {}
    for modul in MODUL_BAYAR.values():
        if modul not in izin:
            izin[modul] = bool(await boleh(modul))
    lihat = {j: izin[m] for j, m in MODUL_BAYAR.items()}
    omitted = sorted({m for m in izin if not izin[m]})

    k = _Kumpul()
    n = p["payment_number"]
    k.tambah(p["created_at"], "PEMBAYARAN_DIBUAT", f"Pembayaran {n} {_rp(p['total_amount'])} dibuat", p["created_by"], "payment_out", p["id"], n)
    k.tambah(p["posted_at"], "PEMBAYARAN_DIBAYAR", f"Pembayaran {n} diposting", p["posted_by"], "payment_out", p["id"], n)
    alasan = f": {p['void_reason']}" if p["void_reason"] else ""
    k.tambah(p["voided_at"], "PEMBAYARAN_DIBATALKAN", f"Pembayaran {n} dibatalkan{alasan}", p["voided_by"], "payment_out", p["id"], n)
    if lihat["bill"] and p["posted_at"]:
        for a in await conn.fetch(
            """SELECT b.id, b.invoice_number, SUM(a.amount_applied) AS jumlah
               FROM bill_payment_allocations a JOIN bills b ON b.id = a.bill_id AND b.tenant_id = $1
               WHERE a.payment_id = $2 GROUP BY b.id, b.invoice_number ORDER BY b.invoice_number""", tenant_id, payment_id):
            k.tambah(p["posted_at"], "ALOKASI_TAGIHAN", f"{_rp(a['jumlah'])} dialokasikan ke faktur pembelian {a['invoice_number']}",
                     p["posted_by"], "bill", a["id"], a["invoice_number"], kunci=a["id"])
    keluar, total = await _selesaikan(conn, tenant_id, k, {"payment_out": [p["id"]]}, lihat, limit)
    return {"payment_id": str(p["id"]), "payment_number": n, "events": keluar, "total": total, "omitted": omitted}
