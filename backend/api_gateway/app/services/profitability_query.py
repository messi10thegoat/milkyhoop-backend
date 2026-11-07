"""
Profitability Query Service — CORE (do not delegate)

Architecture (Law #1 — Ledger Supremacy):
  Revenue  → sales_invoice_items.subtotal (pre-tax, per item) MINUS credit_note_items.subtotal NK posted/applied (BUG-102, SQL_BARIS_JUAL)
  COGS     → inventory_ledger.total_cost  (WAC at time of sale, per item) MINUS restock NK (ledger CREDIT_NOTE, SQL_HPP_JUAL)

Why NOT sales_invoice_items.total_cost for COGS:
  - It's a cache column that is sometimes NULL (V137 fulfillment path
    does not always backfill it)
  - inventory_ledger is the authoritative source — it records the actual
    WAC × qty movement at the moment of sale

Join path for COGS:
  inventory_ledger.source_id → sales_invoices.id
  (source_type IN ('SALES_INVOICE', 'INVOICE_FULFILLMENT') — both point
   to sales_invoices.id despite the naming)
"""

from .kosakata_ledger import KELUAR_JUAL_FAKTUR, sql_daftar


from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Tuple


# ---------------------------------------------------------------------------
# GOODS profitability — dual-source (revenue + COGS)
# ---------------------------------------------------------------------------

# Qty baris dalam SATUAN DASAR barang (6 Nov 2026, putusan pemilik langsung): dulu SUM(sii.quantity) mentah lintas satuan dengan label
# satuan dasar (nyawit Minyakita 1L: 3 karton + 20 pcs tampil "23 pcs", benar 56). Faktor = unit_conversions aktif satuan baris (sumber
# yang sama dengan kartu stok); satuan dasar / tanpa konversi = 1.
QTY_DASAR = """sii.quantity * COALESCE((
            SELECT uc.conversion_factor FROM unit_conversions uc
            WHERE uc.tenant_id = si.tenant_id AND uc.product_id = sii.item_id AND COALESCE(uc.is_active, true)
              AND lower(btrim(uc.conversion_unit)) = lower(btrim(sii.unit))
              AND lower(btrim(sii.unit)) <> lower(btrim(COALESCE(p_src.satuan, '')))
            ORDER BY uc.created_at LIMIT 1), 1)"""

# PENJUALAN BERSIH RETUR (BUG-102/106, MASTER 7 Nov 2026; pola Accurate "per barang/pelanggan = bersih retur"): SATU sumber baris
# penjualan BERTANDA untuk Profitabilitas, rekonsiliasi L/R, dan Penjualan per Pelanggan. Faktur posted tak void (+) dan Nota Kredit
# posted/applied (-) dalam periode tanggal DOKUMEN (invoice_date / credit_note_date). Dulu NK tak dikurangi -> nyawit 1-7 Nov laporan
# 10.009.400 vs Pendapatan L/R 9.912.300 (drift 97.100 = CN-2611-0001), terdeteksi check_reconciliation tapi hanya di-log.
QTY_DASAR_NK = QTY_DASAR.replace("sii.", "cni.").replace("si.tenant_id", "cn.tenant_id")

SQL_BARIS_JUAL = """
    SELECT sii.item_id AS product_id, p_src.item_type, si.customer_id, si.customer_name, si.id AS doc_id, 'faktur' AS jenis,
           (__QTY_DASAR__) AS qty_dasar, sii.quantity AS qty_baris, sii.subtotal, COALESCE(sii.discount_amount, 0) AS discount_amount,
           sii.total, sii.unit_price
    FROM sales_invoice_items sii
    JOIN sales_invoices si ON si.id = sii.invoice_id
    JOIN products p_src ON p_src.id = sii.item_id AND p_src.deleted_at IS NULL
    WHERE si.tenant_id = $1 AND si.accounting_status = 'POSTED' AND si.status != 'void'
      AND si.invoice_date >= $2 AND si.invoice_date <= $3 AND sii.item_id IS NOT NULL
    UNION ALL
    SELECT cni.item_id, p_src.item_type, cn.customer_id, cn.customer_name, cn.id, 'retur',
           -(__QTY_DASAR_NK__), -cni.quantity, -cni.subtotal, -COALESCE(cni.discount_amount, 0), -cni.total, cni.unit_price
    FROM credit_note_items cni
    JOIN credit_notes cn ON cn.id = cni.credit_note_id
    JOIN products p_src ON p_src.id = cni.item_id AND p_src.deleted_at IS NULL
    WHERE cn.tenant_id = $1 AND cn.status IN ('posted', 'applied')
      AND cn.credit_note_date >= $2 AND cn.credit_note_date <= $3 AND cni.item_id IS NOT NULL
""".replace("__QTY_DASAR__", QTY_DASAR).replace("__QTY_DASAR_NK__", QTY_DASAR_NK)

# HPP bertanda: keluar-jual faktur + alokasi SJ (+) dan HPP BALIK restock Nota Kredit (ledger CREDIT_NOTE, -) -- == akun HPP L/R.
SQL_HPP_JUAL = """
    SELECT il.product_id, il.total_cost AS cogs
    FROM inventory_ledger il
    JOIN sales_invoices si ON si.id = il.source_id
    WHERE il.tenant_id = $1 AND il.movement_type = 'SALE' AND il.source_type IN __KELUAR_JUAL_FAKTUR__
      AND si.accounting_status = 'POSTED' AND si.status != 'void' AND si.invoice_date >= $2 AND si.invoice_date <= $3
    UNION ALL
    -- R1 irisan 2: HPP barang yang dikirim lewat Surat Jalan dari pesanan = alokasi aktif (snapshot SJ), per faktur
    SELECT ifi.product_id, a.total_cost
    FROM sj_faktur_alokasi a
    JOIN sales_invoice_items sii ON sii.id = a.invoice_item_id
    JOIN sales_invoices si ON si.id = sii.invoice_id
    JOIN invoice_fulfillment_items ifi ON ifi.id = a.fulfillment_item_id
    WHERE a.tenant_id = $1 AND a.voided_at IS NULL AND a.total_cost > 0
      AND si.accounting_status = 'POSTED' AND si.status != 'void' AND si.invoice_date >= $2 AND si.invoice_date <= $3
    UNION ALL
    -- BUG-102: barang retur masuk lagi ke stok -> HPP berkurang sebesar biaya restock NK
    SELECT il.product_id, -il.total_cost
    FROM inventory_ledger il
    JOIN credit_notes cn ON cn.id = il.source_id
    WHERE il.tenant_id = $1 AND il.source_type = 'CREDIT_NOTE' AND cn.status IN ('posted', 'applied')
      AND cn.credit_note_date >= $2 AND cn.credit_note_date <= $3
""".replace("__KELUAR_JUAL_FAKTUR__", sql_daftar(KELUAR_JUAL_FAKTUR))

ITEMS_QUERY = """
WITH revenue AS (
    SELECT
        b.product_id,
        SUM(b.qty_dasar)                AS qty_sold,
        SUM(b.subtotal)                 AS revenue_gross,
        SUM(b.discount_amount)          AS total_discount,
        SUM(b.total)                    AS revenue_with_tax,
        COUNT(DISTINCT b.doc_id) FILTER (WHERE b.jenis = 'faktur') AS invoice_count,
        MIN(b.unit_price) FILTER (WHERE b.jenis = 'faktur') AS min_price,
        MAX(b.unit_price) FILTER (WHERE b.jenis = 'faktur') AS max_price,
        AVG(b.unit_price) FILTER (WHERE b.jenis = 'faktur') AS avg_price,
        COUNT(*) FILTER (WHERE b.jenis = 'faktur' AND b.unit_price = 0 AND b.qty_baris > 0) AS zero_price_lines,
        COALESCE(-SUM(b.qty_dasar) FILTER (WHERE b.jenis = 'retur'), 0) AS qty_retur,
        COALESCE(-SUM(b.subtotal) FILTER (WHERE b.jenis = 'retur'), 0)  AS retur_value
    FROM (__BARIS__) b
    WHERE b.item_type = 'goods'
    GROUP BY b.product_id
),
cogs AS (
    SELECT x.product_id, SUM(x.cogs) AS cogs FROM (__HPP__) x GROUP BY x.product_id
)
SELECT
    p.id            AS product_id,
    p.item_code,
    p.nama_produk   AS product_name,
    p.kategori      AS category,
    p.satuan        AS unit,
    r.qty_sold,
    r.revenue_gross,
    r.total_discount,
    r.revenue_with_tax,
    COALESCE(c.cogs, 0)                       AS cogs,
    r.revenue_gross - COALESCE(c.cogs, 0)     AS gross_profit,
    CASE WHEN r.revenue_gross > 0
         THEN ROUND((r.revenue_gross - COALESCE(c.cogs, 0))
              / r.revenue_gross * 100, 2)
         ELSE NULL END                        AS margin_pct,
    r.invoice_count,
    r.min_price,
    r.max_price,
    r.avg_price,
    (r.revenue_gross - COALESCE(c.cogs, 0)) < 0  AS is_loss_making,
    r.zero_price_lines > 0                        AS has_zero_price,
    r.qty_retur,
    r.retur_value
FROM revenue r
JOIN products p ON p.id = r.product_id
LEFT JOIN cogs c ON c.product_id = r.product_id
WHERE p.deleted_at IS NULL
ORDER BY {sort_clause}
LIMIT $4 OFFSET $5
""".replace("__BARIS__", SQL_BARIS_JUAL).replace("__HPP__", SQL_HPP_JUAL)


ITEMS_COUNT_QUERY = """
SELECT COUNT(DISTINCT b.product_id) AS total FROM (__BARIS__) b WHERE b.item_type = 'goods'
""".replace("__BARIS__", SQL_BARIS_JUAL)


SUMMARY_QUERY = """
WITH revenue AS (
    SELECT
        SUM(b.subtotal)  AS total_revenue_gross,
        SUM(b.discount_amount) AS total_discount,
        SUM(b.total)     AS total_revenue_with_tax,
        SUM(b.qty_dasar) AS total_qty_sold,
        COUNT(DISTINCT b.product_id) AS unique_items,
        COUNT(DISTINCT b.doc_id) FILTER (WHERE b.jenis = 'faktur') AS unique_invoices,
        COALESCE(-SUM(b.subtotal) FILTER (WHERE b.jenis = 'retur'), 0) AS total_retur
    FROM (__BARIS__) b
    WHERE b.item_type = 'goods'
),
cogs AS (
    SELECT SUM(x.cogs) AS total_cogs FROM (__HPP__) x
)
SELECT
    r.total_revenue_gross,
    r.total_discount,
    r.total_revenue_with_tax,
    COALESCE(c.total_cogs, 0)  AS total_cogs,
    r.total_revenue_gross - COALESCE(c.total_cogs, 0) AS total_gross_profit,
    CASE WHEN r.total_revenue_gross > 0
         THEN ROUND((r.total_revenue_gross - COALESCE(c.total_cogs, 0))
              / r.total_revenue_gross * 100, 2)
         ELSE NULL END AS overall_margin_pct,
    r.total_qty_sold,
    r.unique_items,
    r.unique_invoices,
    r.total_retur
FROM revenue r, cogs c
""".replace("__BARIS__", SQL_BARIS_JUAL).replace("__HPP__", SQL_HPP_JUAL)


# ---------------------------------------------------------------------------
# SERVICE revenue (no COGS — services don't go through inventory)
# ---------------------------------------------------------------------------

SERVICES_QUERY = """
SELECT
    p.id           AS product_id,
    p.item_code,
    p.nama_produk  AS product_name,
    p.kategori     AS category,
    p.satuan       AS unit,
    SUM(b.qty_baris) AS qty_sold,
    SUM(b.subtotal) AS revenue_gross
FROM (__BARIS__) b
JOIN products p ON p.id = b.product_id AND p.deleted_at IS NULL
WHERE b.item_type != 'goods'
GROUP BY p.id, p.item_code, p.nama_produk, p.kategori, p.satuan
ORDER BY SUM(b.subtotal) DESC
LIMIT $4 OFFSET $5
""".replace("__BARIS__", SQL_BARIS_JUAL)

# BUG-106: Penjualan per Pelanggan = penjualan BERSIH retur (subtotal tanpa pajak), SATU sumber dengan Profitabilitas -> Σ == Pendapatan L/R
PELANGGAN_QUERY = """
SELECT b.customer_id, COALESCE(MAX(c.nama), MAX(b.customer_name)) AS customer_name,
       SUM(b.subtotal) AS penjualan_bersih,
       COALESCE(SUM(b.subtotal) FILTER (WHERE b.jenis = 'faktur'), 0) AS penjualan,
       COALESCE(-SUM(b.subtotal) FILTER (WHERE b.jenis = 'retur'), 0) AS retur,
       COUNT(DISTINCT b.doc_id) FILTER (WHERE b.jenis = 'faktur') AS faktur_count
FROM (__BARIS__) b
LEFT JOIN customers c ON c.id = b.customer_id AND c.tenant_id = $1
GROUP BY b.customer_id
ORDER BY SUM(b.subtotal) DESC, b.customer_id
LIMIT $4 OFFSET $5
""".replace("__BARIS__", SQL_BARIS_JUAL)
PELANGGAN_RINGKAS_QUERY = """
SELECT COUNT(DISTINCT b.customer_id) AS jumlah_pelanggan, COALESCE(SUM(b.subtotal), 0) AS total_bersih,
       COALESCE(-SUM(b.subtotal) FILTER (WHERE b.jenis = 'retur'), 0) AS total_retur
FROM (__BARIS__) b
""".replace("__BARIS__", SQL_BARIS_JUAL)


# ---------------------------------------------------------------------------
# Sort options
# ---------------------------------------------------------------------------

SORT_MAP = {
    "margin_desc": "(r.revenue_gross - COALESCE(c.cogs, 0)) DESC NULLS LAST",
    "margin_asc": "(r.revenue_gross - COALESCE(c.cogs, 0)) ASC NULLS LAST",
    "revenue_desc": "r.revenue_gross DESC NULLS LAST",
    "qty_desc": "r.qty_sold DESC NULLS LAST",
    "margin_pct_desc": "CASE WHEN r.revenue_gross > 0 "
    "THEN (r.revenue_gross - COALESCE(c.cogs, 0)) / r.revenue_gross "
    "ELSE NULL END DESC NULLS LAST",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _d(v) -> Decimal:
    """Safely convert to Decimal."""
    if v is None:
        return Decimal("0")
    return Decimal(str(v))


def _r2(v) -> str:
    """Round to 2 decimals, return as string for JSON safety."""
    return str(_d(v).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def get_item_profitability(
    conn,
    tenant_id: str,
    start_date,
    end_date,
    sort_by: str = "margin_desc",
    limit: int = 50,
    offset: int = 0,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any], int]:
    """
    Realized gross margin per product (goods only).

    Revenue from sales_invoice_items, COGS from inventory_ledger.
    Caller MUST set RLS context before calling.
    """
    sort_clause = SORT_MAP.get(sort_by, SORT_MAP["margin_desc"])
    query = ITEMS_QUERY.replace("{sort_clause}", sort_clause)

    rows = await conn.fetch(query, tenant_id, start_date, end_date, limit, offset)

    items = []
    for r in rows:
        items.append(
            {
                "product_id": str(r["product_id"]),
                "item_code": r["item_code"] or "",
                "product_name": r["product_name"] or "",
                "category": r["category"] or "",
                "unit": r["unit"] or "",
                "qty_sold": _r2(r["qty_sold"]),
                "revenue_gross": _r2(r["revenue_gross"]),
                "total_discount": _r2(r["total_discount"]),
                "revenue_with_tax": _r2(r["revenue_with_tax"]),
                "cogs": _r2(r["cogs"]),
                "gross_profit": _r2(r["gross_profit"]),
                "margin_pct": _r2(r["margin_pct"])
                if r["margin_pct"] is not None
                else None,
                "invoice_count": r["invoice_count"],
                "min_price": _r2(r["min_price"]),
                "max_price": _r2(r["max_price"]),
                "avg_price": _r2(r["avg_price"]),
                "is_loss_making": bool(r["is_loss_making"]),
                "has_zero_price": bool(r["has_zero_price"]),
                "qty_retur": _r2(r["qty_retur"]),
                "retur_value": _r2(r["retur_value"]),
            }
        )

    summary_row = await conn.fetchrow(SUMMARY_QUERY, tenant_id, start_date, end_date)
    summary = {
        "total_revenue_gross": _r2(summary_row["total_revenue_gross"]),
        "total_discount": _r2(summary_row["total_discount"]),
        "total_revenue_with_tax": _r2(summary_row["total_revenue_with_tax"]),
        "total_cogs": _r2(summary_row["total_cogs"]),
        "total_gross_profit": _r2(summary_row["total_gross_profit"]),
        "overall_margin_pct": _r2(summary_row["overall_margin_pct"])
        if summary_row["overall_margin_pct"] is not None
        else None,
        "total_qty_sold": _r2(summary_row["total_qty_sold"]),
        "unique_items": summary_row["unique_items"],
        "unique_invoices": summary_row["unique_invoices"],
        "total_retur": _r2(summary_row["total_retur"]),
    }

    count_row = await conn.fetchrow(ITEMS_COUNT_QUERY, tenant_id, start_date, end_date)
    total_count = count_row["total"]

    return items, summary, total_count


async def get_service_revenue(
    conn,
    tenant_id: str,
    start_date,
    end_date,
    limit: int = 50,
    offset: int = 0,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Service item revenue (no COGS — services don't go through inventory).
    Caller MUST set RLS context before calling.
    """
    rows = await conn.fetch(
        SERVICES_QUERY, tenant_id, start_date, end_date, limit, offset
    )

    items = []
    total_revenue = Decimal("0")
    for r in rows:
        rev = _d(r["revenue_gross"])
        total_revenue += rev
        items.append(
            {
                "product_id": str(r["product_id"]),
                "item_code": r["item_code"] or "",
                "product_name": r["product_name"] or "",
                "category": r["category"] or "",
                "unit": r["unit"] or "",
                "qty_sold": _r2(r["qty_sold"]),
                "revenue_gross": _r2(rev),
            }
        )

    summary = {
        "total_revenue_gross": _r2(total_revenue),
        "item_count": len(items),
    }

    return items, summary


async def get_sales_by_customer(conn, tenant_id: str, start_date, end_date, limit: int = 50, offset: int = 0) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """BUG-106: penjualan bersih retur per pelanggan (SQL_BARIS_JUAL). Caller MUST set RLS context."""
    rows = await conn.fetch(PELANGGAN_QUERY, tenant_id, start_date, end_date, limit, offset)
    items = [{"customer_id": str(r["customer_id"]) if r["customer_id"] else None, "customer_name": r["customer_name"] or "",
              "penjualan": _r2(r["penjualan"]), "retur": _r2(r["retur"]), "penjualan_bersih": _r2(r["penjualan_bersih"]),
              "faktur_count": r["faktur_count"]} for r in rows]
    s = await conn.fetchrow(PELANGGAN_RINGKAS_QUERY, tenant_id, start_date, end_date)
    return items, {"jumlah_pelanggan": s["jumlah_pelanggan"], "total_bersih": _r2(s["total_bersih"]), "total_retur": _r2(s["total_retur"])}
