"""K-1/K-2 (16-17 Nov 2026; putusan pemilik 14 Nov, rancangan BACKEND2 RANCANGAN-kategori-barang-master-20261114.md): DAFTAR STANDAR SO master Kategori barang. Membaca kolom V498
(is_active, is_default, parent_id, description) -> V498 DIAPPLY DULU, baru kode (K-1 murni tanpa kolom itu; K-2 menambahnya).

Sumber = registry item_categories (id, name, created_at) + hitungan barang lewat teks kategori (products.kategori TETAP teks bebas; pencocokan lower(name) seperti
sekarang): jumlah_barang = barang tak terhapus (aktif + nonaktif), jumlah_aktif = status 'active'. keadaan: nonaktif | kosong (0 barang) | terpakai. Kunci ?aksi=: tanpa_barang | nonaktif.
aksi_tersedia (SATU bentuk): ubah (nama; selalu), nonaktifkan (aktif dan bukan bawaan), aktifkan (nonaktif), hapus = hanya bila 0 barang AKTIF (aturan SAMA dgn DELETE /items/categories + pratinjau
hapus; kalimat alasan dari barang_aksi.rencana_kategori_hapus). Aksi lain (jadikan bawaan, gabung, sub kategori) = K-3/K-4. Hanya MEMBACA."""
from typing import Optional

from ..schemas.aksi_tersedia import aksi
from . import barang_aksi as BA

AKSI = ("tanpa_barang", "nonaktif")
BATAS_MAKS = 200

_HITUNG = ("LEFT JOIN products p ON p.tenant_id = c.tenant_id AND lower(p.kategori) = lower(c.name) AND p.deleted_at IS NULL "
           "WHERE c.tenant_id = $1 AND ($2::text IS NULL OR c.name ILIKE '%' || $2 || '%') AND ($3::text IS DISTINCT FROM 'nonaktif' OR NOT c.is_active) GROUP BY c.id")
_HAVING = " HAVING ($3::text IS DISTINCT FROM 'tanpa_barang' OR COUNT(p.id) = 0)"

SQL_DAFTAR = ("SELECT c.id, c.name, c.created_at, c.is_active, c.is_default, c.parent_id, c.description, COUNT(p.id) AS jumlah_barang, COUNT(p.id) FILTER (WHERE p.status = 'active') AS jumlah_aktif "
              "FROM item_categories c " + _HITUNG + _HAVING + " ORDER BY lower(c.name), c.id LIMIT $4 OFFSET $5")
SQL_TOTAL = "SELECT COUNT(*) AS n FROM (SELECT c.id FROM item_categories c " + _HITUNG + _HAVING + ") x"
SQL_RINGKASAN = ("SELECT (SELECT COUNT(*) FROM item_categories WHERE tenant_id = $1) AS total, "
                 "(SELECT COUNT(*) FROM item_categories WHERE tenant_id = $1 AND NOT is_active) AS nonaktif, "
                 "(SELECT COUNT(*) FROM item_categories c WHERE c.tenant_id = $1 AND NOT EXISTS (SELECT 1 FROM products p WHERE p.tenant_id = c.tenant_id "
                 "AND lower(p.kategori) = lower(c.name) AND p.deleted_at IS NULL)) AS tanpa_barang, "
                 "(SELECT COUNT(*) FROM products WHERE tenant_id = $1 AND deleted_at IS NULL AND (kategori IS NULL OR btrim(kategori) = '')) AS barang_tanpa_kategori, "
                 "(SELECT COUNT(*) FROM products WHERE tenant_id = $1 AND deleted_at IS NULL) AS barang_total")


def position_text(jumlah_barang: int, jumlah_aktif: int, is_active: bool = True, is_default: bool = False) -> str:
    isi = "Belum ada barang" if jumlah_barang == 0 else (f"{jumlah_barang} barang" + (f" · {jumlah_aktif} aktif" if jumlah_aktif != jumlah_barang else ""))
    awalan = "Nonaktif" if not is_active else ("Bawaan" if is_default else None)
    return f"{awalan} · {isi}" if awalan else isi


def aksi_tersedia(n: int, a: int, nama: str, is_active: bool, is_default: bool) -> dict:
    """SATU bentuk (schemas.aksi_tersedia). Dipakai daftar (K-1) dan detail (K-2)."""
    alasan_hapus = BA.rencana_kategori_hapus(nama, a, True)["blocks"][0]["message"] if a else None
    return {"ubah": aksi(True, None),
            "nonaktifkan": aksi(is_active and not is_default, "Kategori sudah nonaktif." if not is_active else "Kategori bawaan tak bisa dinonaktifkan; jadikan kategori lain bawaan dulu."),
            "aktifkan": aksi(not is_active, "Kategori sudah aktif."),
            "hapus": aksi(a == 0, alasan_hapus)}


def baris(r) -> dict:
    """Satu baris respons dari baris SQL_DAFTAR (atau SQL_UKUR di kategori_aksi: kolom jumlah_barang/jumlah_aktif sama)."""
    n, a = int(r["jumlah_barang"]), int(r["jumlah_aktif"])
    aktif, bawaan = bool(r["is_active"]), bool(r["is_default"])
    semua = [k for k, ya in (("tanpa_barang", n == 0), ("nonaktif", not aktif)) if ya]
    return {"id": str(r["id"]), "name": r["name"], "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            "is_active": aktif, "is_default": bawaan, "parent_id": str(r["parent_id"]) if r["parent_id"] else None, "description": r["description"],
            "jumlah_barang": n, "jumlah_aktif": a, "keadaan": "nonaktif" if not aktif else ("kosong" if n == 0 else "terpakai"),
            "position_text": position_text(n, a, aktif, bawaan), "aksi": (semua or [None])[0], "aksi_semua": semua,
            "aksi_tersedia": aksi_tersedia(n, a, r["name"], aktif, bawaan)}


async def muat_daftar(conn, tid: str, cari: Optional[str] = None, kunci_aksi: Optional[str] = None, limit: int = 50, offset: int = 0) -> dict:
    """-> {items, total, limit, offset, has_more}. kunci_aksi tak dikenal -> ValueError (pemanggil 422)."""
    if kunci_aksi and kunci_aksi not in AKSI:
        raise ValueError(f"aksi tidak dikenal: {kunci_aksi}")
    limit, offset = max(1, min(int(limit), BATAS_MAKS)), max(0, int(offset))
    c = (cari or "").strip() or None
    total = int(await conn.fetchval(SQL_TOTAL, tid, c, kunci_aksi or None))
    rows = await conn.fetch(SQL_DAFTAR, tid, c, kunci_aksi or None, limit, offset)
    return {"items": [baris(r) for r in rows], "total": total, "limit": limit, "offset": offset, "has_more": offset + limit < total}


async def ringkasan(conn, tid: str) -> dict:
    r = await conn.fetchrow(SQL_RINGKASAN, tid)
    return {"total_count": int(r["total"]), "nonaktif_count": int(r["nonaktif"]), "tanpa_barang_count": int(r["tanpa_barang"]), "barang_tanpa_kategori_count": int(r["barang_tanpa_kategori"]),
            "barang_total": int(r["barang_total"]), "barang_tanpa_kategori_text": (f"{int(r['barang_tanpa_kategori'])} dari {int(r['barang_total'])} barang belum berkategori"
                                                                                  if int(r["barang_tanpa_kategori"]) else None)}
