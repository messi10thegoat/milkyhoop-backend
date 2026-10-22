"""Riwayat UANG MUKA PEMBELIAN (22 Okt 2026, CW penuh): bentuk SAMA dengan GET /sales-orders/{id}/history (services/so_riwayat) supaya
petaRiwayatServer FE dipakai apa adanya. Ekor bersama (_selesaikan: gabung audit_logs, nama aktor satu kueri, zona tenant, terbaru dulu).

Sumber kejadian (beraktor):
  dibuat (created_at/by) · diposting (jurnal VENDOR_DEPOSIT asli: created_at/by) · diterapkan ke tagihan (vendor_deposit_applications
  created_at/by) · dilepas (reversed_at/by, V437) · dikembalikan (vendor_deposit_refunds created_at/by) · dibatalkan (audit VD_VOIDED +
  alasan, ditulis /void). Tagihan bertaut tampil bila izin baca purchase_invoice; tanpa izin -> `omitted`.
None = uang muka tak ada di tenant ini (-> 404)."""
from typing import Awaitable, Callable, Optional

from .so_riwayat import _Kumpul, _rp, _selesaikan

MODUL = {"vendor_deposit": "vendor_deposit", "bill": "purchase_invoice"}


async def riwayat_um_vendor(conn, tenant_id: str, deposit_id, boleh: Callable[[str], Awaitable[bool]], limit: int = 200) -> Optional[dict]:
    vd = await conn.fetchrow(
        """SELECT id, deposit_number, amount, status, created_at, created_by, journal_id FROM vendor_deposits
           WHERE id = $1 AND tenant_id = $2""", deposit_id, tenant_id)
    if not vd:
        return None
    izin = {}
    for modul in set(MODUL.values()):
        izin[modul] = bool(await boleh(modul))
    lihat = {j: izin[m] for j, m in MODUL.items()}
    omitted = sorted(m for m in izin if not izin[m])

    k = _Kumpul()
    n = vd["deposit_number"]
    k.tambah(vd["created_at"], "UM_DIBUAT", f"Uang muka pembelian {n} {_rp(vd['amount'])} dibuat", vd["created_by"], "vendor_deposit", vd["id"], n)
    if vd["journal_id"]:
        j = await conn.fetchrow("SELECT created_at, created_by FROM journal_entries WHERE id = $1 AND tenant_id = $2", vd["journal_id"], tenant_id)
        if j:
            k.tambah(j["created_at"], "UM_DIPOSTING", f"Uang muka pembelian {n} diposting", j["created_by"], "vendor_deposit", vd["id"], n)
    for a in await conn.fetch(
        """SELECT a.id, a.amount, a.created_at, a.created_by, a.reversed_at, a.reversed_by, a.bill_id, b.invoice_number
           FROM vendor_deposit_applications a LEFT JOIN bills b ON b.id = a.bill_id AND b.tenant_id = $2
           WHERE a.vendor_deposit_id = $1 ORDER BY a.created_at""", vd["id"], tenant_id):
        nomor = a["invoice_number"] if lihat["bill"] else None
        ke = f" ke tagihan {nomor}" if nomor else ""
        dok = ("bill", a["bill_id"], nomor) if lihat["bill"] and a["bill_id"] else ("vendor_deposit", vd["id"], n)
        k.tambah(a["created_at"], "UM_DITERAPKAN", f"Uang muka {n} {_rp(a['amount'])} diterapkan{ke}", a["created_by"], *dok, kunci=a["id"])
        k.tambah(a["reversed_at"], "UM_DILEPAS", f"Penerapan uang muka {n} {_rp(a['amount'])}{ke} dilepas", a["reversed_by"], *dok, kunci=a["id"])
    for r in await conn.fetch(
        "SELECT id, amount, created_at, created_by FROM vendor_deposit_refunds WHERE vendor_deposit_id = $1 ORDER BY created_at", vd["id"]):
        k.tambah(r["created_at"], "UM_DIKEMBALIKAN", f"Uang muka {n} {_rp(r['amount'])} dikembalikan vendor", r["created_by"],
                 "vendor_deposit", vd["id"], n, kunci=r["id"])
    keluar, total = await _selesaikan(conn, tenant_id, k, {"vendor_deposit": [vd["id"]]}, lihat, limit)
    return {"deposit_id": str(vd["id"]), "deposit_number": n, "events": keluar, "total": total, "omitted": omitted}
