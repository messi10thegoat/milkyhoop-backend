"""BUG-254 (16 Nov 2026): SATU penentu pilihan barang per Kategori / Pemasok / Merek untuk Daftar Harga -- dipakai (1) resolusi filter di Ubah harga massal D3
(pratinjau == tulis: id terselesaikan dikembalikan di payload) dan (2) GET /{id}/barang-pilihan (layar "Ambil Barang", FE tanpa logika). Bersaring tenant.
Kategori = teks products.kategori (master V498 belum ada; tanpa beda huruf); pemasok = products.preferred_vendor_id; Merek = products.merek (V502, BACKEND --
kode ini HANYA menyentuh kolom merek bila filter/pilihan merek diminta atau `dengan_merek`)."""
from typing import List, Optional
from uuid import UUID

from fastapi import HTTPException

BATAS_FILTER = 50           # nilai per saringan (kategori/pemasok/merek)


def _uniq(vals, huruf_kecil: bool) -> List[str]:
    out, lihat = [], set()
    for v in vals or []:
        s = " ".join(str(v).split())
        k = s.lower() if huruf_kecil else s
        if s and k not in lihat:
            lihat.add(k)
            out.append(k if huruf_kecil else s)
    return out


def bersihkan(kategori=None, pemasok_id=None, merek=None) -> dict:
    """Saringan -> {kategori[], pemasok[], merek[]} (kategori+merek huruf kecil; pemasok UUID sah) atau 400 berkalimat."""
    f = {"kategori": _uniq(kategori, True), "merek": _uniq(merek, True), "pemasok": []}
    for p in _uniq(pemasok_id, False):
        try:
            f["pemasok"].append(str(UUID(p)))
        except ValueError:
            raise HTTPException(status_code=400, detail={"code": "FILTER_PEMASOK_TIDAK_SAH", "message": f"ID pemasok tidak sah: {p[:40]}"})
    for k, v in f.items():
        if len(v) > BATAS_FILTER:
            raise HTTPException(status_code=400, detail={"code": "FILTER_TERLALU_BANYAK", "message": f"Maksimal {BATAS_FILTER} nilai per saringan."})
    return f


def ada_filter(f: dict) -> bool:
    return bool(f["kategori"] or f["pemasok"] or f["merek"])


def syarat(f: dict, pl_id, di_daftar: Optional[bool], idx: int):
    """-> (sql "AND ...", params, idx_berikut). $1 = tenant dipegang pemanggil. di_daftar True = hanya barang yang punya baris di daftar itu; False = hanya yang belum;
    None = semua. Tanpa filter di_daftar hanya barang aktif + dijual (pilihan); dengan di_daftar=True barang di daftar apa pun statusnya (ubah massal)."""
    sql, params = "", []
    if f["kategori"]:
        sql += f" AND lower(p.kategori) = ANY(${idx}::text[])"
        params.append(f["kategori"]); idx += 1
    if f["pemasok"]:
        sql += f" AND p.preferred_vendor_id = ANY(${idx}::uuid[])"
        params.append(f["pemasok"]); idx += 1
    if f["merek"]:
        sql += f" AND lower(p.merek) = ANY(${idx}::text[])"
        params.append(f["merek"]); idx += 1
    if di_daftar is not None:
        sql += f" AND {'' if di_daftar else 'NOT '}EXISTS (SELECT 1 FROM price_list_items pli WHERE pli.price_list_id = ${idx} AND pli.item_id = p.id)"
        params.append(pl_id); idx += 1
    if di_daftar is not True:
        sql += " AND p.status = 'active' AND COALESCE(p.for_sales, true)"
    return sql, params, idx


async def resolusi(conn, tid: str, pl_id, f: dict, di_daftar: bool = True, batas: Optional[int] = None) -> List[UUID]:
    """Id barang yang memenuhi saringan (urut nama). Tanpa saringan = tak ada hasil (pemanggil memutuskan perilaku lama)."""
    if not ada_filter(f):
        return []
    s, p, _ = syarat(f, pl_id, di_daftar, 2)
    rows = await conn.fetch(f"SELECT p.id FROM products p WHERE p.tenant_id = $1 AND p.deleted_at IS NULL{s} ORDER BY p.nama_produk, p.id"
                            + (f" LIMIT {int(batas) + 1}" if batas else ""), tid, *p)
    return [r["id"] for r in rows]
