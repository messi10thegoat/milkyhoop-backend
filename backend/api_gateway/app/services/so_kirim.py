"""Jumlah TERKIRIM per baris Pesanan Penjualan + nilai BELUM DIKIRIM (26 Sep 2026, audit SO FE).

Sumber terkirim = Surat Jalan (invoice_fulfillments AKTIF) — sumber yang sama dengan header
sales_orders.shipped_qty (V264 recompute_so_shipped). Kolom sales_order_items.quantity_shipped MATI
sejak V264 (tak ada penulis; terukur 26 Sep: 0 di semua 212 baris, padahal kaos punya 215 unit terkirim).

Rantai per baris: invoice_fulfillment_items.invoice_item_id -> sales_invoice_items.sales_order_item_id.
Baris faktur tanpa tautan ke baris SO tak bisa dibagikan ke baris mana pun; jumlahnya dilaporkan
TERPISAH (fulfilled_qty_unlinked) supaya Σ baris + tak-tertaut == header shipped_qty (terukur 26 Sep: 0).

Nilai belum dikirim per baris = (qty − terkirim, min 0) × line_total / qty. line_total SO = NETO
(sesudah diskon baris, SEBELUM pajak; terukur 210/212 baris = qty×harga×(1−diskon%)). Diskon header,
ongkir, dan pajak TIDAK termasuk. Decimal + ROUND_HALF_UP (Law 9/25).
"""
from decimal import ROUND_HALF_UP, Decimal

NOL = Decimal("0")
SEN = Decimal("0.01")

# Filter fulfillment AKTIF identik dengan V264 recompute_so_shipped (header shipped_qty).
_AKTIF = "f.voided_at IS NULL AND f.status <> 'voided'"

SQL_TERKIRIM_PER_BARIS = f"""
    SELECT si.sales_order_id AS so_id, sii.sales_order_item_id AS soi_id, SUM(ifi.quantity) AS terkirim
    FROM sales_invoices si
    JOIN sales_invoice_items sii ON sii.invoice_id = si.id
    JOIN invoice_fulfillment_items ifi ON ifi.invoice_item_id = sii.id
    JOIN invoice_fulfillments f ON f.id = ifi.fulfillment_id AND f.tenant_id = si.tenant_id AND {_AKTIF}
    WHERE si.tenant_id = $1 AND si.sales_order_id = ANY($2::uuid[])
    GROUP BY 1, 2
"""


def _d(v) -> Decimal:
    return Decimal(str(v)) if v is not None else NOL


async def terkirim_per_baris(conn, tenant_id: str, so_ids: list) -> tuple:
    """-> ({soi_id: Decimal terkirim}, {so_id: Decimal terkirim tanpa tautan baris})."""
    if not so_ids:
        return {}, {}
    rows = await conn.fetch(SQL_TERKIRIM_PER_BARIS, tenant_id, list(so_ids))
    per_baris, tanpa_tautan = {}, {}
    for r in rows:
        if r["soi_id"] is None:
            tanpa_tautan[r["so_id"]] = tanpa_tautan.get(r["so_id"], NOL) + _d(r["terkirim"])
        else:
            per_baris[r["soi_id"]] = _d(r["terkirim"])
    return per_baris, tanpa_tautan


def belum_dikirim(quantity, terkirim) -> Decimal:
    sisa = _d(quantity) - _d(terkirim)
    return sisa if sisa > NOL else NOL


def nilai_belum_dikirim(quantity, line_total, terkirim) -> Decimal:
    q = _d(quantity)
    if q <= NOL:
        return NOL
    return (belum_dikirim(q, terkirim) * _d(line_total) / q).quantize(SEN, rounding=ROUND_HALF_UP)


async def ringkasan_belum_dikirim(conn, tenant_id: str, status_tidak: tuple) -> dict:
    """Σ nilai belum dikirim SO selain status_tidak (satu definisi dengan detail per baris)."""
    baris = await conn.fetch(
        """SELECT so.id AS so_id, soi.id AS soi_id, soi.quantity, soi.line_total
           FROM sales_orders so JOIN sales_order_items soi ON soi.sales_order_id = so.id
           WHERE so.tenant_id = $1 AND so.status <> ALL($2::text[])""",
        tenant_id, list(status_tidak),
    )
    so_ids = sorted({r["so_id"] for r in baris})
    per_baris, _ = await terkirim_per_baris(conn, tenant_id, so_ids)
    per_so = {}
    for r in baris:
        per_so[r["so_id"]] = per_so.get(r["so_id"], NOL) + nilai_belum_dikirim(
            r["quantity"], r["line_total"], per_baris.get(r["soi_id"])
        )
    return {"total": sum(per_so.values(), NOL), "count": sum(1 for v in per_so.values() if v > NOL)}
