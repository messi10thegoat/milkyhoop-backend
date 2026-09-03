"""
Tier 1: Derived Profile — ledger-as-memory.

Queries real transaction data to build business awareness context.
Injected into system prompt at session start.

IRON LAW 3.1: NO financial amounts. Only counts, names, dates, ratios.
"""

import asyncio
import logging
from typing import Optional

from cachetools import TTLCache

logger = logging.getLogger("unified_agent.tier1_profile")

_top_entities_cache = TTLCache(maxsize=500, ttl=21600)
_payment_patterns_cache = TTLCache(maxsize=500, ttl=86400)
_warehouse_defaults_cache = TTLCache(maxsize=500, ttl=86400)
_overdue_counts_cache = TTLCache(maxsize=500, ttl=300)

MIN_CUSTOMERS_OR_VENDORS = 3
MIN_PAID_INVOICES_FOR_PATTERN = 5
MIN_STOCK_MOVEMENTS = 3


async def _query_top_entities(pool, tenant_id: str) -> Optional[str]:
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tenant_id
            )

            customers = await conn.fetch(
                """
                SELECT c.nama as name, COUNT(*) as cnt
                FROM sales_invoices si
                JOIN customers c ON c.id = si.customer_id
                WHERE si.status IN ('POSTED', 'PARTIAL')
                GROUP BY c.id, c.nama
                HAVING COUNT(*) >= 1
                ORDER BY cnt DESC LIMIT 5
            """
            )

            vendors = await conn.fetch(
                """
                SELECT v.name, COUNT(*) as cnt
                FROM bills b
                JOIN vendors v ON v.id = b.vendor_id
                WHERE COALESCE(b.status_v2, 'draft') NOT IN ('draft', 'void')
                GROUP BY v.id, v.name
                HAVING COUNT(*) >= 1
                ORDER BY cnt DESC LIMIT 5
            """
            )

            items = await conn.fetch(
                """
                SELECT p.nama_produk as name, COUNT(*) as cnt
                FROM sales_invoice_items sii
                JOIN sales_invoices si ON si.id = sii.invoice_id
                JOIN products p ON p.id = sii.item_id
                WHERE si.status IN ('POSTED', 'PARTIAL') AND sii.item_id IS NOT NULL
                GROUP BY p.id, p.nama_produk
                ORDER BY cnt DESC LIMIT 10
            """
            )

    total_unique = len(customers) + len(vendors)
    if total_unique < MIN_CUSTOMERS_OR_VENDORS:
        return None

    parts = []
    if customers:
        names = ", ".join(f"{r['name']} ({r['cnt']}x)" for r in customers)
        parts.append(f"Top pelanggan: {names}")
    if vendors:
        names = ", ".join(f"{r['name']} ({r['cnt']}x)" for r in vendors)
        parts.append(f"Top vendor: {names}")
    if items:
        names = ", ".join(f"{r['name']} ({r['cnt']}x)" for r in items[:10])
        parts.append(f"Top item: {names}")

    if customers and vendors:
        cust_total = sum(r["cnt"] for r in customers)
        vend_total = sum(r["cnt"] for r in vendors)
        total = cust_total + vend_total
        if total > 0:
            pct = int(cust_total / total * 100)
            if pct >= 70:
                parts.append(f"Primary flow: sales invoices ({pct}%)")
            elif pct <= 30:
                parts.append(f"Primary flow: bills ({100 - pct}%)")

    return "\n".join(parts) if parts else None


async def _query_payment_patterns(pool, tenant_id: str) -> Optional[str]:
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tenant_id
            )

            rows = await conn.fetch(
                """
                SELECT c.nama as name,
                       ROUND(AVG(rp.payment_date - si.invoice_date)) as avg_days,
                       COUNT(*) as cnt
                FROM receive_payments rp
                JOIN receive_payment_allocations rpa ON rpa.payment_id = rp.id
                JOIN sales_invoices si ON si.id = rpa.invoice_id
                JOIN customers c ON c.id = si.customer_id
                WHERE rp.status = 'POSTED'
                GROUP BY c.id, c.nama
                HAVING COUNT(*) >= $1
                ORDER BY cnt DESC LIMIT 5
            """,
                MIN_PAID_INVOICES_FOR_PATTERN,
            )

    if not rows:
        return None

    parts = []
    for r in rows:
        days = int(r["avg_days"]) if r["avg_days"] else 0
        parts.append(f"{r['name']} rata-rata H+{days}")

    return "Pola bayar: " + ", ".join(parts) if parts else None


async def _query_warehouse_defaults(pool, tenant_id: str) -> Optional[str]:
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tenant_id
            )

            warehouse = await conn.fetchrow(
                """
                SELECT w.name, COUNT(*) as cnt
                FROM inventory_ledger il
                JOIN warehouses w ON w.id = il.warehouse_id
                GROUP BY w.id, w.name
                ORDER BY cnt DESC LIMIT 1
            """
            )

            tax_rate = await conn.fetchrow(
                """
                SELECT tax_rate, COUNT(*) as cnt
                FROM sales_invoices
                WHERE status IN ('POSTED', 'PARTIAL')
                  AND tax_rate IS NOT NULL AND tax_rate > 0
                GROUP BY tax_rate
                ORDER BY cnt DESC LIMIT 1
            """
            )

    parts = []
    if warehouse and warehouse["cnt"] >= MIN_STOCK_MOVEMENTS:
        parts.append(f"Gudang utama: {warehouse['name']}")
    if tax_rate:
        parts.append(f"Tax rate default: {int(tax_rate['tax_rate'])}%")

    return "\n".join(parts) if parts else None


async def _query_overdue_counts(pool, tenant_id: str) -> Optional[str]:
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tenant_id
            )

            inv_row = await conn.fetchrow(
                """
                SELECT COUNT(*) as cnt
                FROM sales_invoices
                WHERE status IN ('POSTED', 'PARTIAL')
                  AND due_date < CURRENT_DATE
                  AND (total_amount - COALESCE(amount_paid, 0)) > 0
            """
            )

            bill_row = await conn.fetchrow(
                """
                SELECT COUNT(*) as cnt
                FROM bills
                WHERE status IN ('POSTED', 'PARTIAL')
                  AND due_date < CURRENT_DATE
                  AND (amount - COALESCE(amount_paid, 0)) > 0
            """
            )

    inv_count = inv_row["cnt"] if inv_row else 0
    bill_count = bill_row["cnt"] if bill_row else 0

    if inv_count == 0 and bill_count == 0:
        return None

    parts = []
    if inv_count > 0:
        parts.append(f"{inv_count} faktur")
    if bill_count > 0:
        parts.append(f"{bill_count} bill")

    return f"Jatuh tempo: {', '.join(parts)}"


async def get_tier1_context(pool, tenant_id: str, ttl_override: int = None) -> str:
    caches = {
        "top_entities": (_top_entities_cache, _query_top_entities),
        "payment_patterns": (_payment_patterns_cache, _query_payment_patterns),
        "warehouse_defaults": (_warehouse_defaults_cache, _query_warehouse_defaults),
        "overdue_counts": (_overdue_counts_cache, _query_overdue_counts),
    }

    results = {}
    fetch_tasks = []

    for metric, (cache, query_fn) in caches.items():
        if ttl_override == 0:
            fetch_tasks.append((metric, query_fn))
            continue
        cached = cache.get(tenant_id)
        if cached is not None:
            results[metric] = cached
        else:
            fetch_tasks.append((metric, query_fn))

    if fetch_tasks:
        query_results = await asyncio.gather(
            *[fn(pool, tenant_id) for _, fn in fetch_tasks], return_exceptions=True
        )

        for (metric, _), result in zip(fetch_tasks, query_results):
            if isinstance(result, Exception):
                logger.warning(
                    "[TIER1] %s query failed for %s: %s", metric, tenant_id, result
                )
                results[metric] = None
            else:
                caches[metric][0][tenant_id] = result if result else ""
                results[metric] = result

    parts = []
    for metric in [
        "top_entities",
        "payment_patterns",
        "warehouse_defaults",
        "overdue_counts",
    ]:
        value = results.get(metric)
        if value:
            parts.append(value)

    if not parts:
        return ""

    context = "## PROFIL BISNIS\n" + "\n".join(parts)
    logger.info(
        "[TIER1] Profile loaded for %s: %d metrics, ~%d tokens",
        tenant_id,
        len(parts),
        len(context) // 4,
    )
    return context
