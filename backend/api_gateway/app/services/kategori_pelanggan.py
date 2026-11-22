"""BUG-338A: KATEGORI PELANGGAN (master per tenant; pelanggan menempel ke SATU kategori; kategori menunjuk SATU daftar harga).
SATU penentu untuk router /api/customer-categories dan validasi customers.category_id. Semua kueri bersaring tenant (+ FK komposit V508)."""
from typing import Optional

import asyncpg

from .galat_berkode import GalatBerkode

MAKS_NAMA = 5


def _nama(x) -> str:
    return " ".join(str(x or "").split())


def bentuk(r) -> dict:
    return {"id": str(r["id"]), "name": r["name"], "price_list_id": str(r["price_list_id"]) if r["price_list_id"] else None,
            "price_list_name": r["price_list_name"], "is_active": bool(r["is_active"]), "jumlah_pelanggan": int(r["jumlah_pelanggan"])}


SQL_DAFTAR = """SELECT c.id, c.name, c.price_list_id, c.is_active, pl.name AS price_list_name,
                       (SELECT COUNT(*) FROM customers u WHERE u.tenant_id = c.tenant_id AND u.category_id = c.id AND u.deleted_at IS NULL) AS jumlah_pelanggan
                FROM customer_categories c LEFT JOIN price_lists pl ON pl.id = c.price_list_id AND pl.tenant_id = c.tenant_id
                WHERE c.tenant_id = $1"""


async def daftar(conn, tid: str) -> list:
    return [bentuk(r) for r in await conn.fetch(SQL_DAFTAR + " ORDER BY lower(c.name), c.id", tid)]


async def satu(conn, tid: str, kid):
    return await conn.fetchrow(SQL_DAFTAR + " AND c.id = $2", tid, kid)


async def pastikan_daftar(conn, tid: str, pl_id) -> None:
    if pl_id is not None and not await conn.fetchval("SELECT 1 FROM price_lists WHERE id = $1 AND tenant_id = $2 AND is_active", pl_id, tid):
        raise GalatBerkode(400, "DH_TAK_ADA", "Daftar harga tidak ditemukan di usaha ini.", field="price_list_id")


async def pastikan_kategori(conn, tid: str, kid) -> None:
    """Untuk customers.category_id: kategori wajib milik usaha ini dan aktif (None = tanpa kategori)."""
    if kid is not None and not await conn.fetchval("SELECT 1 FROM customer_categories WHERE id = $1 AND tenant_id = $2 AND is_active", kid, tid):
        raise GalatBerkode(400, "KATEGORI_TAK_ADA", "Kategori pelanggan tidak ditemukan di usaha ini.", field="category_id")


def _ganda(e) -> GalatBerkode:
    return GalatBerkode(409, "KATEGORI_NAMA_GANDA", "Nama kategori sudah dipakai di usaha ini.", field="name")


async def buat(conn, tid: str, name, price_list_id) -> str:
    n = _nama(name)
    if not n:
        raise GalatBerkode(422, "KATEGORI_NAMA_KOSONG", "Nama kategori wajib diisi.", field="name")
    await pastikan_daftar(conn, tid, price_list_id)
    try:
        return str(await conn.fetchval("INSERT INTO customer_categories (tenant_id, name, price_list_id) VALUES ($1, $2, $3) RETURNING id", tid, n, price_list_id))
    except asyncpg.exceptions.UniqueViolationError as e:
        raise _ganda(e)


async def ubah(conn, tid: str, kid, data: dict) -> None:
    if not await conn.fetchval("SELECT 1 FROM customer_categories WHERE id = $1 AND tenant_id = $2 FOR UPDATE", kid, tid):
        raise GalatBerkode(404, "KATEGORI_TAK_ADA", "Kategori pelanggan tidak ditemukan.")
    sets, par = [], [kid, tid]
    if "name" in data:
        n = _nama(data["name"])
        if not n:
            raise GalatBerkode(422, "KATEGORI_NAMA_KOSONG", "Nama kategori wajib diisi.", field="name")
        par.append(n); sets.append(f"name = ${len(par)}")
    if "price_list_id" in data:
        await pastikan_daftar(conn, tid, data["price_list_id"])
        par.append(data["price_list_id"]); sets.append(f"price_list_id = ${len(par)}")
    if "is_active" in data and data["is_active"] is not None:
        par.append(bool(data["is_active"])); sets.append(f"is_active = ${len(par)}")
    if not sets:
        return
    try:
        await conn.execute(f"UPDATE customer_categories SET {', '.join(sets)}, updated_at = now() WHERE id = $1 AND tenant_id = $2", *par)
    except asyncpg.exceptions.UniqueViolationError as e:
        raise _ganda(e)


async def nama_pelanggan(conn, tid: str, kid) -> tuple:
    """(jumlah tanpa terhapus, nama maks MAKS_NAMA) pelanggan kategori itu -- predikat SAMA dgn hitung DH (BUG-340)."""
    n = int(await conn.fetchval("SELECT COUNT(*) FROM customers WHERE tenant_id = $1 AND category_id = $2 AND deleted_at IS NULL", tid, kid) or 0)
    nama = [r["nama"] for r in await conn.fetch(
        "SELECT nama FROM customers WHERE tenant_id = $1 AND category_id = $2 AND deleted_at IS NULL AND NULLIF(btrim(nama), '') IS NOT NULL "
        f"ORDER BY lower(nama), id LIMIT {MAKS_NAMA}", tid, kid)]
    return n, nama


async def hapus(conn, tid: str, kid) -> None:
    """Hapus permanen: 409 bila masih dipakai pelanggan (termasuk yang terhapus-lunak: FK komposit menolaknya) -- jumlah + nama di galat."""
    r = await conn.fetchrow("SELECT name FROM customer_categories WHERE id = $1 AND tenant_id = $2 FOR UPDATE", kid, tid)
    if not r:
        raise GalatBerkode(404, "KATEGORI_TAK_ADA", "Kategori pelanggan tidak ditemukan.")
    n, nama = await nama_pelanggan(conn, tid, kid)
    n_semua = int(await conn.fetchval("SELECT COUNT(*) FROM customers WHERE tenant_id = $1 AND category_id = $2", tid, kid) or 0)
    if n_semua:
        sebut = (": " + ", ".join(nama) + (f" dan {n - len(nama)} lainnya" if n > len(nama) else "")) if nama else ""
        raise GalatBerkode(409, "KATEGORI_DIPAKAI_PELANGGAN",
                           f"{r['name']} dipakai {n_semua} pelanggan{sebut}; pindahkan pelanggannya dulu atau pilih Nonaktifkan.",
                           extra={"jumlah_pelanggan": n_semua, "nama_pelanggan": nama, "sisa_pelanggan": max(0, n - len(nama))})
    await conn.execute("DELETE FROM customer_categories WHERE id = $1 AND tenant_id = $2", kid, tid)
