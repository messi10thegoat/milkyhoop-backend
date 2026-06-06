"""Customer sales over a period — journal-derived (Iron Law 16) compute module.

READ-ONLY analytics layer. Computes a customer's (or all customers') GROSS
BILLED amount over a date window, derived 100% from the immutable ledger
(`journal_lines` + `journal_entries`), NOT from any wrapper/transaction table.

"Gross billed" = the sum of debits posted to the customer's RECEIVABLE CoA at
billing time (`journal_entries.source_type = 'INVOICE'`). Because the AR debit
at invoice posting is the invoice grand total, this gross-billed figure INCLUDES
PPN/VAT. It is a *billed* metric (what was invoiced in the window), distinct
from *collected* (cash received) or *outstanding* (open AR).

Effective-only: reversal entries and reversed originals are both excluded
(`reversed_by_id IS NULL AND reversal_of_id IS NULL`), so a voided invoice does
not contribute to the period total once its reversal exists.

This module does NOT mutate any journal and is not (yet) wired into the
orchestrator. No restart / commit performed by this module.

Iron Law compliance
--------------------
* Law 16 (Ledger supremacy / journal-derived): every number derives from
  `journal_lines` filtered by `journal_entries.status = 'POSTED'` with explicit
  `journal_date` bounds, anchored on the RECEIVABLE CoA at
  `source_type = 'INVOICE'`. NO read from wrapper tables (sales_invoices.amount,
  receive_payments, etc.) for the amount — sales_invoices/customers are joined
  only to resolve names/ids, never to source the figure.
* Law 24 (Tenant isolation): app-layer `je.tenant_id = $1` WHERE filter plus the
  RLS context `SELECT set_config('app.tenant_id', $1, true)` set inside
  `conn.transaction()` (mirrors driver_deltas.py).
* Law 25 (Precision): amounts kept as `Decimal` (SUM(jl.debit) comes back as
  numeric/Decimal from asyncpg and is preserved). NO float in computation.
* Law 32 (Pool): connection comes from the `get_db_pool()` singleton; this
  module never calls `asyncpg.connect()` or creates a pool. One
  `async with pool.acquire()` per call.
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List

logger = logging.getLogger("unified_agent.customer_sales")

_ZERO = Decimal("0")


# --------------------------------------------------------------------------- #
# Journal-derived SQL (Law 16: gross billed = Dr RECEIVABLE at INVOICE posting)
# --------------------------------------------------------------------------- #

# Ranked customers by gross billed in [start_date, end_date].
_RANK_SQL = """
    SELECT c.id::text AS customer_id, c.nama AS customer_name,
           SUM(jl.debit) AS billed, COUNT(DISTINCT je.id) AS invoice_count
    FROM journal_entries je
    JOIN journal_lines jl ON jl.journal_id = je.id
    JOIN chart_of_accounts coa ON coa.id = jl.account_id AND coa.account_type = 'RECEIVABLE'
    JOIN sales_invoices si ON si.id::text = je.source_id::text
    JOIN customers c ON c.id::text = si.customer_id::text
    WHERE je.tenant_id = $1
      AND je.source_type = 'INVOICE'
      AND je.status = 'POSTED'
      AND je.reversed_by_id IS NULL AND je.reversal_of_id IS NULL
      AND je.journal_date >= $2::date AND je.journal_date <= $3::date
      AND jl.debit > 0
    GROUP BY c.id, c.nama
    ORDER BY billed DESC
    LIMIT $4
"""

# Single customer's gross billed in [start_date, end_date] (0 or 1 row).
_TOTAL_SQL = """
    SELECT c.id::text AS customer_id, c.nama AS customer_name,
           SUM(jl.debit) AS billed, COUNT(DISTINCT je.id) AS invoice_count
    FROM journal_entries je
    JOIN journal_lines jl ON jl.journal_id = je.id
    JOIN chart_of_accounts coa ON coa.id = jl.account_id AND coa.account_type = 'RECEIVABLE'
    JOIN sales_invoices si ON si.id::text = je.source_id::text
    JOIN customers c ON c.id::text = si.customer_id::text
    WHERE je.tenant_id = $1
      AND je.source_type = 'INVOICE'
      AND je.status = 'POSTED'
      AND je.reversed_by_id IS NULL AND je.reversal_of_id IS NULL
      AND je.journal_date >= $2::date AND je.journal_date <= $3::date
      AND jl.debit > 0
      AND si.customer_id::text = $4
    GROUP BY c.id, c.nama
"""


def _to_decimal(value: Any) -> Decimal:
    """Coerce an asyncpg numeric (Decimal/None) to a Decimal, preserving scale."""
    if value is None:
        return _ZERO
    return Decimal(value)


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
async def compute_customer_sales_rank(
    tenant_id: str,
    start_date,
    end_date,
    limit: int = 10,
) -> List[Dict[str, Any]]:
    """Rank customers by gross billed (Dr RECEIVABLE at INVOICE) over a period.

    READ-ONLY, journal-derived (Law 16). Gross billed INCLUDES PPN. Reversals
    and reversed originals are excluded (effective-only).

    Args:
        tenant_id: tenant slug (text PK, e.g. "grapgrap").
        start_date: period start (python `date` or 'YYYY-MM-DD' string).
        end_date: period end (python `date` or 'YYYY-MM-DD' string).
        limit: max customers to return (default 10).

    Returns:
        List of dicts (billed DESC): {"customer_name": str, "customer_id": str,
        "billed": Decimal, "invoice_count": int}. Empty list if none.
    """
    if isinstance(start_date, str):
        start_date = date.fromisoformat(start_date)
    if isinstance(end_date, str):
        end_date = date.fromisoformat(end_date)

    from ..db_pool import get_db_pool  # Law 32: singleton pool

    pool = await get_db_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Law 24: RLS context (asyncpg cannot bind SET LOCAL params).
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tenant_id
            )
            rows = await conn.fetch(_RANK_SQL, tenant_id, start_date, end_date, limit)

    result = [
        {
            "customer_name": r["customer_name"],
            "customer_id": r["customer_id"],
            "billed": _to_decimal(r["billed"]),
            "invoice_count": int(r["invoice_count"]),
        }
        for r in rows
    ]

    logger.info(
        "customer_sales_rank tenant=%s period=%s..%s limit=%s -> %d customers",
        tenant_id,
        start_date,
        end_date,
        limit,
        len(result),
    )
    return result


async def compute_customer_sales_total(
    tenant_id: str,
    customer_id: str,
    start_date,
    end_date,
) -> Dict[str, Any]:
    """One customer's gross billed (Dr RECEIVABLE at INVOICE) over a period.

    READ-ONLY, journal-derived (Law 16). Gross billed INCLUDES PPN. Reversals
    and reversed originals are excluded (effective-only).

    Args:
        tenant_id: tenant slug (text PK, e.g. "grapgrap").
        customer_id: target customer id (text).
        start_date: period start (python `date` or 'YYYY-MM-DD' string).
        end_date: period end (python `date` or 'YYYY-MM-DD' string).

    Returns:
        {"customer_name": str|None, "billed": Decimal, "invoice_count": int}.
        Zeros (and customer_name=None) when the customer billed nothing in the
        window — the caller already knows the name from entity resolution.
    """
    if isinstance(start_date, str):
        start_date = date.fromisoformat(start_date)
    if isinstance(end_date, str):
        end_date = date.fromisoformat(end_date)

    from ..db_pool import get_db_pool  # Law 32: singleton pool

    pool = await get_db_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Law 24: RLS context (asyncpg cannot bind SET LOCAL params).
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tenant_id
            )
            row = await conn.fetchrow(
                _TOTAL_SQL, tenant_id, start_date, end_date, customer_id
            )

    if row is None:
        result = {"customer_name": None, "billed": _ZERO, "invoice_count": 0}
    else:
        result = {
            "customer_name": row["customer_name"],
            "billed": _to_decimal(row["billed"]),
            "invoice_count": int(row["invoice_count"]),
        }

    logger.info(
        "customer_sales_total tenant=%s customer=%s period=%s..%s -> billed=%s count=%s",
        tenant_id,
        customer_id,
        start_date,
        end_date,
        result["billed"],
        result["invoice_count"],
    )
    return result
