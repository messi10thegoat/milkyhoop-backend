"""K-1 (16 Nov 2026; putusan pemilik 14 Nov, rancangan BACKEND2 RANCANGAN-kategori-barang-master-20261114.md): DAFTAR STANDAR SO master Kategori barang -- TANPA migrasi.

Sumber = registry item_categories (id, name, created_at) + hitungan barang lewat teks kategori (products.kategori TETAP teks bebas; pencocokan lower(name) seperti
sekarang): jumlah_barang = barang tak terhapus (aktif + nonaktif), jumlah_aktif = status 'active'. keadaan: kosong (0 barang) | terpakai. Satu kunci ?aksi=: tanpa_barang.
aksi_tersedia (SATU bentuk): hapus = hanya bila 0 barang AKTIF (aturan SAMA dgn DELETE /items/categories + pratinjau hapus; kalimat alasan dari barang_aksi.rencana_kategori_hapus).
Aksi lain (ubah nama, nonaktif, default, gabung) = unit K-2/K-3 (butuh migrasi V498). Hanya MEMBACA."""
from typing import Optional

from ..schemas.aksi_tersedia import aksi
from . import barang_aksi as BA

AKSI = ("tanpa_barang",)
BATAS_MAKS = 200

_HITUNG = ("LEFT JOIN products p ON p.tenant_id = c.tenant_id AND lower(p.kategori) = lower(c.name) AND p.deleted_at IS NULL "
           "WHERE c.tenant_id = $1 AND ($2::text IS NULL OR c.name ILIKE '%' || $2 || '%') GROUP BY c.id")
_HAVING = " HAVING ($3::boolean IS NOT TRUE OR COUNT(p.id) = 0)"

SQL_DAFTAR = ("SELECT c.id, c.name, c.created_at, COUNT(p.id) AS jumlah_barang, COUNT(p.id) FILTER (WHERE p.status = 'active') AS jumlah_aktif "
              "FROM item_categories c " + _HITUNG + _HAVING + " ORDER BY lower(c.name), c.id LIMIT $4 OFFSET $5")
SQL_TOTAL = "SELECT COUNT(*) AS n FROM (SELECT c.id FROM item_categories c " + _HITUNG + _HAVING + ") x"
SQL_RINGKASAN = ("SELECT (SELECT COUNT(*) FROM item_categories WHERE tenant_id = $1) AS total, "
                 "(SELECT COUNT(*) FROM item_categories c WHERE c.tenant_id = $1 AND NOT EXISTS (SELECT 1 FROM products p WHERE p.tenant_id = c.tenant_id "
                 "AND lower(p.kategori) = lower(c.name) AND p.deleted_at IS NULL)) AS tanpa_barang, "
                 "(SELECT COUNT(*) FROM products WHERE tenant_id = $1 AND deleted_at IS NULL AND (kategori IS NULL OR btrim(kategori) = '')) AS barang_tanpa_kategori, "
                 "(SELECT COUNT(*) FROM products WHERE tenant_id = $1 AND deleted_at IS NULL) AS barang_total")


def position_text(jumlah_barang: int, jumlah_aktif: int) -> str:
    if jumlah_barang == 0:
        return "Belum ada barang"
    return f"{jumlah_barang} barang" + (f" · {jumlah_aktif} aktif" if jumlah_aktif != jumlah_barang else "")


def baris(r) -> dict:
    """Satu baris respons dari baris SQL_DAFTAR."""
    n, a = int(r["jumlah_barang"]), int(r["jumlah_aktif"])
    alasan_hapus = BA.rencana_kategori_hapus(r["name"], a, True)["blocks"][0]["message"] if a else None
    semua = [k for k, ya in (("tanpa_barang", n == 0),) if ya]
    return {"id": str(r["id"]), "name": r["name"], "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            "jumlah_barang": n, "jumlah_aktif": a, "keadaan": "kosong" if n == 0 else "terpakai", "position_text": position_text(n, a),
            "aksi": (semua or [None])[0], "aksi_semua": semua,
            "aksi_tersedia": {"hapus": aksi(a == 0, alasan_hapus)}}


async def muat_daftar(conn, tid: str, cari: Optional[str] = None, kunci_aksi: Optional[str] = None, limit: int = 50, offset: int = 0) -> dict:
    """-> {items, total, limit, offset, has_more}. kunci_aksi tak dikenal -> ValueError (pemanggil 422)."""
    if kunci_aksi and kunci_aksi not in AKSI:
        raise ValueError(f"aksi tidak dikenal: {kunci_aksi}")
    limit, offset = max(1, min(int(limit), BATAS_MAKS)), max(0, int(offset))
    c = (cari or "").strip() or None
    tb = True if kunci_aksi == "tanpa_barang" else None
    total = int(await conn.fetchval(SQL_TOTAL, tid, c, tb))
    rows = await conn.fetch(SQL_DAFTAR, tid, c, tb, limit, offset)
    return {"items": [baris(r) for r in rows], "total": total, "limit": limit, "offset": offset, "has_more": offset + limit < total}


async def ringkasan(conn, tid: str) -> dict:
    r = await conn.fetchrow(SQL_RINGKASAN, tid)
    return {"total_count": int(r["total"]), "tanpa_barang_count": int(r["tanpa_barang"]), "barang_tanpa_kategori_count": int(r["barang_tanpa_kategori"]),
            "barang_total": int(r["barang_total"]), "barang_tanpa_kategori_text": (f"{int(r['barang_tanpa_kategori'])} dari {int(r['barang_total'])} barang belum berkategori"
                                                                                  if int(r["barang_tanpa_kategori"]) else None)}
