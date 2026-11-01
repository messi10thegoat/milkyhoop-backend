"""Cari barang untuk Milkybook v2 (03-DATA-DAN-API §3 baris 2): nama/kode termasuk tengah kata, toleran 1 salah ketik,
harga master + varian ukuran per barang dasar (harga per ukuran dari master, bukan rumus) dan skor sering dipakai.

Barang dasar = produk tanpa matrix_parent_id. Varian = produk dengan matrix_parent_id = barang dasar; ukurannya dari
matrix_attributes (kunci size/ukuran). Barang tanpa varian (grapgrap 1 Nov: 0 varian) -> ukuran [] dan FE memakai harga
barang dasar untuk semua ukuran (putusan pemilik 31 Okt #3: ukuran = rincian jumlah satu baris).

Urutan sama dengan cari pelanggan: kelas (0 = tiap kata cocok di awal kata nama, 1 = di tengah / kode / sku, 2 = hanya
mirip), lalu skor (baris penawaran bukan void + pesanan bukan batal, 180 hari, termasuk variannya), lalu kemiripan, lalu
nama. Kueri kosong = pilihan awal (skor)."""
from __future__ import annotations

import json
from decimal import Decimal
from typing import Optional

from .cari_pelanggan import AMBANG_MIRIP, HARI_SKOR, MAKS_KANDIDAT, MIN_MIRIP, _lolos

KUNCI_UKURAN = ("ukuran", "size")
URUTAN_UKURAN = ["XS", "S", "M", "L", "XL", "XXL", "3XL", "4XL", "5XL"]

SQL = """
WITH k AS (
  SELECT p.id, p.nama_produk AS nama, p.item_code AS kode, p.base_unit AS satuan, p.item_type AS jenis,
    p.sales_price AS harga,
    CASE WHEN (' ' || lower(p.nama_produk)) LIKE ALL($3::text[]) THEN 0
         WHEN lower(concat_ws(' ', p.nama_produk, p.item_code, p.sku)) LIKE ALL($2::text[]) THEN 1
         ELSE 2 END AS kelas,
    CASE WHEN $5 THEN word_similarity($4, p.nama_produk) ELSE 0 END AS mirip
  FROM products p
  WHERE p.tenant_id = $1 AND p.status = 'active' AND p.deleted_at IS NULL AND p.matrix_parent_id IS NULL
    AND (lower(concat_ws(' ', p.nama_produk, p.item_code, p.sku)) LIKE ALL($2::text[])
         OR ($5 AND word_similarity($4, p.nama_produk) >= $6))
  ORDER BY kelas, mirip DESC
  LIMIT $7
)
SELECT k.id, k.nama, k.kode, k.satuan, k.jenis, k.harga, k.kelas, k.mirip,
  COALESCE(q.n, 0) + COALESCE(o.n, 0) AS skor, v.varian
FROM k
LEFT JOIN LATERAL (
  SELECT jsonb_agg(jsonb_build_object('id', x.id, 'nama', x.nama_produk, 'atribut', x.matrix_attributes,
                                      'harga', x.sales_price) ORDER BY x.nama_produk) AS varian
  FROM products x
  WHERE x.tenant_id = $1 AND x.matrix_parent_id = k.id AND x.status = 'active' AND x.deleted_at IS NULL) v ON true
LEFT JOIN LATERAL (
  SELECT count(*) AS n FROM quote_items qi JOIN quotes h ON h.id = qi.quote_id
  WHERE h.tenant_id = $1 AND h.status <> 'void' AND h.quote_date >= CURRENT_DATE - $8::int
    AND qi.item_id IN (SELECT k.id UNION ALL SELECT y.id FROM products y WHERE y.tenant_id = $1 AND y.matrix_parent_id = k.id)
) q ON true
LEFT JOIN LATERAL (
  SELECT count(*) AS n FROM sales_order_items si JOIN sales_orders h ON h.id = si.sales_order_id
  WHERE h.tenant_id = $1 AND h.status <> 'cancelled' AND h.order_date >= CURRENT_DATE - $8::int
    AND si.item_id IN (SELECT k.id UNION ALL SELECT y.id FROM products y WHERE y.tenant_id = $1 AND y.matrix_parent_id = k.id)
) o ON true
ORDER BY k.kelas, skor DESC, k.mirip DESC, k.nama
LIMIT $9
"""


def parameter(tenant_id: str, q: str, batas: int) -> list:
    kata = [_lolos(w) for w in (q or "").lower().split()]
    bersih = " ".join((q or "").split())
    return [tenant_id, [f"%{w}%" for w in kata], [f"% {w}%" for w in kata], bersih,
            len(bersih) >= MIN_MIRIP, AMBANG_MIRIP, MAKS_KANDIDAT, HARI_SKOR, batas]


def rupiah(nilai) -> Optional[int | str]:
    """Harga master: bilangan bulat bila utuh (rupiah), selain itu teks desimal persis (tanpa float)."""
    if nilai is None:
        return None
    d = Decimal(str(nilai))
    return int(d) if d == d.to_integral_value() else format(d.normalize(), "f")


def label_ukuran(atribut) -> Optional[str]:
    if isinstance(atribut, str):
        atribut = json.loads(atribut or "{}")
    for k, v in (atribut or {}).items():
        if str(k).strip().lower() in KUNCI_UKURAN and str(v).strip():
            return str(v).strip().upper()
    return None


def _kunci_urut(u: dict):
    s = u["ukuran"]
    return (URUTAN_UKURAN.index(s) if s in URUTAN_UKURAN else len(URUTAN_UKURAN), s)


def ukuran(varian) -> list:
    """Varian ber-ukuran -> [{ukuran, id, nama, harga}] urut XS..5XL lalu abjad; varian tanpa ukuran (mis. hanya warna)
    tidak masuk daftar ukuran."""
    if isinstance(varian, str):
        varian = json.loads(varian)
    hasil = []
    for v in varian or []:
        u = label_ukuran(v.get("atribut"))
        if u:
            hasil.append({"ukuran": u, "id": str(v["id"]), "nama": v.get("nama"), "harga": rupiah(v.get("harga"))})
    return sorted(hasil, key=_kunci_urut)


def baris_ke_hasil(b) -> dict:
    return {"id": str(b["id"]), "nama": b["nama"], "kode": b["kode"], "satuan": b["satuan"], "jenis": b["jenis"],
            "harga": rupiah(b["harga"]), "ukuran": ukuran(b["varian"]), "skor": b["skor"], "mirip": b["kelas"] == 2}


async def cari(conn, tenant_id: str, q: str, batas: int = 6) -> list:
    return [baris_ke_hasil(b) for b in await conn.fetch(SQL, *parameter(tenant_id, q, batas))]
