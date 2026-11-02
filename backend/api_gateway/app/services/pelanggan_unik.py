"""Keunikan pelanggan -- 10 Okt 2026 (MASTER GO B2).

- NAMA: tak ada indeks unik DB (V402 menghapus uq_customers_tenant_name yang menjaga kolom mati `name`). App menolak nama
  pelanggan yang PERSIS sama, AKTIF MAUPUN NONAKTIF (NAMA-NONAKTIF 2 Nov 2026, putusan MASTER, pola NetSuite, == vendor_unik);
  terhapus lunak (deleted_at) boleh dipakai ulang (predikat sama dengan nomor). "mirip" = peringatan FE lewat autocomplete.
- NOMOR (nomor_member, di API `code`): dijaga DB oleh V402 uq_customers_tenant_nomor_member (deleted_at IS NULL AND
  nomor_member IS NOT NULL). Cek app di sini memakai predikat YANG SAMA supaya bentrok jadi 409 terbaca, bukan 500 DB.
Galat = 409 GalatBerkode {detail, code, message, field} (VENDOR-DUPLIKAT 1 Nov 2026: satu bentuk dengan vendor; detail
string supaya pembaca detail-string FE menampilkan kalimat; WORKSPACE memetakan code -> medan).
"""
from .galat_berkode import GalatBerkode

KODE_NAMA = "NAMA_PELANGGAN_DIPAKAI"
KODE_NOMOR = "KODE_PELANGGAN_DIPAKAI"


async def tolak_nama_dipakai(conn, tenant_id: str, nama, kecuali_id=None) -> None:
    if not nama:
        return
    r = await conn.fetchrow(
        """SELECT id, is_active FROM customers WHERE tenant_id = $1 AND nama = $2 AND deleted_at IS NULL
           AND ($3::uuid IS NULL OR id <> $3::uuid) ORDER BY is_active DESC LIMIT 1""",
        tenant_id, nama, None if kecuali_id is None else str(kecuali_id))
    if r:
        pesan = ("sudah dipakai pelanggan lain. Gunakan nama lain." if r["is_active"]
                 else "sudah dipakai pelanggan nonaktif — aktifkan lagi dari Kontak atau gunakan nama lain.")
        raise GalatBerkode(409, KODE_NAMA, f"Nama pelanggan \"{nama}\" {pesan}", "name")


async def tolak_nomor_dipakai(conn, tenant_id: str, nomor, kecuali_id=None) -> None:
    if not nomor:
        return
    ada = await conn.fetchval(
        """SELECT id FROM customers WHERE tenant_id = $1 AND nomor_member = $2 AND deleted_at IS NULL
           AND ($3::uuid IS NULL OR id <> $3::uuid) LIMIT 1""",
        tenant_id, nomor, None if kecuali_id is None else str(kecuali_id))
    if ada:
        raise GalatBerkode(409, KODE_NOMOR, f"Kode pelanggan \"{nomor}\" sudah dipakai pelanggan lain. Gunakan kode lain.", "code")
