"""REORDER persediaan (24 Nov 2026, antrean tanpa migrasi): daftar barang yang perlu dipesan ulang dari products.reorder_level.

Satu penentu, server-hitung (FE tak menjumlah): stok = Σ masuk − Σ keluar inventory_ledger (Law 16; bukan cache warehouse_stock yang terpangkas 0);
dipesan = Σ (quantity − quantity_received) baris PO TERBUKA (status sent / partial_received) yang satuannya = satuan dasar barang (atau kosong);
usulan beli = max(batas minimum − (stok + dipesan), 0). Baris PO bersatuan lain TIDAK dikonversi diam-diam: dihitung terpisah (`dipesan_satuan_lain`)
dan diberi catatan -- usulan memakai angka yang pasti benar saja. Tanpa nilai Rp (harga beli = data master, bukan biaya transaksi)."""
from decimal import Decimal
from typing import Optional

NOL = Decimal(0)


def usulan_beli(stok, minimum, dipesan) -> Decimal:
    """Batas minimum dipenuhi oleh stok + yang sudah dipesan; kekurangannya = usulan. Tak pernah negatif."""
    stok, minimum, dipesan = Decimal(str(stok or 0)), Decimal(str(minimum or 0)), Decimal(str(dipesan or 0))
    if minimum <= 0:
        return NOL
    return max(minimum - (stok + dipesan), NOL)


def teks_baris(nama: str, unit: Optional[str], stok, minimum, dipesan, usulan, dipesan_lain) -> str:
    satuan = unit or "pcs"
    t = f"{nama}: stok {_f(stok)} {satuan}, minimum {_f(minimum)}"
    if Decimal(str(dipesan or 0)) > 0:
        t += f", sudah dipesan {_f(dipesan)}"
    t += f" — usul pesan {_f(usulan)} {satuan}." if usulan > 0 else " — aman."
    if Decimal(str(dipesan_lain or 0)) > 0:
        t += " Ada pesanan dengan satuan lain yang belum dihitung; cek di Pesanan Pembelian."
    return t


def _f(x) -> str:
    d = Decimal(str(x or 0)).normalize()
    s = format(d, "f")
    return s.rstrip("0").rstrip(".") if "." in s else s


_SQL = """
SELECT p.id::text AS id, p.nama_produk, COALESCE(NULLIF(p.base_unit, ''), p.satuan) AS unit, p.reorder_level AS minimum,
       COALESCE(s.stok, 0) AS stok, COALESCE(o.dipesan, 0) AS dipesan, COALESCE(o.dipesan_lain, 0) AS dipesan_lain
FROM products p
LEFT JOIN LATERAL (SELECT COALESCE(SUM(l.quantity_in) - SUM(l.quantity_out), 0) AS stok FROM inventory_ledger l
                   WHERE l.tenant_id = p.tenant_id AND l.product_id = p.id) s ON true
LEFT JOIN LATERAL (
    SELECT COALESCE(SUM(GREATEST(i.quantity - COALESCE(i.quantity_received, 0), 0)) FILTER (WHERE COALESCE(NULLIF(i.unit, ''), COALESCE(NULLIF(p.base_unit, ''), p.satuan)) = COALESCE(NULLIF(p.base_unit, ''), p.satuan)), 0) AS dipesan,
           COALESCE(SUM(GREATEST(i.quantity - COALESCE(i.quantity_received, 0), 0)) FILTER (WHERE COALESCE(NULLIF(i.unit, ''), COALESCE(NULLIF(p.base_unit, ''), p.satuan)) <> COALESCE(NULLIF(p.base_unit, ''), p.satuan)), 0) AS dipesan_lain
    FROM purchase_order_items i JOIN purchase_orders po ON po.id = i.po_id
    WHERE po.tenant_id = p.tenant_id AND i.item_id = p.id AND po.status IN ('sent', 'partial_received')) o ON true
WHERE p.tenant_id = $1 AND p.deleted_at IS NULL AND p.item_type = 'goods' AND COALESCE(p.track_inventory, true) AND COALESCE(p.reorder_level, 0) > 0
ORDER BY lower(p.nama_produk), p.id
"""


async def daftar(conn, tenant_id: str, limit: Optional[int] = None) -> dict:
    """-> {items[...usulan>0, urut kekurangan terbesar], jumlah_perlu_pesan, jumlah_dipantau}. Barang dipantau = reorder_level > 0."""
    rows = await conn.fetch(_SQL, tenant_id)
    semua = []
    for r in rows:
        u = usulan_beli(r["stok"], r["minimum"], r["dipesan"])
        semua.append({"id": r["id"], "nama_produk": r["nama_produk"], "satuan": r["unit"] or "pcs", "stok": Decimal(str(r["stok"])),
                      "minimum": Decimal(str(r["minimum"])), "dipesan": Decimal(str(r["dipesan"])), "dipesan_satuan_lain": Decimal(str(r["dipesan_lain"])),
                      "usulan_beli": u, "teks": teks_baris(r["nama_produk"], r["unit"], r["stok"], r["minimum"], r["dipesan"], u, r["dipesan_lain"])})
    perlu = sorted((x for x in semua if x["usulan_beli"] > 0), key=lambda x: (-x["usulan_beli"], x["nama_produk"].lower()))
    return {"items": perlu[:limit] if limit else perlu, "jumlah_perlu_pesan": len(perlu), "jumlah_dipantau": len(semua)}
