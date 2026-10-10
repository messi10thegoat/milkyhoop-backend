"""Keunikan pelanggan -- 10 Okt 2026 (MASTER GO B2).

- NAMA: tak ada indeks unik DB (V402 menghapus uq_customers_tenant_name yang menjaga kolom mati `name`). App menolak nama
  pelanggan AKTIF yang PERSIS sama; "mirip" = peringatan FE lewat autocomplete (putusan pilot: duplikat = peringatan).
- NOMOR (nomor_member, di API `code`): dijaga DB oleh V402 uq_customers_tenant_nomor_member (deleted_at IS NULL AND
  nomor_member IS NOT NULL). Cek app di sini memakai predikat YANG SAMA supaya bentrok jadi 409 terbaca, bukan 500 DB.
Galat = 409 detail {code, message} (WORKSPACE memetakan code -> medan).
"""
from fastapi import HTTPException

KODE_NAMA = "NAMA_PELANGGAN_DIPAKAI"
KODE_NOMOR = "KODE_PELANGGAN_DIPAKAI"


async def tolak_nama_dipakai(conn, tenant_id: str, nama, kecuali_id=None) -> None:
    if not nama:
        return
    ada = await conn.fetchval(
        """SELECT id FROM customers WHERE tenant_id = $1 AND nama = $2 AND is_active = true
           AND ($3::uuid IS NULL OR id <> $3::uuid) LIMIT 1""",
        tenant_id, nama, None if kecuali_id is None else str(kecuali_id))
    if ada:
        raise HTTPException(status_code=409, detail={"code": KODE_NAMA, "message": (
            f"Nama pelanggan \"{nama}\" sudah dipakai pelanggan lain. Gunakan nama lain.")})


async def tolak_nomor_dipakai(conn, tenant_id: str, nomor, kecuali_id=None) -> None:
    if not nomor:
        return
    ada = await conn.fetchval(
        """SELECT id FROM customers WHERE tenant_id = $1 AND nomor_member = $2 AND deleted_at IS NULL
           AND ($3::uuid IS NULL OR id <> $3::uuid) LIMIT 1""",
        tenant_id, nomor, None if kecuali_id is None else str(kecuali_id))
    if ada:
        raise HTTPException(status_code=409, detail={"code": KODE_NOMOR, "message": (
            f"Kode pelanggan \"{nomor}\" sudah dipakai pelanggan lain. Gunakan kode lain.")})
