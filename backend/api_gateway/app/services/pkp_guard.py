"""Penjaga PPN untuk tenant non-PKP -- SATU tempat, dipakai faktur penjualan + penjualan tunai.

Pengusaha non-PKP tidak boleh memungut PPN. Kriteria SAMA dengan
role_resolver.resolve_account_id_by_role_if_pkp ("Tenant".is_pkp); nota kredit sudah menolak lewat
resolver itu, faktur & penjualan tunai dulu tidak (asimetri: PPN bisa dipungut, tak bisa dikoreksi).
Ditolak saat HITUNG/SIMPAN (pengguna tahu selagi menyunting), bukan hanya saat posting.
"""
from decimal import Decimal

from fastapi import HTTPException

PESAN_NON_PKP = (
    "Tenant non-PKP tidak dapat memungut PPN. Hapus pajak dari baris atau aktifkan status PKP."
)


async def tolak_ppn_bila_non_pkp(conn, tenant_id: str, tax_total) -> None:
    """422 bila tax_total > 0 dan tenant non-PKP. Pajak nol -> tak ada kueri, tak ada efek."""
    if Decimal(str(tax_total or 0)) <= 0:
        return
    is_pkp = await conn.fetchval('SELECT is_pkp FROM "Tenant" WHERE id = $1', tenant_id)
    if is_pkp is False:
        raise HTTPException(status_code=422, detail=PESAN_NON_PKP)
