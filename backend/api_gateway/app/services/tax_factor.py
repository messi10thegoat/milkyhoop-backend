"""Faktor DPP per kode pajak (V289, PMK 131/2024) -- SATU resolver untuk semua dokumen.

DPP yang dikenai tarif = DPP harga jual x (dpp_factor_num / dpp_factor_den).
Urutan penentuan faktor sebuah baris/dokumen:
  1. kode pajak eksplisit (tax_code_id) milik tenant ini -> faktornya;
  2. tanpa kode: kode PPN AKTIF tenant dengan tarif & arah yang sama. Tepat satu faktor ->
     dipakai; nol kode -> 1/1; LEBIH dari satu faktor berbeda (mis. 12% umum 11/12 dan
     12% barang mewah 1/1) -> 400: pengguna WAJIB memilih kode, sistem tidak menebak;
  3. tarif 0 -> 1/1.
Pecahan eksak (bukan desimal) supaya 11/12 tak menggeser sen.
"""
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException


def _d(v) -> Decimal:
    return Decimal(str(v)) if v not in (None, "") else Decimal("0")


async def resolve_dpp_factor(conn, tenant_id, tax_code_id, rate, direction, cache=None):
    rate = _d(rate)
    if rate <= 0:
        return (1, 1)
    key = (str(tax_code_id) if tax_code_id else None, str(rate), direction)
    if cache is not None and key in cache:
        return cache[key]
    fac = None
    if tax_code_id:
        try:
            tcid = tax_code_id if isinstance(tax_code_id, UUID) else UUID(str(tax_code_id))
        except ValueError:
            tcid = None
        if tcid:
            row = await conn.fetchrow(
                "SELECT dpp_factor_num, dpp_factor_den FROM tax_codes WHERE id = $1 AND tenant_id = $2",
                tcid, tenant_id,
            )
            if row:
                fac = (row["dpp_factor_num"], row["dpp_factor_den"])
    if fac is None:
        rows = await conn.fetch(
            """SELECT DISTINCT dpp_factor_num, dpp_factor_den FROM tax_codes
               WHERE tenant_id = $1 AND tax_type = 'ppn' AND direction = $2
                 AND rate = $3 AND is_active = true""",
            tenant_id, direction, rate,
        )
        if len(rows) > 1:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Ada lebih dari satu kode PPN {rate.normalize()}% dengan dasar pengenaan "
                    "berbeda (mis. DPP nilai lain 11/12 dan DPP penuh). Pilih kode pajaknya."
                ),
            )
        fac = (rows[0]["dpp_factor_num"], rows[0]["dpp_factor_den"]) if rows else (1, 1)
    if cache is not None:
        cache[key] = fac
    return fac


async def attach_dpp_factors(conn, tenant_id, items, code_key, direction="output"):
    """Tempel dpp_factor_num/den ke tiap baris (dict) untuk compute_document."""
    cache = {}
    for it in items:
        num, den = await resolve_dpp_factor(
            conn, tenant_id, it.get(code_key), it.get("tax_rate"), direction, cache
        )
        it["dpp_factor_num"], it["dpp_factor_den"] = num, den
    return items
