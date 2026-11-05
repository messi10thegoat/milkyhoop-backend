"""Master satuan: SATU sumber untuk halaman Satuan (GET/POST /api/items/units) DAN form barang (/api/units, /api/units/dropdown).

BUG-014 (5 Nov 2026): POST /api/items/units dulu membalas 200 tanpa menulis apa pun, dan GET-nya tak membaca product_units,
sehingga satuan baru hilang sunyi. Kini:
- daftar = satuan bawaan (DEFAULT_UNITS) + product_units aktif tenant + nama yang sudah dipakai produk/konversi (data lama).
  Kunci dedupe = singkatan tanpa beda huruf besar/kecil; baris tenant (ber-id) menang atas bawaan/turunan.
- buat = INSERT product_units; teks disimpan apa adanya (tak dipaksa huruf kecil), bentrok dicocokkan tanpa beda huruf besar/kecil
  (indeks unik uq_product_units_tenant_abbr = lower(abbreviation)) -> 400 berkalimat, tidak pernah sukses tanpa baris.
"""
from typing import Optional

from ..schemas.items import DEFAULT_UNITS
from .galat_berkode import GalatBerkode

ID_BAWAAN = "bawaan:"
ID_TERPAKAI = "terpakai:"


def kunci(teks: str) -> str:
    return " ".join((teks or "").split()).lower()


async def daftar(conn, tenant_id: str, cari: Optional[str] = None) -> list:
    """-> [{id, name, abbreviation, is_system, bawaan}] urut bawaan dulu lalu nama. id bawaan/turunan = 'bawaan:<x>' / 'terpakai:<x>'."""
    rows = await conn.fetch(
        """SELECT id::text AS id, name, abbreviation, is_system FROM product_units WHERE tenant_id = $1 AND COALESCE(is_active, true)
           UNION ALL
           SELECT NULL, u, u, false FROM (SELECT base_unit AS u FROM products WHERE tenant_id = $1
                                          UNION SELECT satuan FROM products WHERE tenant_id = $1
                                          UNION SELECT conversion_unit FROM unit_conversions WHERE tenant_id = $1) t
           WHERE NULLIF(btrim(u), '') IS NOT NULL""",
        tenant_id,
    )
    hasil = {}
    for r in rows:  # baris tenant dulu (UNION ALL: product_units di depan), turunan hanya mengisi yang belum ada
        k = kunci(r["abbreviation"])
        if k and k not in hasil:
            hasil[k] = {"id": r["id"] or ID_TERPAKAI + r["abbreviation"], "name": r["name"], "abbreviation": r["abbreviation"],
                        "is_system": bool(r["is_system"]), "bawaan": False}
    bawaan = []
    for u in DEFAULT_UNITS:
        b = hasil.pop(kunci(u), None) or {"id": ID_BAWAAN + u, "name": u, "abbreviation": u, "is_system": True}
        bawaan.append({**b, "bawaan": True})
    lain = sorted(hasil.values(), key=lambda x: kunci(x["name"]))
    semua = bawaan + lain
    if cari and cari.strip():
        c = kunci(cari)
        semua = [x for x in semua if c in kunci(x["name"]) or c in kunci(x["abbreviation"])]
    return semua


def _galat(pesan: str, field: str = "abbreviation") -> GalatBerkode:
    return GalatBerkode(400, "SATUAN_TIDAK_VALID", pesan, field=field)


async def buat(conn, tenant_id: str, nama: str, singkatan: Optional[str] = None) -> dict:
    """INSERT satuan kustom. -> {id, name, abbreviation}. Kosong/terlalu panjang/sudah ada = GalatBerkode 400 (tak pernah sukses sunyi)."""
    nama = " ".join((nama or "").split())
    singkatan = " ".join((singkatan or "").split()) or nama
    if not nama:
        raise _galat("Nama satuan wajib diisi.", "name")
    if len(nama) > 50:
        raise _galat("Nama satuan maksimal 50 karakter.", "name")
    if len(singkatan) > 20:
        raise _galat("Singkatan satuan maksimal 20 karakter.")
    if kunci(singkatan) in {kunci(u) for u in DEFAULT_UNITS}:
        raise GalatBerkode(400, "SATUAN_SUDAH_ADA", f"Satuan '{singkatan}' sudah ada (satuan bawaan).", field="abbreviation")
    uid = await conn.fetchval(
        """INSERT INTO product_units (tenant_id, name, abbreviation, is_system) VALUES ($1, $2, $3, false)
           ON CONFLICT (tenant_id, lower(abbreviation)) DO NOTHING RETURNING id""",
        tenant_id, nama, singkatan,
    )
    if uid is None:
        raise GalatBerkode(400, "SATUAN_SUDAH_ADA", f"Satuan '{singkatan}' sudah ada.", field="abbreviation")
    return {"id": str(uid), "name": nama, "abbreviation": singkatan}
