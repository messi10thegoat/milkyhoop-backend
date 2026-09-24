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


def effective_shipping_code(explicit_code_id, lines, code_key, shipping_amount):
    """Unit 3d: the code shipping is ACTUALLY taxed under, for GET responses: the explicit
    choice, else the single tax code shared by all taxable goods lines, else None (goods
    carry only a rate, or no shipping). Same identity rule as resolve_shipping_tax; pure."""
    if _d(shipping_amount) <= 0:
        return None
    if explicit_code_id:
        return str(explicit_code_id)
    codes = {str(ln.get(code_key)) if ln.get(code_key) else None
             for ln in lines if _d(ln.get("tax_rate")) > 0}
    return next(iter(codes)) if len(codes) == 1 and None not in codes else None


async def turunkan_tarif_baris(conn, tenant_id, items, code_key, direction="output"):
    """#34 -- tarif PPN baris ditentukan SERVER dari kode pajak, bukan dari kiriman klien.

    Baris berkode: tarif = tax_codes.rate milik tenant (kode tak dikenal/bukan milik tenant -> 400);
    tarif kiriman klien DIABAIKAN (PPN-11 + tax_rate 0 -> 11%).
    Baris tanpa kode bertarif > 0: tarif itu wajib dimiliki kode PPN AKTIF tenant (arah sama);
    tidak ada -> 400 (klien tak boleh mengarang tarif). Tarif 0 tanpa kode -> tanpa pajak.
    Mengubah `items` di tempat; panggil SEBELUM attach_dpp_factors."""
    cache = {}
    for it in items:
        code = it.get(code_key)
        if code:
            try:
                tcid = code if isinstance(code, UUID) else UUID(str(code))
            except ValueError:
                raise HTTPException(status_code=400, detail="Kode pajak tidak valid.")
            if tcid not in cache:
                cache[tcid] = await conn.fetchval(
                    "SELECT rate FROM tax_codes WHERE id = $1 AND tenant_id = $2", tcid, tenant_id
                )
            if cache[tcid] is None:
                raise HTTPException(status_code=400, detail="Kode pajak tidak ditemukan.")
            it["tax_rate"] = _d(cache[tcid])
            continue
        rate = _d(it.get("tax_rate"))
        if rate <= 0:
            it["tax_rate"] = Decimal("0")
            continue
        key = ("rate", rate)
        if key not in cache:
            cache[key] = await conn.fetchval(
                """SELECT count(*) FROM tax_codes
                   WHERE tenant_id = $1 AND tax_type = 'ppn' AND direction = $2
                     AND rate = $3 AND is_active = true""",
                tenant_id, direction, rate,
            )
        if not cache[key]:
            raise HTTPException(
                status_code=400,
                detail=f"Tidak ada kode pajak aktif bertarif {rate.normalize()}%. Pilih kode pajaknya.",
            )
    return items


async def attach_dpp_factors(conn, tenant_id, items, code_key, direction="output"):
    """Tempel dpp_factor_num/den ke tiap baris (dict) untuk compute_document."""
    cache = {}
    for it in items:
        num, den = await resolve_dpp_factor(
            conn, tenant_id, it.get(code_key), it.get("tax_rate"), direction, cache
        )
        it["dpp_factor_num"], it["dpp_factor_den"] = num, den
    return items
