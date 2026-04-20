"""
Profitability Query Service — CORE (do not delegate)

Architecture (Law #1 — Ledger Supremacy):
  Revenue  → sales_invoice_items.subtotal (pre-tax, per item)
  COGS     → inventory_ledger.total_cost  (WAC at time of sale, per item)

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

from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Tuple

_BASE_INVOICE_FILTER = """
    si.tenant_id = $1
    AND si.accounting_status = 'POSTED'
    AND si.status != 'void'
    AND si.invoice_date >= $2
    AND si.invoice_date <= $3
"""

# ---------------------------------------------------------------------------
# GOODS profitability — dual-source (revenue + COGS)
# ---------------------------------------------------------------------------

ITEMS_QUERY = """
WITH revenue AS (
    SELECT
        sii.item_id                     AS product_id,
        SUM(sii.quantity)               AS qty_sold,
        SUM(sii.subtotal)              AS revenue_gross,
        SUM(sii.discount_amount)       AS total_discount,
        SUM(sii.total)                 AS revenue_with_tax,
        COUNT(DISTINCT sii.invoice_id) AS invoice_count,
        MIN(sii.unit_price)            AS min_price,
        MAX(sii.unit_price)            AS max_price,
        AVG(sii.unit_price)            AS avg_price,
        COUNT(*) FILTER (WHERE sii.unit_price = 0 AND sii.quantity > 0) AS zero_price_lines
    FROM sales_invoice_items sii
    JOIN sales_invoices si ON si.id = sii.invoice_id
    JOIN products p_src ON p_src.id = sii.item_id
        AND p_src.item_type = 'goods'
        AND p_src.deleted_at IS NULL
    WHERE {filter}
      AND sii.item_id IS NOT NULL
    GROUP BY sii.item_id
),
cogs AS (
    SELECT
        il.product_id,
        SUM(il.total_cost) AS cogs
    FROM inventory_ledger il
    JOIN sales_invoices si ON si.id = il.source_id
    WHERE il.tenant_id = $1
      AND il.movement_type = 'SALE'
      AND il.source_type IN ('SALES_INVOICE', 'INVOICE_FULFILLMENT')
      AND si.accounting_status = 'POSTED'
      AND si.status != 'void'
      AND si.invoice_date >= $2
      AND si.invoice_date <= $3
    GROUP BY il.product_id
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
    r.zero_price_lines > 0                        AS has_zero_price
FROM revenue r
JOIN products p ON p.id = r.product_id
LEFT JOIN cogs c ON c.product_id = r.product_id
WHERE p.deleted_at IS NULL
ORDER BY {sort_clause}
LIMIT $4 OFFSET $5
""".replace("{filter}", _BASE_INVOICE_FILTER)


ITEMS_COUNT_QUERY = """
SELECT COUNT(DISTINCT sii.item_id) AS total
FROM sales_invoice_items sii
JOIN sales_invoices si ON si.id = sii.invoice_id
JOIN products p_src ON p_src.id = sii.item_id
    AND p_src.item_type = 'goods'
    AND p_src.deleted_at IS NULL
WHERE {filter}
  AND sii.item_id IS NOT NULL
""".replace("{filter}", _BASE_INVOICE_FILTER)


SUMMARY_QUERY = """
WITH revenue AS (
    SELECT
        SUM(sii.subtotal)  AS total_revenue_gross,
        SUM(sii.discount_amount) AS total_discount,
        SUM(sii.total)     AS total_revenue_with_tax,
        SUM(sii.quantity)  AS total_qty_sold,
        COUNT(DISTINCT sii.item_id)    AS unique_items,
        COUNT(DISTINCT sii.invoice_id) AS unique_invoices
    FROM sales_invoice_items sii
    JOIN sales_invoices si ON si.id = sii.invoice_id
    JOIN products p_src ON p_src.id = sii.item_id
        AND p_src.item_type = 'goods'
        AND p_src.deleted_at IS NULL
    WHERE {filter}
      AND sii.item_id IS NOT NULL
),
cogs AS (
    SELECT SUM(il.total_cost) AS total_cogs
    FROM inventory_ledger il
    JOIN sales_invoices si ON si.id = il.source_id
    WHERE il.tenant_id = $1
      AND il.movement_type = 'SALE'
      AND il.source_type IN ('SALES_INVOICE', 'INVOICE_FULFILLMENT')
      AND si.accounting_status = 'POSTED'
      AND si.status != 'void'
      AND si.invoice_date >= $2
      AND si.invoice_date <= $3
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
    r.unique_invoices
FROM revenue r, cogs c
""".replace("{filter}", _BASE_INVOICE_FILTER)


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
    SUM(sii.quantity) AS qty_sold,
    SUM(sii.subtotal) AS revenue_gross
FROM sales_invoice_items sii
JOIN sales_invoices si ON si.id = sii.invoice_id
JOIN products p ON p.id = sii.item_id
    AND p.item_type != 'goods'
    AND p.deleted_at IS NULL
WHERE {filter}
  AND sii.item_id IS NOT NULL
GROUP BY p.id, p.item_code, p.nama_produk, p.kategori, p.satuan
ORDER BY SUM(sii.subtotal) DESC
LIMIT $4 OFFSET $5
""".replace("{filter}", _BASE_INVOICE_FILTER)


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
