"""
Profitability Reconciliation — CORE (do not delegate)

Compares profitability report totals vs P&L from journal_lines.
Law #1: Ledger Supremacy.

Architecture:
  Report revenue  → sales_invoice_items.subtotal (goods + services)
  Report COGS     → inventory_ledger.total_cost  (goods only)
  P&L revenue     → journal_lines REVENUE accounts
  P&L COGS        → journal_lines COGS accounts

Two-tier drift:
  - match:       drift <= 0.01 IDR
  - minor_drift: 0.01 < drift <= 1000 IDR (serve with warning)
  - major_drift: drift > 1000 IDR (non-blocking in v1, logged)
"""

from decimal import Decimal, ROUND_HALF_UP
import logging

logger = logging.getLogger(__name__)

MINOR_THRESHOLD = Decimal("0.01")
MAJOR_THRESHOLD = Decimal("1000.00")

PNL_TOTALS_QUERY = """
SELECT
    COALESCE(SUM(
        CASE WHEN coa.account_type = 'REVENUE'
             THEN jl.credit - jl.debit ELSE 0 END
    ), 0) AS pnl_revenue,
    COALESCE(SUM(
        CASE WHEN coa.account_type = 'COGS'
             THEN jl.debit - jl.credit ELSE 0 END
    ), 0) AS pnl_cogs
FROM journal_lines jl
JOIN journal_entries je ON je.id = jl.journal_id
JOIN chart_of_accounts coa ON coa.id = jl.account_id
WHERE je.tenant_id = $1
  AND je.status = 'POSTED'
  AND je.reversed_by_id IS NULL
  AND je.journal_date BETWEEN $2 AND $3
  AND coa.account_type IN ('REVENUE', 'COGS')
"""

REPORT_REVENUE_QUERY = """
SELECT
    COALESCE(SUM(CASE WHEN p.item_type = 'goods'
                      THEN sii.subtotal ELSE 0 END), 0) AS goods_revenue,
    COALESCE(SUM(CASE WHEN p.item_type != 'goods'
                      THEN sii.subtotal ELSE 0 END), 0) AS services_revenue
FROM sales_invoice_items sii
JOIN sales_invoices si ON si.id = sii.invoice_id
JOIN products p ON p.id = sii.item_id AND p.deleted_at IS NULL
WHERE si.tenant_id = $1
  AND si.accounting_status = 'POSTED'
  AND si.status != 'void'
  AND si.invoice_date BETWEEN $2 AND $3
  AND sii.item_id IS NOT NULL
"""

REPORT_COGS_QUERY = """
SELECT COALESCE(SUM(il.total_cost), 0) AS goods_cogs
FROM inventory_ledger il
JOIN sales_invoices si ON si.id = il.source_id
WHERE il.tenant_id = $1
  AND il.movement_type = 'SALE'
  AND il.source_type IN ('SALES_INVOICE', 'INVOICE_FULFILLMENT')
  AND si.accounting_status = 'POSTED'
  AND si.status != 'void'
  AND si.invoice_date BETWEEN $2 AND $3
"""


def _s(v: Decimal) -> str:
    return str(v.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


async def check_reconciliation(conn, tenant_id: str, start_date, end_date) -> dict:
    """
    Compare report totals vs P&L journal totals.
    Returns reconciliation result with severity.
    """
    pnl_row = await conn.fetchrow(PNL_TOTALS_QUERY, tenant_id, start_date, end_date)
    rev_row = await conn.fetchrow(REPORT_REVENUE_QUERY, tenant_id, start_date, end_date)
    cogs_row = await conn.fetchrow(REPORT_COGS_QUERY, tenant_id, start_date, end_date)

    pnl_revenue = Decimal(str(pnl_row["pnl_revenue"]))
    pnl_cogs = Decimal(str(pnl_row["pnl_cogs"]))

    goods_revenue = Decimal(str(rev_row["goods_revenue"]))
    services_revenue = Decimal(str(rev_row["services_revenue"]))
    goods_cogs = Decimal(str(cogs_row["goods_cogs"]))

    report_revenue = goods_revenue + services_revenue
    report_cogs = goods_cogs

    revenue_drift = abs(report_revenue - pnl_revenue)
    cogs_drift = abs(report_cogs - pnl_cogs)
    max_drift = max(revenue_drift, cogs_drift)

    if max_drift <= MINOR_THRESHOLD:
        severity = "match"
    elif max_drift <= MAJOR_THRESHOLD:
        severity = "minor_drift"
        logger.warning(
            "Profitability MINOR drift: tenant=%s, rev_drift=%s, cogs_drift=%s",
            tenant_id,
            revenue_drift,
            cogs_drift,
        )
    else:
        severity = "major_drift"
        logger.warning(
            "Profitability MAJOR drift: tenant=%s, rev_drift=%s, cogs_drift=%s, "
            "pnl_rev=%s, report_rev=%s, pnl_cogs=%s, report_cogs=%s",
            tenant_id,
            revenue_drift,
            cogs_drift,
            pnl_revenue,
            report_revenue,
            pnl_cogs,
            report_cogs,
        )

    return {
        "matches_pnl": severity == "match",
        "severity": severity,
        "should_block": False,
        "report_revenue": float(report_revenue),
        "report_goods_revenue": float(goods_revenue),
        "report_services_revenue": float(services_revenue),
        "pnl_revenue": float(pnl_revenue),
        "revenue_drift": float(revenue_drift),
        "report_cogs": float(report_cogs),
        "pnl_cogs": float(pnl_cogs),
        "cogs_drift": float(cogs_drift),
    }
