"""Riwayat NOTA DEBIT (U3c, 23 Okt 2026) -- bentuk SAMA dengan riwayat tagihan/PO/SO (services/tagihan_riwayat, so_riwayat), supaya
petaRiwayatPolish FE dipakai apa adanya. Ekor bersama so_riwayat._selesaikan (gabung audit_logs, nama aktor, zona tenant, terbaru dulu).

Sumber kejadian (kolom beraktor; tak ditebak):
  nota debit : dibuat (created_by) · diposting (posted_by) · dibatalkan (voided_by + alasan)
  penerapan  : diterapkan ke tagihan (vendor_credit_applications.created_by) · dilepas (reversed_by + alasan, V443)
  refund     : uang dikembalikan vendor (vendor_credit_refunds.created_by)
  audit_logs : entitas vendor_credits (ENTITAS_AUDIT -> debit_note)
Kejadian penerapan merujuk tagihan (jenis 'bill', izin purchase_invoice); tanpa izin baca tagihan -> nomor tagihan tak disebut, modul
masuk `omitted`. None = nota debit tak ada di tenant ini (-> 404)."""
from typing import Awaitable, Callable, Optional

from .so_riwayat import _Kumpul, _rp, _selesaikan

MODUL_ND = {"debit_note": "debit_note", "bill": "purchase_invoice"}


async def riwayat_nota_debit(conn, tenant_id: str, vc_id, boleh: Callable[[str], Awaitable[bool]], limit: int = 200) -> Optional[dict]:
    v = await conn.fetchrow(
        """SELECT id, credit_number, total_amount, created_at, created_by, posted_at, posted_by, voided_at, voided_by, voided_reason
           FROM vendor_credits WHERE id = $1 AND tenant_id = $2""", vc_id, tenant_id)
    if not v:
        return None
    izin = {}
    for modul in MODUL_ND.values():
        if modul not in izin:
            izin[modul] = bool(await boleh(modul))
    lihat = {j: izin[m] for j, m in MODUL_ND.items()}
    omitted = sorted({m for m in izin if not izin[m]})

    k = _Kumpul()
    n = v["credit_number"]
    k.tambah(v["created_at"], "NOTA_DEBIT_DIBUAT", f"Nota debit {n} {_rp(v['total_amount'])} dibuat", v["created_by"], "debit_note", v["id"], n)
    k.tambah(v["posted_at"], "NOTA_DEBIT_DIPOSTING", f"Nota debit {n} diposting", v["posted_by"], "debit_note", v["id"], n)
    alasan = f": {v['voided_reason']}" if v["voided_reason"] else ""
    k.tambah(v["voided_at"], "NOTA_DEBIT_DIBATALKAN", f"Nota debit {n} dibatalkan (void){alasan}", v["voided_by"], "debit_note", v["id"], n)

    for a in await conn.fetch(
        """SELECT vca.id AS kunci, vca.amount_applied, vca.created_at, vca.created_by, vca.reversed_at, vca.reversed_by, vca.reversal_reason,
                  b.id AS bill_id, b.invoice_number
           FROM vendor_credit_applications vca LEFT JOIN bills b ON b.id = vca.bill_id AND b.tenant_id = $1
           WHERE vca.tenant_id = $1 AND vca.vendor_credit_id = $2""", tenant_id, vc_id):
        ke = f" ke faktur pembelian {a['invoice_number']}" if lihat["bill"] and a["invoice_number"] else ""
        dari = f" dari faktur pembelian {a['invoice_number']}" if lihat["bill"] and a["invoice_number"] else ""
        k.tambah(a["created_at"], "NOTA_DEBIT_DITERAPKAN", f"Nota debit {n} {_rp(a['amount_applied'])} diterapkan{ke}",
                 a["created_by"], "debit_note", v["id"], n, kunci=a["kunci"])
        al = f": {a['reversal_reason']}" if a["reversal_reason"] else ""
        k.tambah(a["reversed_at"], "NOTA_DEBIT_DILEPAS", f"Nota debit {n} dilepas{dari}{al}",
                 a["reversed_by"], "debit_note", v["id"], n, kunci=a["kunci"])

    for r in await conn.fetch(
        """SELECT id AS kunci, amount, created_at, created_by FROM vendor_credit_refunds
           WHERE tenant_id = $1 AND vendor_credit_id = $2""", tenant_id, vc_id):
        k.tambah(r["created_at"], "NOTA_DEBIT_DIKEMBALIKAN", f"Vendor mengembalikan {_rp(r['amount'])} atas nota debit {n}",
                 r["created_by"], "debit_note", v["id"], n, kunci=r["kunci"])

    keluar, total = await _selesaikan(conn, tenant_id, k, {"debit_note": [v["id"]]}, lihat, limit)
    return {"vendor_credit_id": str(v["id"]), "vendor_credit_number": n, "events": keluar, "total": total, "omitted": omitted}
