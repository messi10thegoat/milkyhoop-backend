"""Keunikan vendor -- VENDOR-DUPLIKAT 1 Nov 2026 (kembar services/pelanggan_unik.py, satu bentuk galat).

- NAMA: dijaga DB uq_vendors_tenant_name UNIQUE(tenant_id, name) -- SEMUA vendor, aktif maupun nonaktif. Cek app
  memakai predikat YANG SAMA (persis, peka huruf) supaya bentrok jadi 409 terbaca, bukan 500 DB.
- KODE: dijaga DB lewat indeks unik parsial (code IS NOT NULL); predikat sama.
Galat = 409 GalatBerkode {detail, code, message, field}; pemegang nonaktif disebut di kalimatnya.
"""
from . import teks_galat as tg
from .galat_berkode import GalatBerkode

KODE_NAMA = "NAMA_VENDOR_DIPAKAI"
KODE_KODE = "KODE_VENDOR_DIPAKAI"


async def tolak_nama_dipakai(conn, tenant_id: str, nama, kecuali_id=None) -> None:
    if not nama:
        return
    r = await conn.fetchrow(
        """SELECT id, is_active FROM vendors WHERE tenant_id = $1 AND name = $2
           AND ($3::uuid IS NULL OR id <> $3::uuid) LIMIT 1""",
        tenant_id, nama, None if kecuali_id is None else str(kecuali_id))
    if r:
        raise GalatBerkode(409, KODE_NAMA, tg.sudah_dipakai("vendor", "nama", nama, r["is_active"]), "name")


async def tolak_kode_dipakai(conn, tenant_id: str, kode, kecuali_id=None) -> None:
    if not kode:
        return
    r = await conn.fetchrow(
        """SELECT id, is_active FROM vendors WHERE tenant_id = $1 AND code = $2 AND code IS NOT NULL
           AND ($3::uuid IS NULL OR id <> $3::uuid) LIMIT 1""",
        tenant_id, kode, None if kecuali_id is None else str(kecuali_id))
    if r:
        raise GalatBerkode(409, KODE_KODE, tg.sudah_dipakai("vendor", "kode", kode, r["is_active"]), "code")
