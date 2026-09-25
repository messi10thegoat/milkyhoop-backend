"""Q-015 (25 Sep 2026): ringkasan pesanan per pelanggan untuk saran saat mengetik (CW BUG-008).

Kenapa di server: FE tidak boleh menghitung jumlah pesanan dari daftar SO yang terpotong/berhalaman
(angka palsu). Satu kueri agregat untuk SEMUA pelanggan di halaman (bukan N+1), berpagar tenant.

Definisi (disetujui MASTER 25 Sep):
- order_count     = jumlah SO pelanggan itu selain draf & batal (status NOT IN ('draft','cancelled')) —
                    SAMA dengan top_customers (so_agregat.py); dua "jumlah pesanan" berbeda definisi = dua sumber.
- last_order_date = order_date SO non-draf non-batal terbaru.
- last_dp_percent = dp_percent SO non-draf non-batal TERBARU itu (bukan "DP default": tak ada DP default per
                    pelanggan di skema). NULL bila SO terbaru tak menyimpan persen — sengaja TIDAK
                    diturunkan dari dp_amount/total (terukur 25 Sep: 35 SO punya dp_amount>0 tanpa dp_percent).
Pelanggan tanpa SO semacam itu: 0 / None / None.
"""
from decimal import Decimal

SQL_RINGKAS_SO = """
    SELECT DISTINCT ON (so.customer_id)
           so.customer_id,
           COUNT(*) OVER (PARTITION BY so.customer_id) AS order_count,
           so.order_date AS last_order_date,
           so.dp_percent AS last_dp_percent
    FROM sales_orders so
    WHERE so.tenant_id = $1
      AND so.customer_id = ANY($2::uuid[])
      AND so.status NOT IN ('draft', 'cancelled')
    ORDER BY so.customer_id, so.order_date DESC, so.created_at DESC, so.id DESC
"""

KOSONG = {"order_count": 0, "last_order_date": None, "last_dp_percent": None}


def _persen(v):
    return None if v is None else float(Decimal(str(v)))


async def ringkas_so_pelanggan(conn, tenant_id: str, customer_ids) -> dict:
    """{str(customer_id): {order_count, last_order_date (ISO|None), last_dp_percent (float|None)}}.

    Setiap id yang diminta SELALU ada di hasil (tanpa SO -> KOSONG). Satu kueri, tanpa kueri bila daftar kosong.
    """
    ids = [str(c) for c in customer_ids]
    hasil = {c: dict(KOSONG) for c in ids}
    if not ids:
        return hasil
    rows = await conn.fetch(SQL_RINGKAS_SO, tenant_id, ids)
    for r in rows:
        cid = str(r["customer_id"])
        if cid not in hasil:
            continue
        hasil[cid] = {
            "order_count": int(r["order_count"]),
            "last_order_date": r["last_order_date"].isoformat() if r["last_order_date"] else None,
            "last_dp_percent": _persen(r["last_dp_percent"]),
        }
    return hasil
