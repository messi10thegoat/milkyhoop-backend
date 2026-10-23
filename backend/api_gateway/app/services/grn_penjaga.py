"""Penjaga GRNI (RANCANGAN-penerimaan-barang-grn §7a, Law 15): per tenant, saldo jurnal EFEKTIF akun peran
GOODS_RECEIVED_NOT_INVOICED (2-10150, kredit-normal) == Σ baris GRN tak-void ber-stok: (qty - qty retur) x harga terima - Σ nilai GRNI
tautan tagihan AKTIF. Fail-closed: peran tak terpetakan = galat, bukan "0". Dua sisi (sehat HIJAU + injeksi MERAH) diuji harness."""
from decimal import Decimal

NOL = Decimal("0")

SQL_SALDO_GL = """
    SELECT COALESCE(SUM(jl.credit - jl.debit), 0)
    FROM journal_lines jl JOIN journal_entries je ON je.id = jl.journal_id
    WHERE je.tenant_id = $1 AND je.status = 'POSTED' AND is_effective_journal(je.id) AND jl.account_id = $2
"""
SQL_TERBUKA_DOK = """
    SELECT COALESCE(SUM(ROUND((i.quantity - i.quantity_returned) * i.unit_cost, 2)), 0)
         - COALESCE((SELECT SUM(l.grni_amount) FROM goods_receipt_bill_links l JOIN goods_receipt_items i2 ON i2.id = l.goods_receipt_item_id
                     JOIN goods_receipts g2 ON g2.id = i2.goods_receipt_id
                     WHERE g2.tenant_id = $1 AND g2.status = 'posted' AND l.status = 'active'), 0)
    FROM goods_receipt_items i JOIN goods_receipts g ON g.id = i.goods_receipt_id
    WHERE g.tenant_id = $1 AND g.status = 'posted' AND i.ber_stok
"""


async def periksa(conn, tenant_id: str) -> dict:
    """-> {saldo_gl, terbuka_dokumen, selisih, sehat}. Galat bila peran GRNI tak terpetakan (fail-closed)."""
    from .role_resolver import AccountRole, resolve_account_id_by_role
    akun = await resolve_account_id_by_role(conn, tenant_id, AccountRole.GOODS_RECEIVED_NOT_INVOICED)
    gl = Decimal(str(await conn.fetchval(SQL_SALDO_GL, tenant_id, akun)))
    dok = Decimal(str(await conn.fetchval(SQL_TERBUKA_DOK, tenant_id)))
    return {"saldo_gl": gl, "terbuka_dokumen": dok, "selisih": gl - dok, "sehat": gl == dok}
