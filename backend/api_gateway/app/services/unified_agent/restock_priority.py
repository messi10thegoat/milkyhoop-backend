"""Restock priority — compound "sells well but low on stock" compute module.

READ-ONLY analytics layer. Computes the INTERSECTION of two signals over the
tenant's catalog, ranked as an actionable restock-priority list:

  * SELLS WELL  — units sold in the last 90 days, summed from
    `sales_invoice_items.quantity` joined to EFFECTIVE sales invoices only
    (`sales_invoices.status NOT IN ('draft','void')`), within 90 days of today,
    grouped per item. Items with 0 units sold in the window are EXCLUDED.
  * LOW STOCK   — current stock-on-hand <= `products.reorder_level`; if
    `reorder_level` is 0 or NULL, fall back to stock-on-hand <= 0 (habis).

Stock-on-hand uses the CANONICAL journal-derived source — the SAME expression
the `/api/inventory/low-stock` endpoint (backing `query_items_low_stock`) uses:
`SUM(inventory_ledger.quantity_in) - SUM(inventory_ledger.quantity_out)` per
product, per tenant. NOT `products.opening_stock`, NOT `warehouse_stock` (a
floored derived cache). See milkyhoop-inventory: inventory_ledger is the
quantity source of truth.

Rank by units-sold-90d DESC (best sellers first). This module does NOT mutate
any journal/ledger and is dispatched via an early override in the orchestrator
gated on the compound phrasing. No restart / commit performed by this module.

Iron Law compliance
--------------------
* Law 16 (journal/ledger-derived): stock-on-hand derives from inventory_ledger
  (the quantity source of truth, append-only); sales velocity derives from
  sales_invoice_items joined to effective invoices (draft/void excluded). No
  read from `warehouse_stock` cache, no `products.opening_stock`.
* Law 24 (Tenant isolation): app-layer `*.tenant_id = $1` WHERE filters plus the
  RLS context `SELECT set_config('app.tenant_id', $1, true)` set inside
  `conn.transaction()` (mirrors customer_sales.py).
* Law 25 (Precision): quantities kept as `Decimal` (asyncpg numeric preserved).
  NO float in computation.
* Law 32 (Pool): connection from the `get_db_pool()` singleton; never calls
  `asyncpg.connect()` or creates a pool. One `async with pool.acquire()`.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Dict, List

logger = logging.getLogger("unified_agent.restock_priority")

_ZERO = Decimal("0")


# --------------------------------------------------------------------------- #
# Compound SQL: items that SELL WELL (90d units > 0) AND are LOW ON STOCK.
#
# sold   — units sold per item in the last 90 days from EFFECTIVE invoices.
# soh    — canonical journal-derived stock-on-hand per product, same expression
#          as /api/inventory/low-stock: SUM(quantity_in) - SUM(quantity_out).
# Low-stock filter (mirrors the endpoint):
#   reorder_level > 0  -> soh <= reorder_level
#   reorder_level 0/NULL -> soh <= 0 (habis)
# Rank by units-sold-90d DESC (best sellers first).
# --------------------------------------------------------------------------- #
_RESTOCK_SQL = """
    WITH sold AS (
        SELECT sii.item_id AS product_id,
               SUM(sii.quantity) AS units_90d
        FROM sales_invoice_items sii
        JOIN sales_invoices si ON si.id = sii.invoice_id
        WHERE si.tenant_id = $1
          AND si.status NOT IN ('draft', 'void')
          AND si.invoice_date >= CURRENT_DATE - INTERVAL '90 days'
        GROUP BY sii.item_id
        HAVING SUM(sii.quantity) > 0
    ),
    stock AS (
        SELECT il.product_id,
               COALESCE(SUM(il.quantity_in) - SUM(il.quantity_out), 0) AS soh
        FROM inventory_ledger il
        WHERE il.tenant_id = $1
        GROUP BY il.product_id
    )
    SELECT p.id::text AS product_id,
           p.nama_produk AS name,
           p.satuan AS unit,
           sold.units_90d AS units_90d,
           COALESCE(stock.soh, 0) AS soh,
           COALESCE(p.reorder_level, 0) AS reorder_level
    FROM sold
    JOIN products p ON p.id = sold.product_id AND p.tenant_id = $1
    LEFT JOIN stock ON stock.product_id = sold.product_id
    WHERE COALESCE(p.track_inventory, true) = true
      AND (
            (COALESCE(NULLIF(p.reorder_level, 0), 0) > 0
             AND COALESCE(stock.soh, 0) <= COALESCE(NULLIF(p.reorder_level, 0), 0))
            OR
            (COALESCE(NULLIF(p.reorder_level, 0), 0) = 0
             AND COALESCE(stock.soh, 0) <= 0)
          )
    ORDER BY sold.units_90d DESC, p.nama_produk ASC
    LIMIT $2
"""


def _to_decimal(value: Any) -> Decimal:
    """Coerce an asyncpg numeric (Decimal/None) to a Decimal, preserving scale."""
    if value is None:
        return _ZERO
    return Decimal(value)


async def compute_restock_priority(
    tenant_id: str,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """Items that sell well (90d) AND are low on stock, ranked by 90d units DESC.

    READ-ONLY, journal/ledger-derived (Law 16). Stock-on-hand uses the canonical
    inventory_ledger expression (same as /api/inventory/low-stock); sales velocity
    uses sales_invoice_items on effective (non draft/void) invoices in a 90-day
    window. Non-sellers (0 units in window) are excluded by construction.

    Args:
        tenant_id: tenant slug (text PK, e.g. "grapgrap").
        limit: max rows to compute (default 50; renderer shows ~top 10).

    Returns:
        List of dicts (units_90d DESC): {"product_id": str, "name": str,
        "unit": str, "units_90d": Decimal, "soh": Decimal,
        "reorder_level": Decimal}. Empty list if no item qualifies.
    """
    from ..db_pool import get_db_pool  # Law 32: singleton pool

    pool = await get_db_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Law 24: RLS context (asyncpg cannot bind SET LOCAL params).
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tenant_id
            )
            rows = await conn.fetch(_RESTOCK_SQL, tenant_id, limit)

    result = [
        {
            "product_id": r["product_id"],
            "name": r["name"],
            "unit": r["unit"] or "pcs",
            "units_90d": _to_decimal(r["units_90d"]),
            "soh": _to_decimal(r["soh"]),
            "reorder_level": _to_decimal(r["reorder_level"]),
        }
        for r in rows
    ]

    logger.info(
        "restock_priority tenant=%s limit=%s -> %d items (top=%s)",
        tenant_id,
        limit,
        len(result),
        result[0]["name"] if result else None,
    )
    return result
