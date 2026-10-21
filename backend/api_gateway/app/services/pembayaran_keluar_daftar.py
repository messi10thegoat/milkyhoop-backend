"""PEMBELIAN-SERAGAM unit (2) / Pembayaran Keluar (22 Okt 2026, putusan MASTER): data daftar/detail dihitung SERVER dari WRAPPER
bill_payments_v2 (bukan dari jurnal ber-debit AP -- dulu jurnal MANUAL/VENDOR_OPENING_BALANCE ikut tampil sebagai "pembayaran" hantu dan
pembayaran voided/draf tak pernah tampil). Kembaran services/penerimaan_daftar (Penjualan).

  dialokasikan       = Σ bill_payment_allocations.amount_applied pembayaran ini; hanya wrapper POSTED (tabel alokasi tak berstatus:
                       "aktif" = pembayaran posted), lainnya 0.
  bruto              = total_amount + discount_amount (rumus tulis: unapplied = total + diskon − Σ alokasi, routers/bill_payments create).
  belum_dialokasikan = GREATEST(bruto − dialokasikan, 0) -- TURUNAN alokasi, BUKAN cache wrapper (`allocated_amount`/`unapplied_amount`
                       = 0 pada 24/26 pembayaran posted karena bills_service.record_payment tak mengisinya; Law 16).
  aksi               = 'belum_dialokasikan' bila belum_dialokasikan > 0
  alokasi_keadaan    = belum | sebagian | penuh | tanpa (draf/voided) ; alokasi_teks = teks SAMA dgn Penerimaan
  dengan_turunan     = posted: allocated_amount := dialokasikan, unapplied_amount := belum_dialokasikan (kolom cache DB tetap ada).
"""
from decimal import Decimal
from typing import Optional

NOL = Decimal("0")
TOLERANSI = Decimal("0.005")
AKSI = ("belum_dialokasikan",)
TEKS = {"belum": "Belum dialokasikan", "sebagian": "Sebagian dialokasikan", "penuh": "Teralokasi penuh", "tanpa": "—"}


def sql_dialokasi(bp: str = "bp") -> str:
    """Σ alokasi pembayaran `bp` (alias bill_payments_v2) -- hanya posted (tabel alokasi tak berstatus)."""
    return (f"(CASE WHEN {bp}.status = 'posted' THEN COALESCE((SELECT SUM(a.amount_applied) FROM bill_payment_allocations a "
            f"WHERE a.payment_id = {bp}.id), 0) ELSE 0 END)")


def sql_bruto(bp: str = "bp") -> str:
    return f"({bp}.total_amount + COALESCE({bp}.discount_amount, 0))"


def sql_belum(bp: str = "bp") -> str:
    return f"(CASE WHEN {bp}.status = 'posted' THEN GREATEST({sql_bruto(bp)} - {sql_dialokasi(bp)}, 0) ELSE 0 END)"


def alokasi_fakta(status: Optional[str], bruto, dialokasi) -> dict:
    """Pure: bruto = total_amount + discount_amount, dialokasi = Σ alokasi (posted)."""
    if status != "posted" or bruto is None:
        belum, kd, ke_tagihan = NOL, "tanpa", NOL
    else:
        ke_tagihan = Decimal(str(dialokasi or 0))
        belum = max(Decimal(str(bruto)) - ke_tagihan, NOL)
        kd = "penuh" if belum <= TOLERANSI else ("belum" if belum >= Decimal(str(bruto)) - TOLERANSI else "sebagian")
    return {"dialokasikan": float(ke_tagihan), "belum_dialokasikan": float(belum), "aksi": "belum_dialokasikan" if belum > TOLERANSI else None,
            "alokasi_keadaan": kd, "alokasi_teks": TEKS[kd]}


def dengan_turunan(fakta: dict) -> dict:
    """Posted: allocated_amount/unapplied_amount = turunan (fungsi SAMA dgn alokasi_fakta); draf/voided: kunci cache tak ditimpa."""
    if fakta["alokasi_keadaan"] == "tanpa":
        return dict(fakta)
    return {**fakta, "allocated_amount": fakta["dialokasikan"], "unapplied_amount": fakta["belum_dialokasikan"]}


async def bruto_dialokasi(conn, tid: str, ids: list) -> dict:
    """{payment_id: (bruto, dialokasi)} -- ungkapan SAMA dgn sql_belum."""
    if not ids:
        return {}
    return {r["id"]: (r["bruto"], r["dialokasi"]) for r in await conn.fetch(
        f"SELECT bp.id, {sql_bruto('bp')} AS bruto, {sql_dialokasi('bp')} AS dialokasi FROM bill_payments_v2 bp "
        "WHERE bp.tenant_id = $1 AND bp.id = ANY($2::uuid[])", tid, list(ids))}
