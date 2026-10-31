"""Cari pelanggan untuk Milkybook v2 (03-DATA-DAN-API §3 baris 1): nama termasuk tengah kata, toleran 1 salah ketik,
satu fakta (jumlah pesanan/penawaran + terakhir) dan skor sering dipakai. Satu kueri, <= 6 hasil.

Urutan: kelas (0 = tiap kata cocok di awal kata nama, 1 = cocok di tengah / kolom lain, 2 = hanya mirip), lalu skor
(penawaran bukan void + pesanan bukan batal, 180 hari), lalu kemiripan, lalu nama. Kueri kosong = pilihan awal (skor).
Mirip (pg_trgm word_similarity >= AMBANG_MIRIP) hanya untuk ketikan >= MIN_MIRIP huruf: "j" tidak boleh menarik
semua nama secara acak."""
from __future__ import annotations

from datetime import date
from typing import Optional

AMBANG_MIRIP = 0.6  # grapgrap 31 Okt: "tangkawarow" -> Tangkawarouw 0.83; nama lain tertinggi 0.33
MIN_MIRIP = 4
HARI_SKOR = 180
MAKS_KANDIDAT = 200

SQL = """
WITH k AS (
  SELECT c.id, c.nama, c.telepon,
    CASE WHEN (' ' || lower(c.nama)) LIKE ALL($3::text[]) THEN 0
         WHEN lower(concat_ws(' ', c.nama, c.display_name, c.company_name, c.telepon)) LIKE ALL($2::text[]) THEN 1
         ELSE 2 END AS kelas,
    CASE WHEN $5 THEN word_similarity($4, c.nama) ELSE 0 END AS mirip
  FROM customers c
  WHERE c.tenant_id = $1 AND c.is_active AND c.deleted_at IS NULL
    AND (lower(concat_ws(' ', c.nama, c.display_name, c.company_name, c.telepon)) LIKE ALL($2::text[])
         OR ($5 AND word_similarity($4, c.nama) >= $6))
  ORDER BY kelas, mirip DESC
  LIMIT $7
)
SELECT k.id, k.nama, k.telepon, k.kelas, k.mirip,
  COALESCE(q.n, 0) AS penawaran, COALESCE(o.n, 0) AS pesanan,
  GREATEST(q.akhir, o.akhir) AS terakhir,
  COALESCE(q.baru, 0) + COALESCE(o.baru, 0) AS skor
FROM k
LEFT JOIN LATERAL (
  SELECT count(*) AS n, max(quote_date) AS akhir, count(*) FILTER (WHERE quote_date >= CURRENT_DATE - $8::int) AS baru
  FROM quotes WHERE tenant_id = $1 AND customer_id = k.id AND status <> 'void') q ON true
LEFT JOIN LATERAL (
  SELECT count(*) AS n, max(order_date) AS akhir, count(*) FILTER (WHERE order_date >= CURRENT_DATE - $8::int) AS baru
  FROM sales_orders WHERE tenant_id = $1 AND customer_id = k.id AND status <> 'cancelled') o ON true
ORDER BY k.kelas, skor DESC, k.mirip DESC, k.nama
LIMIT $9
"""


def _lolos(kata: str) -> str:
    return kata.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def parameter(tenant_id: str, q: str, batas: int) -> list:
    kata = [_lolos(w) for w in (q or "").lower().split()]
    bersih = " ".join((q or "").split())
    return [tenant_id, [f"%{w}%" for w in kata], [f"% {w}%" for w in kata], bersih,
            len(bersih) >= MIN_MIRIP, AMBANG_MIRIP, MAKS_KANDIDAT, HARI_SKOR, batas]


def fakta(penawaran: int, pesanan: int) -> str:
    if pesanan:
        return f"{pesanan} pesanan"
    if penawaran:
        return f"{penawaran} penawaran"
    return "Belum ada pesanan"


def _tanggal(d: Optional[date]) -> Optional[str]:
    return d.isoformat() if d else None


def baris_ke_hasil(b) -> dict:
    return {"id": str(b["id"]), "nama": b["nama"], "telepon": b["telepon"],
            "fakta": fakta(b["penawaran"], b["pesanan"]),
            "penawaran": b["penawaran"], "pesanan": b["pesanan"], "terakhir": _tanggal(b["terakhir"]),
            "skor": b["skor"], "mirip": b["kelas"] == 2}


async def cari(conn, tenant_id: str, q: str, batas: int = 6) -> list:
    return [baris_ke_hasil(b) for b in await conn.fetch(SQL, *parameter(tenant_id, q, batas))]
