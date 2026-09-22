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


async def resolve_shipping_tax(conn, tenant_id, explicit_code_id, lines, shipping_amount, code_key):
    """Pajak ongkir (V290). Eksplisit -> kode itu. Kosong -> IKUT kode pajak barang (UU PPN
    Pasal 1 angka 18: Harga Jual termasuk semua biaya yang diminta penjual): satu dasar
    (tarif+faktor) di antara baris kena pajak -> dipakai; tak ada baris kena pajak -> 0;
    lebih dari satu -> 400, pengguna memilih. `lines` sudah bertempel faktor.
    Kembalikan {code_id, rate, num, den}."""
    if _d(shipping_amount) <= 0:
        return {"code_id": None, "rate": Decimal("0"), "num": 1, "den": 1}
    if explicit_code_id:
        try:
            tcid = explicit_code_id if isinstance(explicit_code_id, UUID) else UUID(str(explicit_code_id))
        except ValueError:
            tcid = None
        row = await conn.fetchrow(
            "SELECT id, rate, dpp_factor_num, dpp_factor_den FROM tax_codes WHERE id = $1 AND tenant_id = $2",
            tcid, tenant_id,
        ) if tcid else None
        if not row:
            raise HTTPException(status_code=400, detail="Kode pajak ongkos kirim tidak ditemukan.")
        return {"code_id": str(row["id"]), "rate": _d(row["rate"]),
                "num": row["dpp_factor_num"], "den": row["dpp_factor_den"]}
    bases = {}
    for ln in lines:
        r = _d(ln.get("tax_rate"))
        if r <= 0:
            continue
        key = (r, int(ln.get("dpp_factor_num") or 1), int(ln.get("dpp_factor_den") or 1))
        bases.setdefault(key, set()).add(str(ln.get(code_key)) if ln.get(code_key) else None)
    if not bases:
        return {"code_id": None, "rate": Decimal("0"), "num": 1, "den": 1}
    if len(bases) > 1:
        raise HTTPException(
            status_code=400,
            detail=("Barang pada dokumen ini memakai lebih dari satu tarif/dasar PPN. "
                    "Pilih kode pajak untuk ongkos kirim."),
        )
    (rate, num, den), codes = next(iter(bases.items()))
    return {"code_id": next(iter(codes)) if len(codes) == 1 else None,
            "rate": rate, "num": num, "den": den}


async def attach_dpp_factors(conn, tenant_id, items, code_key, direction="output"):
    """Tempel dpp_factor_num/den ke tiap baris (dict) untuk compute_document."""
    cache = {}
    for it in items:
        num, den = await resolve_dpp_factor(
            conn, tenant_id, it.get(code_key), it.get("tax_rate"), direction, cache
        )
        it["dpp_factor_num"], it["dpp_factor_den"] = num, den
    return items
