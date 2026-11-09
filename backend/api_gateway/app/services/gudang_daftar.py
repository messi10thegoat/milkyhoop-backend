"""GUDANG standar SO (gelombang 2, kontrak backend/docs/KONTRAK-persediaan-standar-so-20261109.md; MASTER 9 Nov 2026): kunci ?aksi=, teks
posisi, nilai stok per gudang, aksi_tersedia, dan dua perbaikan integritas -- SATU penentu untuk daftar, detail, dan tulis.

- stok_minus (pill): ada barang dengan saldo inventory_ledger < 0 di gudang itu. Cache warehouse_stock dibatasi 0 (trigger GREATEST),
  jadi stok minus tak terlihat di sana (milkyhoop-inventory Rule 7). nonaktif = filter is_active, bukan pill (putusan MASTER).
- Nilai stok = ekspresi YANG SAMA dengan GET /{id}/stock-value (WAC_GUDANG/NILAI_GUDANG dipindah ke sini).
- U8: jadikan_utama = SATU penulis is_default (set-default, buat, ubah) -- tepat satu gudang utama per tenant, di bawah kunci tenant.
- U9: nonaktifkan ditolak bila saldo LEDGER gudang itu ≠ 0 untuk barang mana pun, bersaring tenant (dulu cache floor-0 tanpa tenant)."""
from typing import Optional

from ..schemas.aksi_tersedia import aksi

WAC_GUDANG = "CROSS JOIN LATERAL (SELECT get_weighted_average_cost($2, ws.item_id) AS wac) w"
NILAI_GUDANG = "ROUND(ws.quantity * w.wac, 0)"
AKSI = ("stok_minus",)


def sql_stok_minus(a: str = "warehouses") -> str:
    return (f"EXISTS (SELECT 1 FROM inventory_ledger il WHERE il.tenant_id = {a}.tenant_id AND il.warehouse_id = {a}.id "
            f"GROUP BY il.product_id HAVING SUM(il.quantity_in) - SUM(il.quantity_out) < 0)")


def sql_nilai(a: str = "warehouses") -> str:
    """Σ nilai stok gudang = Σ ROUND(qty × WAC) baris warehouse_stock > 0 (== /stock-value)."""
    return (f"(SELECT COALESCE(SUM(ROUND(ws.quantity * get_weighted_average_cost({a}.tenant_id, ws.item_id), 0)), 0) "
            f"FROM warehouse_stock ws WHERE ws.warehouse_id = {a}.id AND ws.tenant_id = {a}.tenant_id AND ws.quantity > 0)")


def sql_aksi(kunci: str, a: str = "warehouses") -> str:
    return {"stok_minus": sql_stok_minus(a)}[kunci]


def aksi_semua(stok_minus: bool) -> list:
    return ["stok_minus"] if stok_minus else []


def position_text(is_active: bool, is_default: bool, is_branch: bool = False) -> str:
    teks = "Nonaktif" if not is_active else ("Utama" if is_default else "Aktif")
    return f"{teks} · cabang" if is_branch else teks


async def jumlah_berstok(conn, tid: str, warehouse_id) -> int:
    """Barang yang saldo LEDGER-nya ≠ 0 di gudang ini (U9; bukan cache warehouse_stock)."""
    return int(await conn.fetchval(
        "SELECT COUNT(*) FROM (SELECT product_id FROM inventory_ledger WHERE tenant_id = $1 AND warehouse_id = $2 GROUP BY product_id "
        "HAVING SUM(quantity_in) - SUM(quantity_out) <> 0) x", tid, warehouse_id))


def alasan_nonaktif(is_active: bool, berstok: int) -> Optional[str]:
    if not is_active:
        return "Gudang ini sudah nonaktif."
    if berstok:
        return f"Gudang masih berisi stok ({berstok} barang). Pindahkan atau sesuaikan stoknya dulu."
    return None


def alasan_utama(is_active: bool, is_default: bool) -> Optional[str]:
    if is_default:
        return "Gudang ini sudah gudang utama."
    if not is_active:
        return "Gudang nonaktif tidak bisa jadi gudang utama."
    return None


async def jadikan_utama(conn, tid: str, warehouse_id) -> None:
    """U8: SATU penulis is_default -- tepat satu gudang utama per tenant. Pemanggil di dalam transaksi."""
    await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"WAREHOUSE_DEFAULT:{tid}")
    await conn.execute("UPDATE warehouses SET is_default = (id = $1), updated_at = NOW() WHERE tenant_id = $2 "
                       "AND (is_default OR id = $1)", warehouse_id, tid)


async def aksi_tersedia(conn, tid: str, w) -> dict:
    nonaktif = alasan_nonaktif(w["is_active"], await jumlah_berstok(conn, tid, w["id"]))
    utama = alasan_utama(w["is_active"], w["is_default"])
    return {
        "ubah": aksi(True, None),
        "jadikan_utama": aksi(utama is None, utama),
        "nonaktifkan": aksi(nonaktif is None, nonaktif),
        "aktifkan": aksi(not w["is_active"], "Gudang ini sudah aktif."),
    }
