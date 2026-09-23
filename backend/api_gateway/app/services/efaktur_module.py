"""Penjaga modul e-Faktur (V293, opsi 2).

Hanya tax_invoices + tax_invoice_sources yang ada (V293) -- cukup untuk by-source/list/detail.
Sisa modul (identitas penjual, baris faktur, pemetaan DJP, NSFP, riwayat ekspor, katalog DJP)
SENGAJA belum dibuat (RECOVERY_MISSING_TABLES_BACKLOG.md: dibuat hanya bila pemilik memakai
e-Faktur di MilkyHoop, dan katalog DJP tak punya sumber di repo).

Rute TULIS e-Faktur dan Settings > PKP memakai dependensi ini: bila satu saja tabel modul belum
ada -> 409 berpesan jujur SEBELUM badan handler berjalan, jadi tak ada baris setengah jadi.
Begitu modul lengkap dibuat, penjaga ini lolos sendiri (to_regclass) -- tak perlu dicabut.
"""
from fastapi import HTTPException

EFAKTUR_MODULE_TABLES = (
    "tax_info", "tax_invoices", "tax_invoice_items", "tax_invoice_sources", "product_djp_mapping",
    "nsfp_assignments", "efaktur_exports", "djp_kode_barang_jasa", "djp_satuan_ukur", "djp_kode_transaksi",
)
PESAN = "Fitur Faktur Pajak belum diaktifkan di MilkyHoop. Buat faktur pajak lewat Coretax."


async def efaktur_module_missing(conn) -> list:
    rows = await conn.fetch(
        "SELECT t FROM unnest($1::text[]) t WHERE to_regclass('public.' || t) IS NULL",
        list(EFAKTUR_MODULE_TABLES),
    )
    return [r["t"] for r in rows]


async def require_efaktur_module():
    """Dependensi FastAPI: 409 bila modul e-Faktur belum lengkap."""
    from .db_pool import get_db_pool
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        missing = await efaktur_module_missing(conn)
    if missing:
        raise HTTPException(
            status_code=409,
            detail={"code": "EFAKTUR_NOT_ENABLED", "message": PESAN, "missing_tables": missing},
        )
