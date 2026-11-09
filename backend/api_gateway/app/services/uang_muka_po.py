"""Uang muka pembelian per PESANAN PEMBELIAN -- SATU sumber plafon (UMP-PO U-A, 2 Nov 2026; cermin services/uang_muka_pesanan,
pola NetSuite Vendor Prepayment terhadap PO). Turunan JURNAL (Law 1/16), Decimal (Law 9/25), akun lewat PERAN (Law 27).

Per uang muka (bukan void) bertaut purchase_order_id, dari baris jurnal EFEKTIF pada akun peran AP_PREPAID yang source_id = uang muka itu:
  terima = debit−kredit jurnal VENDOR_DEPOSIT (posting; void membalik lewat is_effective_journal)
  lain   = kredit−debit jurnal selain VENDOR_DEPOSIT / DEPOSIT_APPLICATION (terukur 2 Nov: DEPOSIT_REFUND) = DIKEMBALIKAN
  neto   = terima − lain   (uang yang MENGIKAT pesanan; yang DITERAPKAN tetap milik PO lewat FPB -- tak melepas plafon, == DP)
Draf = belum berjurnal (terima 0); nominalnya ikut MENAHAN plafon (janji, dicek ulang saat posting).
DEPOSIT_APPLICATION / DEPOSIT_REFUND dipakai BERSAMA dengan DP Penjualan -> saringan akun AP_PREPAID + source_id WAJIB.

Pagar: sisa = total PO − Σ(neto + draf) uang muka LAIN di PO itu; jumlah > sisa + 0,005 -> blok UMP_MELEBIHI_SISA_PO (400).
Di jalur tulis PO dikunci FOR UPDATE dulu (Law 13: men-serialkan buat/ubah/posting UMP pada PO yang sama; anti-duplikat tetap
idempotensi rute, Law 14). Pratinjau = baca tanpa kunci (transaksi READ ONLY).
"""
from decimal import Decimal
from typing import Optional

from . import teks_galat as tg
from .role_resolver import AccountRole, AccountRoleUnmappedError, resolve_account_id_by_role

NOL = Decimal("0")
TOLERANSI = Decimal("0.005")
KODE_LEBIH = "UMP_MELEBIHI_SISA_PO"

SQL_PER_UANG_MUKA = """
    SELECT vd.id, vd.status, vd.amount,
           COALESCE(SUM(jl.debit - jl.credit) FILTER (WHERE je.source_type = 'VENDOR_DEPOSIT'), 0) AS terima,
           COALESCE(SUM(jl.credit - jl.debit) FILTER (
               WHERE je.source_type NOT IN ('VENDOR_DEPOSIT', 'DEPOSIT_APPLICATION')), 0) AS lain
    FROM vendor_deposits vd
    LEFT JOIN journal_entries je ON je.tenant_id = vd.tenant_id AND je.source_id = vd.id AND is_effective_journal(je.id)
    LEFT JOIN journal_lines jl ON jl.journal_id = je.id AND jl.account_id = $3
    WHERE vd.tenant_id = $1 AND vd.purchase_order_id = $2 AND vd.status <> 'void'
      AND ($4::uuid IS NULL OR vd.id <> $4::uuid)
    GROUP BY vd.id, vd.status, vd.amount
"""


def _d(v) -> Decimal:
    return Decimal(str(v)) if v is not None else NOL


async def _akun_um(conn, tenant_id: str):
    """Akun peran AP_PREPAID (Law 27). Belum dipetakan -> None: belum ada jurnal UMP yang mungkin, neto = 0, draf tetap menahan."""
    try:
        return await resolve_account_id_by_role(conn, tenant_id, AccountRole.AP_PREPAID)
    except AccountRoleUnmappedError:
        return None


async def rincian_po(conn, tenant_id: str, po_id, exclude_id=None, kunci: bool = False) -> Optional[dict]:
    """-> {po_number, total, neto, draf, dibayar_dimuka, sisa} atau None bila PO tak ada di tenant ini."""
    po = await conn.fetchrow(
        "SELECT id, po_number, total_amount FROM purchase_orders WHERE id = $1 AND tenant_id = $2" + (" FOR UPDATE" if kunci else ""),
        po_id, tenant_id)
    if not po:
        return None
    akun = await _akun_um(conn, tenant_id)
    neto, draf = NOL, NOL
    for r in await conn.fetch(SQL_PER_UANG_MUKA, tenant_id, po_id, akun, None if exclude_id is None else str(exclude_id)):
        if r["status"] == "draft":
            draf += _d(r["amount"])
        else:
            neto += _d(r["terima"]) - _d(r["lain"])
    total = _d(po["total_amount"])
    dibayar = neto + draf
    return {"po_number": po["po_number"], "total": total, "neto": neto, "draf": draf, "dibayar_dimuka": dibayar, "sisa": total - dibayar}


def rincian_lebih(r: dict) -> str:
    """Rincian plafon (pesan_lebih penulis DAN aksi_tersedia `poum` detail PO bila sisa habis)."""
    return (f"Nilai pesanan {tg.rp(r['total'])}, sudah dibayar di muka {tg.rp(r['dibayar_dimuka'])}, "
            f"sisa yang masih dapat dibayar di muka {tg.rp(max(r['sisa'], NOL))}.")


def pesan_lebih(jumlah, r: dict) -> str:
    return f"Jumlah {tg.rp(jumlah)} melebihi sisa yang masih dapat dibayar di muka. " + rincian_lebih(r)


async def periksa_plafon(conn, tenant_id: str, po_id, jumlah, exclude_id, kunci: bool) -> Optional[dict]:
    """-> blok {code, message, status} bila jumlah melebihi sisa PO; None bila muat atau tanpa PO."""
    if not po_id:
        return None
    r = await rincian_po(conn, tenant_id, po_id, exclude_id=exclude_id, kunci=kunci)
    if r is None:
        return None  # PO tak ada: penjaga PO_TIDAK_DITEMUKAN pemanggil yang menolak
    if _d(jumlah) > r["sisa"] + TOLERANSI:
        return {"code": KODE_LEBIH, "message": pesan_lebih(jumlah, r), "status": 400}
    return None
