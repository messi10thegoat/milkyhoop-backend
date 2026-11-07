"""
Profitability Reconciliation — CORE (do not delegate)

Compares profitability report totals vs P&L from journal_lines.
Law #1: Ledger Supremacy.

Architecture:
  Report revenue  → sales_invoice_items.subtotal (goods + services)
  Report COGS     → inventory_ledger.total_cost  (goods only)
  P&L revenue     → journal_lines REVENUE accounts (sudah bersih retur NK: 4-10300 Retur Penjualan)
  P&L COGS        → journal_lines COGS accounts, sumber PENJUALAN saja (HPP_JUAL_JURNAL); sisanya pnl_cogs_non_jual
  Report          → profitability_query.SQL_BARIS_JUAL / SQL_HPP_JUAL (faktur − NK, BUG-102)

Two-tier drift:
  - match:       drift <= 0.01 IDR
  - minor_drift: 0.01 < drift <= 1000 IDR (serve with warning)
  - major_drift: drift > 1000 IDR (non-blocking in v1, logged)
"""

from .profitability_query import SQL_BARIS_JUAL, SQL_HPP_JUAL


from decimal import Decimal, ROUND_HALF_UP
import logging

logger = logging.getLogger(__name__)

MINOR_THRESHOLD = Decimal("0.01")
MAJOR_THRESHOLD = Decimal("1000.00")

# BUG-102 (diukur 7 Nov, semua tenant): akun bertipe COGS juga menerima sisi PEMBELIAN (VENDOR_CREDIT 5-10300 Retur Pembelian,
# VENDOR_CREDIT_COGS, BILL/PPV 5-10400, RECLASSIFY_BILL_INVENTORY, ADJUSTMENT, PAYROLL 5-10110) -- bukan HPP penjualan. Laporan
# Profitabilitas dibandingkan dengan HPP sumber PENJUALAN saja; sisanya diungkap (pnl_cogs_non_jual), tak disembunyikan.
# Pembalik gugur lewat is_effective_journal. nyawit 1-7 Nov: HPP penjualan 9.204.808,72 == laporan; non-jual -4.775,28.
HPP_JUAL_JURNAL = ("INVOICE_FULFILLMENT", "DELIVERY_INVOICED", "CREDIT_NOTE_COGS", "SALES_INVOICE_COGS", "POS_COGS", "SALES_RECEIPT_COGS")

PNL_TOTALS_QUERY = """
SELECT
    COALESCE(SUM(
        CASE WHEN coa.account_type = 'REVENUE'
             THEN jl.credit - jl.debit ELSE 0 END
    ), 0) AS pnl_revenue,
    COALESCE(SUM(
        CASE WHEN coa.account_type = 'COGS'
             THEN jl.debit - jl.credit ELSE 0 END
    ), 0) AS pnl_cogs,
    COALESCE(SUM(
        CASE WHEN coa.account_type = 'COGS' AND je.source_type IN __HPP_JUAL_JURNAL__
             THEN jl.debit - jl.credit ELSE 0 END
    ), 0) AS pnl_cogs_jual
FROM journal_lines jl
JOIN journal_entries je ON je.id = jl.journal_id
JOIN chart_of_accounts coa ON coa.id = jl.account_id
WHERE je.tenant_id = $1
  AND is_effective_journal(je.id)  -- Rule 8.1
  AND je.journal_date BETWEEN $2 AND $3
  AND coa.account_type IN ('REVENUE', 'COGS')
""".replace("__HPP_JUAL_JURNAL__", "(" + ", ".join(f"'{x}'" for x in HPP_JUAL_JURNAL) + ")")

# BUG-102: sisi laporan = SATU sumber dengan Profitabilitas (profitability_query.SQL_BARIS_JUAL / SQL_HPP_JUAL, bersih retur NK)
REPORT_REVENUE_QUERY = """
SELECT
    COALESCE(SUM(CASE WHEN b.item_type = 'goods' THEN b.subtotal ELSE 0 END), 0) AS goods_revenue,
    COALESCE(SUM(CASE WHEN b.item_type != 'goods' THEN b.subtotal ELSE 0 END), 0) AS services_revenue
FROM (__BARIS__) b
""".replace("__BARIS__", SQL_BARIS_JUAL)

REPORT_COGS_QUERY = """
SELECT COALESCE(SUM(x.cogs), 0) AS goods_cogs FROM (__HPP__) x
""".replace("__HPP__", SQL_HPP_JUAL)


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
    pnl_cogs_total = Decimal(str(pnl_row["pnl_cogs"]))
    pnl_cogs = Decimal(str(pnl_row["pnl_cogs_jual"]))  # BUG-102: HPP sumber PENJUALAN (lihat HPP_JUAL_JURNAL)

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
        "pnl_cogs_total": float(pnl_cogs_total),
        "pnl_cogs_non_jual": float(pnl_cogs_total - pnl_cogs),  # retur pembelian/PPV di akun COGS: diungkap, bukan HPP penjualan
    }
