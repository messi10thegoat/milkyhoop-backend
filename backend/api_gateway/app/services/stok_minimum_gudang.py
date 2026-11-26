"""MN-1 (26 Nov 2026): stok minimum/maksimum PER GUDANG (Accurate: Minimum/Maksimum Kuantitas per gudang). Tabel V511 stok_minimum_gudang.
Flag gelap stok_minimum_gudang_v1. Stok per gudang = Σ masuk − Σ keluar inventory_ledger per gudang (Law 16; bukan cache warehouse_stock).
Usulan beli per gudang = max(minimum − stok gudang, 0); PO terbuka tak dibagi per gudang (PO tanpa gudang tujuan) -> tak dihitung di sini, dinyatakan di teks.
Gudang sistem (Transit) tak pernah diatur/didaftar."""
from decimal import Decimal
from typing import List, Optional

from .galat_berkode import GalatBerkode
from .reorder_persediaan import _f

FLAG = "stok_minimum_gudang_v1"
NOL = Decimal(0)


async def flag_nyala(conn, tid: str) -> bool:
    return (await conn.fetchval("SELECT EXISTS (SELECT 1 FROM tenant_features WHERE tenant_id = $1 AND feature = $2 AND enabled)", tid, FLAG)) is True


async def wajib_flag(conn, tid: str) -> None:
    if not await flag_nyala(conn, tid):
        raise GalatBerkode(404, "FITUR_BELUM_AKTIF", "Fitur stok minimum per gudang belum aktif.")


def periksa(minimum, maksimum) -> Optional[str]:
    """Pesan galat Indonesia atau None. MURNI (cermin CHECK V511)."""
    mi = Decimal(str(minimum if minimum is not None else 0))
    if mi < 0:
        return "Minimum tidak boleh negatif."
    if maksimum is not None and Decimal(str(maksimum)) < mi:
        return "Maksimum tidak boleh lebih kecil dari minimum."
    return None


_SQL_BARIS = """
SELECT w.id::text AS gudang_id, w.name AS gudang, COALESCE(m.minimum, 0) AS minimum, m.maksimum,
       COALESCE((SELECT SUM(l.quantity_in) - SUM(l.quantity_out) FROM inventory_ledger l
                 WHERE l.tenant_id = w.tenant_id AND l.product_id = $2 AND l.warehouse_id = w.id), 0) AS stok
FROM warehouses w
LEFT JOIN stok_minimum_gudang m ON m.tenant_id = w.tenant_id AND m.warehouse_id = w.id AND m.product_id = $2
WHERE w.tenant_id = $1 AND NOT w.is_system AND (w.is_active OR m.warehouse_id IS NOT NULL)
ORDER BY lower(w.name), w.id
"""


async def baca(conn, tid: str, product_id) -> List[dict]:
    rows = await conn.fetch(_SQL_BARIS, tid, product_id)
    return [{"gudang_id": r["gudang_id"], "gudang": r["gudang"], "minimum": Decimal(str(r["minimum"])),
             "maksimum": Decimal(str(r["maksimum"])) if r["maksimum"] is not None else None, "stok": Decimal(str(r["stok"])),
             "usulan_beli": usulan(r["stok"], r["minimum"])} for r in rows]


def usulan(stok, minimum) -> Decimal:
    mi = Decimal(str(minimum or 0))
    return max(mi - Decimal(str(stok or 0)), NOL) if mi > 0 else NOL


async def simpan(conn, tid: str, product_id, baris: List[dict], user_id=None) -> None:
    """Ganti pengaturan barang ini per gudang yang disebut. minimum 0 + maksimum kosong = hapus pengaturan gudang itu."""
    ada = await conn.fetchval("SELECT 1 FROM products WHERE tenant_id = $1 AND id = $2 AND deleted_at IS NULL", tid, product_id)
    if not ada:
        raise GalatBerkode(404, "BARANG_TAK_ADA", "Barang tidak ditemukan.")
    for b in baris:
        pesan = periksa(b.get("minimum"), b.get("maksimum"))
        if pesan:
            raise GalatBerkode(422, "MINIMUM_TAK_SAH", pesan, field="minimum")
        g = await conn.fetchval("SELECT 1 FROM warehouses WHERE tenant_id = $1 AND id = $2 AND NOT is_system", tid, b["gudang_id"])
        if not g:
            raise GalatBerkode(422, "GUDANG_TAK_SAH", "Gudang tidak ditemukan.", field="gudang_id")
        mi, mk = Decimal(str(b.get("minimum") or 0)), b.get("maksimum")
        if mi == 0 and mk is None:
            await conn.execute("DELETE FROM stok_minimum_gudang WHERE tenant_id = $1 AND product_id = $2 AND warehouse_id = $3", tid, product_id, b["gudang_id"])
            continue
        await conn.execute(
            "INSERT INTO stok_minimum_gudang (tenant_id, product_id, warehouse_id, minimum, maksimum, updated_by) VALUES ($1, $2, $3, $4, $5, $6) "
            "ON CONFLICT (tenant_id, product_id, warehouse_id) DO UPDATE SET minimum = EXCLUDED.minimum, maksimum = EXCLUDED.maksimum, "
            "updated_by = EXCLUDED.updated_by, updated_at = now()",
            tid, product_id, b["gudang_id"], mi, Decimal(str(mk)) if mk is not None else None, user_id)


_SQL_PESAN = """
SELECT p.id::text AS id, p.nama_produk, COALESCE(NULLIF(p.base_unit, ''), p.satuan) AS unit, w.id::text AS gudang_id, w.name AS gudang, m.minimum,
       COALESCE((SELECT SUM(l.quantity_in) - SUM(l.quantity_out) FROM inventory_ledger l
                 WHERE l.tenant_id = m.tenant_id AND l.product_id = m.product_id AND l.warehouse_id = m.warehouse_id), 0) AS stok
FROM stok_minimum_gudang m
JOIN products p ON p.id = m.product_id AND p.tenant_id = m.tenant_id
JOIN warehouses w ON w.id = m.warehouse_id AND w.tenant_id = m.tenant_id
WHERE m.tenant_id = $1 AND m.minimum > 0 AND p.deleted_at IS NULL AND NOT w.is_system
"""


async def daftar_pesan_ulang(conn, tid: str, limit: Optional[int] = None, search: Optional[str] = None, status: Optional[str] = None,
                            skip: int = 0, warehouse_id: Optional[str] = None) -> dict:
    """Pesan Ulang per gudang: baris (barang, gudang) yang stoknya di bawah minimum gudang. Urut kekurangan terbesar."""
    semua = []
    for r in await conn.fetch(_SQL_PESAN, tid):
        u, s = usulan(r["stok"], r["minimum"]), r["unit"] or "pcs"
        teks = f"{r['nama_produk']} di {r['gudang']}: stok {_f(r['stok'])} {s}, minimum {_f(r['minimum'])}"
        teks += f" — usul pesan {_f(u)} {s}." if u > 0 else " — aman."
        semua.append({"id": r["id"], "nama_produk": r["nama_produk"], "satuan": s, "gudang_id": r["gudang_id"], "gudang": r["gudang"],
                      "stok": Decimal(str(r["stok"])), "minimum": Decimal(str(r["minimum"])), "usulan_beli": u, "teks": teks})
    if warehouse_id:
        semua = [x for x in semua if x["gudang_id"] == str(warehouse_id)]
    perlu = sorted((x for x in semua if x["usulan_beli"] > 0), key=lambda x: (-x["usulan_beli"], x["nama_produk"].lower(), x["gudang"].lower()))
    from .reorder_persediaan import halaman
    gudang = sorted({(x["gudang_id"], x["gudang"]) for x in perlu}, key=lambda g: g[1].lower())
    return {**halaman(perlu, len(semua), search, status, skip, limit), "gudang_tersedia": [{"id": i, "nama": n} for i, n in gudang],
            "catatan": "Pesanan pembelian terbuka belum dibagi per gudang; usulan hanya memakai stok gudang."}
