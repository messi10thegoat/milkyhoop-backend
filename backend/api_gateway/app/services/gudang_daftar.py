"""GUDANG standar SO (gelombang 2, kontrak backend/docs/KONTRAK-persediaan-standar-so-20261109.md; MASTER 9 Nov 2026): kunci ?aksi=, teks
posisi, nilai stok per gudang, aksi_tersedia, dan satu perbaikan integritas (U9) -- SATU penentu untuk daftar, detail, dan tulis.

- stok_minus (pill): ada barang dengan saldo inventory_ledger < 0 di gudang itu. Cache warehouse_stock dibatasi 0 (trigger GREATEST),
  jadi stok minus tak terlihat di sana (milkyhoop-inventory Rule 7). nonaktif = filter is_active, bukan pill (putusan MASTER).
- Nilai stok = ekspresi YANG SAMA dengan GET /{id}/stock-value (WAC_GUDANG/NILAI_GUDANG dipindah ke sini).
- Satu gudang utama per tenant DIJAGA DB: trigger trg_single_default_warehouse (BEFORE INSERT/UPDATE WHEN is_default; diukur pg_trigger
  9 Nov, ENABLED) -- klaim U8 kontrak ("set-default tak mencabut yang lama") GUGUR di uji salinan; tak ada penulis tambahan di kode.
- U9: nonaktifkan ditolak bila saldo LEDGER gudang itu ≠ 0 untuk barang mana pun, bersaring tenant (dulu cache floor-0 tanpa tenant)."""
from decimal import Decimal
from typing import Optional

from ..schemas.aksi_tersedia import aksi

WAC_GUDANG = "CROSS JOIN LATERAL (SELECT get_weighted_average_cost($2, ws.item_id) AS wac) w"
NILAI_GUDANG = "ROUND(ws.quantity * w.wac, 0)"
AKSI = ("stok_minus", "nonaktif")  # K1 (13 Nov): pill Nonaktif = filter is_active; urutan = urutan aksi_semua
HAVING_MINUS = "SUM(il.quantity_in) - SUM(il.quantity_out) < 0"  # SATU predikat: EXISTS daftar (pill) == daftar barang minus (detail) == per gudang (Barang)


def sql_stok_minus(a: str = "warehouses") -> str:
    return (f"EXISTS (SELECT 1 FROM inventory_ledger il WHERE il.tenant_id = {a}.tenant_id AND il.warehouse_id = {a}.id "
            f"GROUP BY il.product_id HAVING {HAVING_MINUS})")


def sql_nilai(a: str = "warehouses") -> str:
    """Σ nilai stok gudang = Σ ROUND(qty × WAC) baris warehouse_stock > 0 (== /stock-value)."""
    return (f"(SELECT COALESCE(SUM(ROUND(ws.quantity * get_weighted_average_cost({a}.tenant_id, ws.item_id), 0)), 0) "
            f"FROM warehouse_stock ws WHERE ws.warehouse_id = {a}.id AND ws.tenant_id = {a}.tenant_id AND ws.quantity > 0)")


def sql_aksi(kunci: str, a: str = "warehouses") -> str:
    return {"stok_minus": sql_stok_minus(a), "nonaktif": f"NOT {a}.is_active"}[kunci]


def aksi_semua(stok_minus: bool, nonaktif: bool = False) -> list:
    """Kunci pill yang COCOK untuk baris ini, urutan = AKSI."""
    return [k for k, ya in (("stok_minus", stok_minus), ("nonaktif", nonaktif)) if ya]


def kolom_turunan(a: str = "warehouses") -> str:
    """Kolom turunan baris gudang: SATU ekspresi untuk DAFTAR dan DETAIL (K7: detail memakai penentu yang sama dengan daftar)."""
    return f"{sql_stok_minus(a)} AS stok_minus, {sql_nilai(a)} AS nilai_stok"


def turunan(row) -> dict:
    """Baris hasil `SELECT *, kolom_turunan()` -> {aksi, aksi_semua, ada_stok_minus, nilai_stok} (daftar dan detail)."""
    semua = aksi_semua(bool(row["stok_minus"]), not row["is_active"])
    return {"aksi": (semua or [None])[0], "aksi_semua": semua, "ada_stok_minus": bool(row["stok_minus"]), "nilai_stok": int(row["nilai_stok"] or 0)}


async def barang_minus(conn, tid: str, warehouse_id, batas: int = 100) -> list:
    """Barang yang saldo LEDGER-nya < 0 di gudang ini (predikat HAVING_MINUS yang sama dgn pill stok_minus; bukan cache warehouse_stock yang diklamp 0)."""
    rows = await conn.fetch(
        f"SELECT il.product_id, p.nama_produk AS nama, COALESCE(NULLIF(p.base_unit, ''), p.satuan) AS satuan, "
        f"SUM(il.quantity_in) - SUM(il.quantity_out) AS saldo FROM inventory_ledger il "
        f"LEFT JOIN products p ON p.id = il.product_id AND p.tenant_id = il.tenant_id "
        f"WHERE il.tenant_id = $1 AND il.warehouse_id = $2 GROUP BY il.product_id, p.nama_produk, p.base_unit, p.satuan "
        f"HAVING {HAVING_MINUS} ORDER BY saldo ASC, p.nama_produk, il.product_id LIMIT $3", tid, warehouse_id, batas)
    return [{"item_id": str(r["product_id"]), "nama": r["nama"], "satuan": r["satuan"], "saldo": float(Decimal(str(r["saldo"])))} for r in rows]


async def stok_per_gudang(conn, tid: str, product_id) -> list:
    """P5: saldo LEDGER satu barang per gudang (saldo != 0), minus ditandai. Baris ledger tanpa gudang = '(tanpa gudang)' agar Σ == stok barang."""
    rows = await conn.fetch(
        "SELECT il.warehouse_id, COALESCE(w.name, '(tanpa gudang)') AS nama, w.code AS kode, COALESCE(w.is_active, true) AS aktif, "
        "SUM(il.quantity_in) - SUM(il.quantity_out) AS saldo FROM inventory_ledger il "
        "LEFT JOIN warehouses w ON w.id = il.warehouse_id AND w.tenant_id = il.tenant_id "
        "WHERE il.tenant_id = $1 AND il.product_id = $2 GROUP BY il.warehouse_id, w.name, w.code, w.is_active "
        "HAVING SUM(il.quantity_in) - SUM(il.quantity_out) <> 0 ORDER BY nama, il.warehouse_id", tid, product_id)
    return [{"warehouse_id": str(r["warehouse_id"]) if r["warehouse_id"] else None, "nama": r["nama"], "kode": r["kode"], "aktif": bool(r["aktif"]),
             "saldo": float(Decimal(str(r["saldo"]))), "minus": Decimal(str(r["saldo"])) < 0} for r in rows]


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


async def aksi_tersedia(conn, tid: str, w) -> dict:
    nonaktif = alasan_nonaktif(w["is_active"], await jumlah_berstok(conn, tid, w["id"]))
    utama = alasan_utama(w["is_active"], w["is_default"])
    return {
        "ubah": aksi(True, None),
        "jadikan_utama": aksi(utama is None, utama),
        "nonaktifkan": aksi(nonaktif is None, nonaktif),
        "aktifkan": aksi(not w["is_active"], "Gudang ini sudah aktif."),
    }
