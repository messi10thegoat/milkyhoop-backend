"""BUG-259 (16 Nov 2026; putusan MASTER): LEMBAR PENGHITUNGAN STOK per gudang (Accurate: Laporan Gudang > Lembar Penghitungan Stok) -- dicetak untuk
petugas gudang menghitung fisik; hasilnya dimasukkan lewat Penyesuaian Stok (jenis Hitung ulang, pratinjau selisih sudah ada).

Angka = stok BUKU per gudang PER TANGGAL dari inventory_ledger (Σ masuk − Σ keluar, movement_date <= per_tanggal, gudang itu; BUKAN cache
warehouse_stock yang dikunci 0). Barang = dilacak stok (goods + track_inventory), aktif, tak terhapus. Bawaan: hanya barang yang PERNAH punya gerakan di gudang
itu sampai per_tanggal (stok 0 yang pernah bergerak tetap tampil -- bisa saja fisiknya ada); sertakan_semua=true = semua barang dilacak. Stok minus tampil
(ditandai) agar dihitung ulang. Satuan = satuan dasar. Hanya MEMBACA."""
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from . import barang_daftar as BD

BATAS_BARIS = 5000

SQL_LEMBAR = """
SELECT p.id, p.item_code AS kode, p.nama_produk AS nama, p.kategori, p.merek,
       COALESCE(NULLIF(p.base_unit, ''), p.satuan) AS satuan,
       COALESCE(SUM(COALESCE(il.quantity_in, 0) - COALESCE(il.quantity_out, 0)) FILTER (WHERE il.movement_date <= $3), 0) AS stok_buku
FROM products p
LEFT JOIN inventory_ledger il ON il.product_id = p.id AND il.tenant_id = $1 AND il.warehouse_id = $2
WHERE p.tenant_id = $1 AND p.deleted_at IS NULL AND p.item_type = 'goods' AND p.track_inventory = true AND p.status = 'active'
  AND ($4::text IS NULL OR p.kategori = $4) AND ($5::text IS NULL OR p.merek = $5)
GROUP BY p.id
HAVING $6::boolean OR COUNT(il.id) FILTER (WHERE il.movement_date <= $3) > 0
ORDER BY p.item_code NULLS LAST, p.nama_produk, p.id
LIMIT $7"""


class LembarTerlaluBesar(Exception):
    pass


def teks_stok(x, satuan: Optional[str]) -> str:
    return BD._q(Decimal(str(x))) + (f" {satuan}" if satuan else "")


async def muat_lembar(conn, tid: str, gudang: dict, per_tanggal: date, kategori: Optional[str] = None, merek: Optional[str] = None,
                      sertakan_semua: bool = False, dibuat_pada: Optional[datetime] = None) -> dict:
    """gudang = {id, nama}. -> {gudang, per_tanggal, dibuat_pada, saringan, baris:[...], total_baris, jumlah_minus}. > BATAS_BARIS -> LembarTerlaluBesar."""
    kat, mrk = (kategori or "").strip() or None, (merek or "").strip() or None
    rows = await conn.fetch(SQL_LEMBAR, tid, str(gudang["id"]), per_tanggal, kat, mrk, bool(sertakan_semua), BATAS_BARIS + 1)
    if len(rows) > BATAS_BARIS:
        raise LembarTerlaluBesar()
    baris = []
    for i, r in enumerate(rows, 1):
        s = Decimal(str(r["stok_buku"]))
        baris.append({"no": i, "item_id": str(r["id"]), "kode": r["kode"], "nama": r["nama"], "kategori": r["kategori"], "merek": r["merek"],
                      "satuan": r["satuan"], "stok_buku": float(s), "stok_buku_text": teks_stok(s, r["satuan"]), "minus": s < 0})
    return {"gudang": {"id": str(gudang["id"]), "nama": gudang["nama"]}, "per_tanggal": per_tanggal.isoformat(),
            "dibuat_pada": (dibuat_pada or datetime.now()).isoformat(timespec="seconds"),
            "saringan": {"kategori": kat, "merek": mrk, "sertakan_semua": bool(sertakan_semua)},
            "baris": baris, "total_baris": len(baris), "jumlah_minus": sum(1 for b in baris if b["minus"])}
