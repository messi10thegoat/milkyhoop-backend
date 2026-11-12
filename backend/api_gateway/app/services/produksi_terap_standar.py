"""Penerapan upah + overhead ke WIP (satu helper untuk POST /{id}/labor dan POST /{id}/report-output; BUG-183, putusan pemilik P1 'standar otomatis', 12 Nov 2026).

Jurnal SAMA dengan /labor sejak V173: PRODUCTION_LABOR Dr WIP_GENERIC / Cr MFG_LABOR_APPLIED (2-10430) dan PRODUCTION_OVERHEAD Dr WIP_GENERIC / Cr MFG_OVERHEAD_APPLIED (2-10440),
nomor seri JV-LB / JV-OH, DRAFT -> baris -> POSTED. Tanpa akun baru; month-end-reconcile menutup selisih standar-vs-aktual ke 5-90200.
kekurangan_standar: STANDAR kumulatif (planned per unit x seluruh output baik s/d kini) dikurangi yang SUDAH tercatat (aktual /labor + standar output sebelumnya) -> hanya kekurangan positif:
aktual menang, tak dobel, output kedua tak mengulang."""
import uuid as _uuid
from decimal import ROUND_HALF_UP, Decimal

from .role_resolver import AccountRole, resolve_account_id_by_role

_JENIS = {
    "labor": ("PRODUCTION_LABOR", AccountRole.MFG_LABOR_APPLIED, "labor", "MFG_LABOR_APPLIED"),
    "overhead": ("PRODUCTION_OVERHEAD", AccountRole.MFG_OVERHEAD_APPLIED, "overhead", "MFG_OVERHEAD_APPLIED"),
}
# Awalan seri jurnal = LITERAL di SQL (penjaga test_nomor_jurnal_satu_ruang: tak boleh awalan non-literal/awalan dokumen).
_SQL_NOMOR = {
    "labor": "SELECT get_next_journal_number($1, 'JV-LB', $2)",
    "overhead": "SELECT get_next_journal_number($1, 'JV-OH', $2)",
}


def kekurangan_standar(planned_biaya, planned_qty, sudah_baik, baik, aktual) -> int:
    """Rupiah standar yang MASIH harus diterapkan untuk output ini (>= 0)."""
    if not planned_qty or planned_qty <= 0 or not planned_biaya:
        return 0
    target = (Decimal(planned_biaya) * (Decimal(sudah_baik) + Decimal(baik)) / Decimal(planned_qty)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return max(0, int(target - Decimal(aktual)))


async def terapkan_ke_wip(conn, *, tenant_id, user_id, order_id, order_number, jenis, jumlah, tanggal, deskripsi=None) -> None:
    """Terbitkan SATU jurnal terap (labor | overhead) ke WIP; jumlah <= 0 = tak menerbitkan apa pun."""
    if not jumlah or jumlah <= 0:
        return
    source_type, peran, kata, memo_akun = _JENIS[jenis]
    wip_id = await resolve_account_id_by_role(conn, tenant_id, AccountRole.WIP_GENERIC)
    akun_applied = await resolve_account_id_by_role(conn, tenant_id, peran)
    nomor = await conn.fetchval(_SQL_NOMOR[jenis], tenant_id, tanggal)
    je = _uuid.uuid4()
    await conn.execute(
        """
        INSERT INTO journal_entries (
            id, tenant_id, journal_number, journal_date,
            description, source_type, source_id,
            total_debit, total_credit, status, created_by
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $8, 'DRAFT', $9)
        """,
        je, tenant_id, nomor, tanggal, deskripsi or f"{kata.capitalize()} applied ({order_number})", source_type, order_id, jumlah, user_id)
    await conn.execute(
        "INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo) VALUES ($1, $2, 1, $3, $4, 0, $5)",
        _uuid.uuid4(), je, wip_id, jumlah, f"WIP {kata} applied {order_number}")
    await conn.execute(
        "INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo) VALUES ($1, $2, 2, $3, 0, $4, $5)",
        _uuid.uuid4(), je, akun_applied, jumlah, f"{memo_akun} {order_number}")
    await conn.execute("UPDATE journal_entries SET status = 'POSTED' WHERE id = $1", je)
