"""Riwayat FAKTUR PEMBELIAN (tagihan) -- kontrak seragam 22 Okt 2026 (celah WORKSPACE): bentuk SAMA dengan GET /sales-orders/{id}/history
(services/so_riwayat) dan /purchase-orders/{id}/history (services/po_riwayat), supaya petaRiwayatPolish FE dipakai apa adanya.
Ekor bersama so_riwayat._selesaikan (gabung audit_logs, nama aktor satu kueri, zona tenant, terbaru dulu).

Sumber kejadian (kolom beraktor; tak ditebak):
  tagihan     : dibuat (created_by) · diposting (posted_by) · dibatalkan (voided_at; kolom aktor TIDAK ada -> null + alasan)
  bayar keluar: Pembayaran Keluar beralokasi ke tagihan ini -- dibayar (posted_by) · dibatalkan (voided_by + alasan)   [izin send_payment]
  uang muka   : penerapan UM vendor ke tagihan ini (vendor_deposit_applications.created_by)                         [izin vendor_deposit]
  nota debit  : diterapkan (vendor_credit_applications.created_by) · dilepas (reversed_by + alasan, V443)            [izin debit_note]
  audit_logs  : entitas bills (ENTITAS_AUDIT)
Dokumen terkait tanpa izin baca -> `omitted` (FE jujur, bukan diam). None = tagihan tak ada di tenant ini (-> 404)."""
from typing import Awaitable, Callable, Optional

from .so_riwayat import _Kumpul, _rp, _selesaikan

MODUL_TAGIHAN = {"bill": "purchase_invoice", "payment_out": "send_payment", "vendor_deposit": "vendor_deposit", "debit_note": "debit_note"}


async def riwayat_tagihan(conn, tenant_id: str, bill_id, boleh: Callable[[str], Awaitable[bool]], limit: int = 200) -> Optional[dict]:
    b = await conn.fetchrow(
        """SELECT b.id, b.invoice_number, b.amount, b.created_at, b.created_by, b.posted_at, b.posted_by, b.voided_at, b.voided_reason,
                  po.po_number
           FROM bills b LEFT JOIN purchase_orders po ON po.id = b.purchase_order_id AND po.tenant_id = b.tenant_id
           WHERE b.id = $1 AND b.tenant_id = $2""", bill_id, tenant_id)
    if not b:
        return None
    izin = {}
    for modul in MODUL_TAGIHAN.values():
        if modul not in izin:
            izin[modul] = bool(await boleh(modul))
    lihat = {j: izin[m] for j, m in MODUL_TAGIHAN.items()}
    omitted = sorted({m for m in izin if not izin[m]})

    k = _Kumpul()
    n = b["invoice_number"]
    asal = f" dari pesanan {b['po_number']}" if b["po_number"] else ""
    k.tambah(b["created_at"], "TAGIHAN_DIBUAT", f"Faktur pembelian {n} {_rp(b['amount'])} dibuat{asal}", b["created_by"], "bill", b["id"], n)
    k.tambah(b["posted_at"], "TAGIHAN_DIPOSTING", f"Faktur pembelian {n} diposting", b["posted_by"], "bill", b["id"], n)
    alasan = f": {b['voided_reason']}" if b["voided_reason"] else ""
    k.tambah(b["voided_at"], "TAGIHAN_DIBATALKAN", f"Faktur pembelian {n} dibatalkan (void){alasan}", None, "bill", b["id"], n)

    if lihat["payment_out"]:
        for p in await conn.fetch(
            """SELECT bp.id, bp.payment_number, bp.posted_at, bp.posted_by, bp.voided_at, bp.voided_by, bp.void_reason,
                      SUM(bpa.amount_applied) AS jumlah
               FROM bill_payment_allocations bpa JOIN bill_payments_v2 bp ON bp.id = bpa.payment_id AND bp.tenant_id = $1
               WHERE bpa.bill_id = $2
               GROUP BY bp.id, bp.payment_number, bp.posted_at, bp.posted_by, bp.voided_at, bp.voided_by, bp.void_reason""",
                tenant_id, bill_id):
            pn = p["payment_number"]
            k.tambah(p["posted_at"], "PEMBAYARAN_DIBAYAR", f"Pembayaran {pn} {_rp(p['jumlah'])} dialokasikan ke faktur pembelian {n}",
                     p["posted_by"], "payment_out", p["id"], pn)
            al = f": {p['void_reason']}" if p["void_reason"] else ""
            k.tambah(p["voided_at"], "PEMBAYARAN_DIBATALKAN", f"Pembayaran {pn} dibatalkan{al}", p["voided_by"], "payment_out", p["id"], pn)

    if lihat["vendor_deposit"]:
        for a in await conn.fetch(
            """SELECT vda.id AS kunci, vda.amount, vda.created_at, vda.created_by, vd.id, vd.deposit_number
               FROM vendor_deposit_applications vda JOIN vendor_deposits vd ON vd.id = vda.vendor_deposit_id AND vd.tenant_id = $1
               WHERE vda.bill_id = $2""", tenant_id, bill_id):
            dn = a["deposit_number"]
            k.tambah(a["created_at"], "UANG_MUKA_DITERAPKAN", f"Uang muka {dn} {_rp(a['amount'])} diterapkan ke faktur pembelian {n}",
                     a["created_by"], "vendor_deposit", a["id"], dn, kunci=a["kunci"])

    if lihat["debit_note"]:
        for a in await conn.fetch(
            """SELECT vca.id AS kunci, vca.amount_applied, vca.created_at, vca.created_by, vca.reversed_at, vca.reversed_by, vca.reversal_reason,
                      vc.id, vc.credit_number
               FROM vendor_credit_applications vca JOIN vendor_credits vc ON vc.id = vca.vendor_credit_id AND vc.tenant_id = $1
               WHERE vca.tenant_id = $1 AND vca.bill_id = $2""", tenant_id, bill_id):
            cn = a["credit_number"]
            k.tambah(a["created_at"], "NOTA_DEBIT_DITERAPKAN", f"Nota debit {cn} {_rp(a['amount_applied'])} diterapkan ke faktur pembelian {n}",
                     a["created_by"], "debit_note", a["id"], cn, kunci=a["kunci"])
            al = f": {a['reversal_reason']}" if a["reversal_reason"] else ""
            k.tambah(a["reversed_at"], "NOTA_DEBIT_DILEPAS", f"Nota debit {cn} dilepas dari faktur pembelian {n}{al}",
                     a["reversed_by"], "debit_note", a["id"], cn, kunci=a["kunci"])

    keluar, total = await _selesaikan(conn, tenant_id, k, {"bill": [b["id"]]}, lihat, limit)
    return {"bill_id": str(b["id"]), "invoice_number": n, "events": keluar, "total": total, "omitted": omitted}
