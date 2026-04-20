"""
Dashboard Router - Aggregated KPIs for Dashboard Summary Cards
Combines data from: P&L, AR aging, AP aging, and Kas/Bank balances

Pure Ledger: All financial data queries journal_entries + journal_lines
"""
from fastapi import APIRouter, HTTPException, Request, Query
from pydantic import BaseModel
from typing import List, Optional
import logging
from datetime import datetime, timedelta, date

# Import centralized config
from ..services.db_pool import get_db_pool

logger = logging.getLogger(__name__)
router = APIRouter()


# ========================================
# Response Models
# ========================================


class LabaRugiSummary(BaseModel):
    """P&L summary for dashboard card"""

    profit: int  # Laba bersih
    pendapatan: int  # Total revenue
    pengeluaran: int  # Total expenses (HPP + beban)
    period: str  # "7 Hari" | "30 Hari" | "Bulan Ini"
    margin_persen: float = 0.0  # Profit margin %
    # Period comparison fields
    prev_profit: Optional[int] = None  # Previous period profit
    prev_pendapatan: Optional[int] = None  # Previous period revenue
    profit_change_pct: Optional[float] = None  # % change vs previous
    basis: Optional[str] = None  # 'accrual' or 'cash'


class PiutangSummary(BaseModel):
    """AR aging summary for dashboard card"""

    total: int
    customer_count: int
    jatuh_tempo: int  # Count of overdue customers
    current: int = 0
    overdue_1_30: int = 0
    overdue_31_60: int = 0
    overdue_61_90: int = 0
    overdue_90_plus: int = 0
    oldest_customer: Optional[str] = None  # Customer with oldest overdue
    oldest_days: Optional[int] = None  # Days overdue for oldest
    # Period comparison fields
    prev_total: Optional[int] = None  # Previous period total AR
    change_pct: Optional[float] = None  # % change vs previous


class HutangSummary(BaseModel):
    """AP aging summary for dashboard card"""

    total: int
    supplier_count: int
    jatuh_tempo: int  # Count of urgent (due within 7 days)
    current: int = 0
    overdue_1_30: int = 0
    overdue_31_60: int = 0
    overdue_61_90: int = 0
    overdue_90_plus: int = 0
    nearest_supplier: Optional[str] = None  # Supplier with nearest due date
    nearest_days: Optional[
        int
    ] = None  # Days until due (positive = future, negative = overdue)
    # Period comparison fields
    prev_total: Optional[int] = None  # Previous period total AP
    change_pct: Optional[float] = None  # % change vs previous


class BankAccount(BaseModel):
    """Individual bank/cash account"""

    id: str
    name: str
    account_type: str  # "cash" | "bank"
    balance: int
    account_code: str


class KasBankSummary(BaseModel):
    """Cash and bank summary for dashboard card"""

    total: int
    kas: int  # Total cash
    bank: int  # Total bank
    accounts: List[BankAccount] = []
    # Period comparison fields
    prev_total: Optional[int] = None  # Previous period total cash+bank
    change_pct: Optional[float] = None  # % change vs previous


class KPIMetrics(BaseModel):
    """DSO and DPO KPI metrics for dashboard"""

    dso: float  # Days Sales Outstanding
    dso_benchmark: int = 45  # Industry benchmark
    dso_status: str  # "good" | "ok" | "warning" | "critical"
    dpo: float  # Days Payable Outstanding
    dpo_benchmark: int = 30  # Industry benchmark
    dpo_status: str  # "good" | "ok" | "warning"


class DashboardSummaryResponse(BaseModel):
    """Combined dashboard summary response"""

    laba_rugi: LabaRugiSummary
    piutang: PiutangSummary
    hutang: HutangSummary
    kas_bank: KasBankSummary
    kpi: Optional[KPIMetrics] = None  # DSO/DPO metrics
    generated_at: str


# ========================================
# Cash Flow Trends Models
# ========================================


class CashFlowTrend(BaseModel):
    """Daily cash flow data point"""

    date: str  # 'YYYY-MM-DD'
    label: str  # Day name: 'Sen', 'Sel', 'Rab', etc.
    kas_masuk: int
    kas_keluar: int


class CashFlowTrendsResponse(BaseModel):
    """Cash flow trends response"""

    kas_masuk: int  # Total cash inflow
    kas_keluar: int  # Total cash outflow
    net_flow: int  # Net cash flow
    trends: List[CashFlowTrend]
    trx_masuk: int = 0  # Count of inflow transactions (today)
    trx_keluar: int = 0  # Count of outflow transactions (today)


# ========================================
# Top Expenses Models
# ========================================


class TopExpense(BaseModel):
    """Expense category breakdown"""

    category: str
    amount: int
    percentage: float


class TopExpensesResponse(BaseModel):
    """Top expenses response"""

    expenses: List[TopExpense]
    total: int


# ========================================
# Overdue Details Models
# ========================================


class OverdueInvoice(BaseModel):
    """Overdue AR invoice detail"""

    invoice_number: str
    customer_name: str
    due_date: str
    days_overdue: int
    outstanding: int


class OverdueInvoicesResponse(BaseModel):
    """Overdue invoices list response"""

    invoices: List[OverdueInvoice]
    total_outstanding: int
    count: int


class OverdueBill(BaseModel):
    """Overdue AP bill detail"""

    bill_number: str
    supplier_name: str
    due_date: str
    days_overdue: int
    outstanding: int


class OverdueBillsResponse(BaseModel):
    """Overdue bills list response"""

    bills: List[OverdueBill]
    total_outstanding: int
    count: int


class APReconciliationResponse(BaseModel):
    """AP Reconciliation status response"""

    in_sync: bool
    status: str  # "OK" | "WARNING"
    bills_outstanding: int
    ap_subledger: int
    gl_ap_balance: int
    variance_bills_ap: int
    variance_ap_gl: int
    issues_count: int
    issues: dict


# ========================================
# Helper Functions
# ========================================

# Indonesian day name abbreviations
DAY_NAMES = ["Sen", "Sel", "Rab", "Kam", "Jum", "Sab", "Min"]


def get_day_label(date: datetime) -> str:
    """Get Indonesian day abbreviation for a date"""
    return DAY_NAMES[date.weekday()]


def calculate_dso_status(dso: float) -> str:
    """Calculate DSO status (lower is better)"""
    if dso <= 30:
        return "good"
    elif dso <= 45:
        return "ok"
    elif dso <= 60:
        return "warning"
    else:
        return "critical"


def calculate_dpo_status(dpo: float) -> str:
    """Calculate DPO status (moderate is best)"""
    if 25 <= dpo <= 35:
        return "good"
    elif 15 <= dpo <= 45:
        return "ok"
    else:
        return "warning"


def get_days_in_period(period: str) -> int:
    """Get number of days for a period"""
    if period == "7d":
        return 7
    elif period == "30d":
        return 30
    else:  # month
        now = datetime.now()
        return now.day  # Days elapsed in current month


def get_period_date_range(period: str) -> tuple:
    """
    Get date range for period as date objects (for journal_date comparison).
    period: '7d' | '30d' | 'month'
    Returns: (start_date, end_date, period_label)
    """
    now = datetime.now()
    today = now.date()

    if period == "7d":
        start_date = today - timedelta(days=7)
        period_label = "7 Hari"
    elif period == "30d":
        start_date = today - timedelta(days=30)
        period_label = "30 Hari"
    else:  # month
        start_date = date(today.year, today.month, 1)
        period_label = "Bulan Ini"

    return start_date, today, period_label


def get_prev_period_date_range(period: str) -> tuple:
    """
    Get date range for PREVIOUS period (for comparison).
    Returns: (start_date, end_date) as date objects
    """
    now = datetime.now()
    today = now.date()

    if period == "7d":
        start_date = today - timedelta(days=14)
        end_date = today - timedelta(days=7)
    elif period == "30d":
        start_date = today - timedelta(days=60)
        end_date = today - timedelta(days=30)
    else:  # month
        first_of_current_month = date(today.year, today.month, 1)
        end_date = first_of_current_month - timedelta(days=1)
        start_date = date(end_date.year, end_date.month, 1)

    return start_date, end_date


def calc_change_pct(current: int, prev: int) -> float:
    """Calculate percentage change from previous to current period."""
    if prev > 0:
        return round((current - prev) / prev * 100, 1)
    elif current > 0:
        return 100.0  # Went from 0 to something = 100% increase
    return 0.0


# ========================================
# API Endpoints
# ========================================


# ========================================
# Combined Dashboard Endpoint (Performance)
# ========================================
# Single connection, single auth check, no pool contention.
# Replaces 5 parallel frontend fetches with 1 request.


# ========================================
# Upcoming Due & Sales Today helpers
# ========================================


async def _get_upcoming_due(request: Request):
    """Tagihan Terdekat - invoices & bills due within 14 days, not yet overdue."""
    tenant_id = request.state.user.get("tenant_id")
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, false)", tenant_id)
        rows = await conn.fetch(
            """
            WITH ar AS (
                SELECT
                    'invoice' AS type,
                    invoice_number AS number,
                    customer_name AS customer_or_vendor,
                    due_date,
                    (due_date - CURRENT_DATE) AS days_until_due,
                    outstanding
                FROM compute_ar_outstanding($1)
                WHERE outstanding > 0
                  AND due_date >= CURRENT_DATE
                  AND due_date <= CURRENT_DATE + 14
            ),
            ap AS (
                SELECT
                    'bill' AS type,
                    bill_number AS number,
                    vendor_name AS customer_or_vendor,
                    due_date,
                    (due_date - CURRENT_DATE) AS days_until_due,
                    outstanding
                FROM compute_ap_outstanding($1)
                WHERE outstanding > 0
                  AND due_date >= CURRENT_DATE
                  AND due_date <= CURRENT_DATE + 14
            )
            SELECT * FROM (SELECT * FROM ar UNION ALL SELECT * FROM ap) combined
            ORDER BY due_date ASC
            LIMIT 10
        """,
            tenant_id,
        )

        items = []
        total_outstanding = 0
        for r in rows:
            outstanding_val = int(r["outstanding"])
            total_outstanding += outstanding_val
            items.append(
                {
                    "type": r["type"],
                    "number": r["number"],
                    "customer_or_vendor": r["customer_or_vendor"],
                    "due_date": r["due_date"].isoformat() if r["due_date"] else None,
                    "days_until_due": int(r["days_until_due"])
                    if r["days_until_due"] is not None
                    else None,
                    "outstanding": outstanding_val,
                }
            )

        return {
            "items": items,
            "total_outstanding": total_outstanding,
            "count": len(items),
        }


async def _get_sales_today(
    request: Request, sales_start: str = None, sales_end: str = None
):
    """Penjualan Hari Ini - journal-derived revenue + invoice context."""
    tenant_id = request.state.user.get("tenant_id")
    _today_str = date.today().isoformat()  # noqa: F841
    s_start = date.fromisoformat(sales_start) if sales_start else date.today()
    s_end = date.fromisoformat(sales_end) if sales_end else date.today()

    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, false)", tenant_id)

        # Revenue from journal (Law 1/16)
        rev_row = await conn.fetchrow(
            """
            SELECT COALESCE(SUM(jl.credit), 0) - COALESCE(SUM(jl.debit), 0) AS total_revenue
            FROM journal_lines jl
            JOIN journal_entries je ON je.id = jl.journal_id
            JOIN chart_of_accounts coa ON coa.id = jl.account_id
            WHERE je.tenant_id = $1
              AND je.status = 'POSTED'
              AND je.reversed_by_id IS NULL
              AND coa.account_type = 'REVENUE'
              AND je.journal_date BETWEEN $2 AND $3
        """,
            str(tenant_id),
            s_start,
            s_end,
        )

        # Context counts from sales_invoices
        ctx_row = await conn.fetchrow(
            """
            SELECT COUNT(*) AS invoice_count,
                   COUNT(DISTINCT customer_name) AS customer_count
            FROM sales_invoices
            WHERE tenant_id = $1
              AND status NOT IN ('draft', 'void')
              AND invoice_date BETWEEN $2 AND $3
        """,
            str(tenant_id),
            s_start,
            s_end,
        )

        return {
            "total_revenue": int(rev_row["total_revenue"]),
            "invoice_count": int(ctx_row["invoice_count"]),
            "customer_count": int(ctx_row["customer_count"]),
            "period_start": s_start.isoformat(),
            "period_end": s_end.isoformat(),
        }


@router.get("/sales-today")
async def get_sales_today_endpoint(
    request: Request,
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
):
    """Lightweight endpoint for sales period data only."""
    return await _get_sales_today(request, sales_start=start_date, sales_end=end_date)


@router.get("/all")
async def get_dashboard_all(
    request: Request,
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    basis: Optional[str] = Query(None),
    cf_start_date: Optional[str] = Query(None),
    cf_end_date: Optional[str] = Query(None),
    exp_start_date: Optional[str] = Query(None),
    exp_end_date: Optional[str] = Query(None),
    expense_limit: int = Query(10, ge=1, le=20),
    sales_start_date: Optional[str] = Query(
        None, description="Sales period start YYYY-MM-DD (default today)"
    ),
    sales_end_date: Optional[str] = Query(
        None, description="Sales period end YYYY-MM-DD (default today)"
    ),
):
    """
    Combined dashboard endpoint — returns summary + cash flow + expenses + overdue in ONE response.
    Uses a single DB connection to avoid pool contention on concurrent requests.
    """
    import asyncio

    try:
        if not hasattr(request.state, "user") or not request.state.user:
            raise HTTPException(status_code=401, detail="Authentication required")
        tenant_id = request.state.user.get("tenant_id")
        if not tenant_id:
            raise HTTPException(status_code=401, detail="Invalid user context")

        # Delegate to individual handlers — they each acquire their own connection
        # but with a warm pool this is fast. The key win is ONE HTTP request = ONE auth check.
        # For further optimization, we could inline the SQL here on one connection,
        # but the auth/network overhead savings alone are significant.

        # Build request params for each sub-endpoint
        summary_params = {}
        if start_date:
            summary_params["start_date"] = start_date
        if end_date:
            summary_params["end_date"] = end_date
        if basis:
            summary_params["basis"] = basis

        cf_sd = cf_start_date or start_date
        cf_ed = cf_end_date or end_date

        exp_sd = exp_start_date or start_date
        exp_ed = exp_end_date or end_date

        # Run all 5 queries concurrently using asyncio.gather
        # Each gets its own pool connection — with warm pool this is ~0ms acquire
        results = await asyncio.gather(
            get_dashboard_summary(
                request, start_date=start_date, end_date=end_date, basis=basis
            ),
            get_cash_flow_trends(request, start_date=cf_sd, end_date=cf_ed),
            get_top_expenses(
                request, start_date=exp_sd, end_date=exp_ed, limit=expense_limit
            ),
            get_overdue_invoices(request),
            get_overdue_bills(request),
            _get_upcoming_due(request),
            _get_sales_today(
                request, sales_start=sales_start_date, sales_end=sales_end_date
            ),
            return_exceptions=True,
        )

        # Build combined response
        response = {}
        labels = [
            "summary",
            "cashFlow",
            "expenses",
            "overdueInvoices",
            "overdueBills",
            "upcomingDue",
            "salesToday",
        ]
        for label, result in zip(labels, results):
            if isinstance(result, Exception):
                logger.error(
                    f"Dashboard /all sub-query {label} failed: {result}",
                    exc_info=result,
                )
                response[label] = None
            else:
                # Pydantic models — convert to dict
                response[label] = result.dict() if hasattr(result, "dict") else result

        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Dashboard /all error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to load dashboard")


@router.get("/summary", response_model=DashboardSummaryResponse)
async def get_dashboard_summary(
    request: Request,
    period: str = Query("30d", regex="^(7d|30d|month)$"),
    start_date: Optional[str] = Query(
        None, description="Start date YYYY-MM-DD (overrides period)"
    ),
    end_date: Optional[str] = Query(
        None, description="End date YYYY-MM-DD (overrides period)"
    ),
    basis: Optional[str] = Query(None, description="Accounting basis: accrual or cash"),
):
    """
    Get aggregated dashboard summary for all 4 cards.

    Period options (legacy):
    - 7d: Last 7 days
    - 30d: Last 30 days (default)
    - month: Current month

    OR use start_date + end_date for fiscal period filtering.
    basis: accrual (default) or cash — affects P&L calculation.
    """
    try:
        # Get tenant_id from auth context
        if not hasattr(request.state, "user") or not request.state.user:
            raise HTTPException(status_code=401, detail="Authentication required")

        tenant_id = request.state.user.get("tenant_id")
        if not tenant_id:
            raise HTTPException(status_code=401, detail="Invalid user context")

        # If start_date/end_date provided, use them (fiscal period mode)
        if start_date and end_date:
            from datetime import date as date_type

            sd = date_type.fromisoformat(start_date)
            ed = date_type.fromisoformat(end_date)
            period_label = f"{sd.strftime('%d/%m/%Y')} - {ed.strftime('%d/%m/%Y')}"
            # Previous period = same duration before start_date
            delta = (ed - sd).days
            prev_end_date = sd - timedelta(days=1)
            prev_start_date = prev_end_date - timedelta(days=delta)
            start_date = sd
            end_date = ed
        else:
            start_date, end_date, period_label = get_period_date_range(period)
            prev_start_date, prev_end_date = get_prev_period_date_range(period)

        # Resolve accounting basis (default: tenant setting or accrual)
        effective_basis = basis  # Will resolve after DB connection if None

        pool = await get_db_pool()
        async with pool.acquire() as conn:
            await conn.execute("SET LOCAL statement_timeout = '5000'")
            # ============================
            # 1. LABA RUGI (P&L Summary)
            # Pure Ledger: uses get_revenue_by_basis / get_expenses_by_basis SQL functions
            # Supports both accrual and cash basis
            # ============================

            # Resolve basis if not provided
            if effective_basis is None:
                settings_row = await conn.fetchrow(
                    "SELECT default_report_basis FROM accounting_settings WHERE tenant_id = $1",
                    tenant_id,
                )
                effective_basis = (
                    settings_row["default_report_basis"] if settings_row else "accrual"
                )

            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, false)", tenant_id
            )

            sd = start_date if isinstance(start_date, date) else start_date
            ed = end_date if isinstance(end_date, date) else end_date

            # Revenue via basis-aware SQL function
            revenue_rows = await conn.fetch(
                "SELECT * FROM get_revenue_by_basis($1, $2, $3, $4)",
                tenant_id,
                sd,
                ed,
                effective_basis,
            )
            pendapatan = sum(int(r["total_amount"]) for r in revenue_rows)

            # Expenses via basis-aware SQL function
            expense_rows = await conn.fetch(
                "SELECT * FROM get_expenses_by_basis($1, $2, $3, $4)",
                tenant_id,
                sd,
                ed,
                effective_basis,
            )
            pengeluaran = sum(int(r["total_amount"]) for r in expense_rows)
            profit = pendapatan - pengeluaran
            margin_persen = (
                round((profit / pendapatan * 100), 1) if pendapatan > 0 else 0.0
            )

            # Previous period P&L (same basis)
            prev_revenue_rows = await conn.fetch(
                "SELECT * FROM get_revenue_by_basis($1, $2, $3, $4)",
                tenant_id,
                prev_start_date,
                prev_end_date,
                effective_basis,
            )
            prev_pendapatan = sum(int(r["total_amount"]) for r in prev_revenue_rows)
            prev_expense_rows = await conn.fetch(
                "SELECT * FROM get_expenses_by_basis($1, $2, $3, $4)",
                tenant_id,
                prev_start_date,
                prev_end_date,
                effective_basis,
            )
            prev_pengeluaran = sum(int(r["total_amount"]) for r in prev_expense_rows)
            prev_profit = prev_pendapatan - prev_pengeluaran
            profit_change_pct = calc_change_pct(profit, prev_profit)

            laba_rugi = LabaRugiSummary(
                profit=profit,
                pendapatan=pendapatan,
                pengeluaran=pengeluaran,
                period=period_label,
                margin_persen=margin_persen,
                prev_profit=prev_profit,
                prev_pendapatan=prev_pendapatan,
                profit_change_pct=profit_change_pct,
                basis=effective_basis,
            )

            # ============================
            # 2. PIUTANG (AR) - from ledger
            # Pure Ledger: queries journal_entries + journal_lines
            # ============================
            # Law 1/16: Pure ledger AR total via compute_ar_summary()
            ar_total_row = await conn.fetchrow(
                "SELECT total_outstanding AS total_piutang FROM compute_ar_summary($1)",
                tenant_id,
            )
            ar_total = int(ar_total_row["total_piutang"]) if ar_total_row else 0

            # Pure Ledger: AR aging from journal-based per-invoice outstanding
            # ARAP Rule 5/6: Use compute_ar_outstanding() — single source of truth
            ar_aging_query = """
                SELECT
                    COUNT(DISTINCT customer_name) as customer_count,
                    COUNT(CASE WHEN due_date < CURRENT_DATE THEN 1 END) as jatuh_tempo,
                    COALESCE(SUM(CASE WHEN due_date >= CURRENT_DATE THEN outstanding ELSE 0 END), 0) as current_amount,
                    COALESCE(SUM(CASE WHEN due_date < CURRENT_DATE AND due_date >= CURRENT_DATE - INTERVAL '30 days' THEN outstanding ELSE 0 END), 0) as overdue_1_30,
                    COALESCE(SUM(CASE WHEN due_date < CURRENT_DATE - INTERVAL '30 days' AND due_date >= CURRENT_DATE - INTERVAL '60 days' THEN outstanding ELSE 0 END), 0) as overdue_31_60,
                    COALESCE(SUM(CASE WHEN due_date < CURRENT_DATE - INTERVAL '60 days' AND due_date >= CURRENT_DATE - INTERVAL '90 days' THEN outstanding ELSE 0 END), 0) as overdue_61_90,
                    COALESCE(SUM(CASE WHEN due_date < CURRENT_DATE - INTERVAL '90 days' THEN outstanding ELSE 0 END), 0) as overdue_90_plus
                FROM compute_ar_outstanding($1)
            """
            ar_aging = await conn.fetchrow(ar_aging_query, tenant_id)

            # ARAP Rule 6: Use compute_ar_outstanding() instead of accounts_receivable table
            oldest_ar_query = """
                SELECT customer_name, CURRENT_DATE - due_date as days_overdue
                FROM compute_ar_outstanding($1)
                WHERE due_date < CURRENT_DATE
                ORDER BY due_date ASC LIMIT 1
            """
            oldest_ar = await conn.fetchrow(oldest_ar_query, tenant_id)

            # Previous period AR balance - Pure Ledger (Law 1/16, ARAP Rule 5/6)
            # Use account_type = 'RECEIVABLE' instead of hardcoded account_code
            prev_ar_query = """
                SELECT COALESCE(SUM(jl.debit) - SUM(jl.credit), 0) AS total_piutang
                FROM journal_lines jl
                JOIN journal_entries je ON je.id = jl.journal_id
                JOIN chart_of_accounts coa ON coa.id = jl.account_id
                WHERE coa.account_type = 'RECEIVABLE'
                  AND is_effective_journal(je.id)  -- Rule 8.1
                  AND je.tenant_id = $1
                  AND je.journal_date <= $2
            """
            prev_ar_row = await conn.fetchrow(prev_ar_query, tenant_id, prev_end_date)
            ar_prev_total = int(prev_ar_row["total_piutang"]) if prev_ar_row else 0
            ar_change_pct = calc_change_pct(ar_total, ar_prev_total)

            piutang = PiutangSummary(
                total=ar_total,
                customer_count=int(ar_aging["customer_count"]) if ar_aging else 0,
                jatuh_tempo=int(ar_aging["jatuh_tempo"]) if ar_aging else 0,
                current=int(ar_aging["current_amount"]) if ar_aging else 0,
                overdue_1_30=int(ar_aging["overdue_1_30"]) if ar_aging else 0,
                overdue_31_60=int(ar_aging["overdue_31_60"]) if ar_aging else 0,
                overdue_61_90=int(ar_aging["overdue_61_90"]) if ar_aging else 0,
                overdue_90_plus=int(ar_aging["overdue_90_plus"]) if ar_aging else 0,
                oldest_customer=oldest_ar["customer_name"] if oldest_ar else None,
                oldest_days=int(oldest_ar["days_overdue"]) if oldest_ar else None,
                prev_total=ar_prev_total,
                change_pct=ar_change_pct,
            )

            # ============================
            # 3. HUTANG (AP) - from ledger
            # Pure Ledger: queries journal_entries + journal_lines
            # ============================
            # Law 1/16: Pure ledger AP total via compute_ap_summary()
            ap_total_row = await conn.fetchrow(
                "SELECT total_outstanding AS total_hutang FROM compute_ap_summary($1)",
                tenant_id,
            )
            ap_total = int(ap_total_row["total_hutang"]) if ap_total_row else 0

            # ARAP Rule 5/6: Use compute_ap_outstanding() — single source of truth
            ap_aging_query = """
                SELECT
                    COUNT(DISTINCT vendor_name) as supplier_count,
                    COUNT(CASE WHEN due_date < CURRENT_DATE THEN 1 END) as jatuh_tempo,
                    COALESCE(SUM(CASE WHEN due_date >= CURRENT_DATE THEN outstanding ELSE 0 END), 0) as current_amount,
                    COALESCE(SUM(CASE WHEN due_date < CURRENT_DATE AND due_date >= CURRENT_DATE - INTERVAL '30 days' THEN outstanding ELSE 0 END), 0) as overdue_1_30,
                    COALESCE(SUM(CASE WHEN due_date < CURRENT_DATE - INTERVAL '30 days' AND due_date >= CURRENT_DATE - INTERVAL '60 days' THEN outstanding ELSE 0 END), 0) as overdue_31_60,
                    COALESCE(SUM(CASE WHEN due_date < CURRENT_DATE - INTERVAL '60 days' AND due_date >= CURRENT_DATE - INTERVAL '90 days' THEN outstanding ELSE 0 END), 0) as overdue_61_90,
                    COALESCE(SUM(CASE WHEN due_date < CURRENT_DATE - INTERVAL '90 days' THEN outstanding ELSE 0 END), 0) as overdue_90_plus
                FROM compute_ap_outstanding($1)
            """
            ap_aging = await conn.fetchrow(ap_aging_query, tenant_id)

            # ARAP Rule 6: nearest supplier from compute_ap_outstanding()
            nearest_ap_query = """
                SELECT vendor_name as supplier_name, due_date - CURRENT_DATE as days_until_due
                FROM compute_ap_outstanding($1)
                ORDER BY due_date ASC LIMIT 1
            """
            nearest_ap = await conn.fetchrow(nearest_ap_query, tenant_id)

            # Previous period AP balance - Pure Ledger
            # Law 1/16: Pure ledger previous period AP
            prev_ap_query = """
                SELECT COALESCE(SUM(jl.credit) - SUM(jl.debit), 0) AS total_hutang
                FROM journal_lines jl
                JOIN journal_entries je ON je.id = jl.journal_id
                JOIN chart_of_accounts coa ON coa.id = jl.account_id
                WHERE coa.account_type = 'PAYABLE'
                  AND is_effective_journal(je.id)  -- Rule 8.1
                  AND je.tenant_id = $1
                  AND je.journal_date <= $2
            """
            prev_ap_row = await conn.fetchrow(prev_ap_query, tenant_id, prev_end_date)
            ap_prev_total = int(prev_ap_row["total_hutang"]) if prev_ap_row else 0
            ap_change_pct = calc_change_pct(ap_total, ap_prev_total)

            hutang = HutangSummary(
                total=ap_total,
                supplier_count=int(ap_aging["supplier_count"]) if ap_aging else 0,
                jatuh_tempo=int(ap_aging["jatuh_tempo"]) if ap_aging else 0,
                current=int(ap_aging["current_amount"]) if ap_aging else 0,
                overdue_1_30=int(ap_aging["overdue_1_30"]) if ap_aging else 0,
                overdue_31_60=int(ap_aging["overdue_31_60"]) if ap_aging else 0,
                overdue_61_90=int(ap_aging["overdue_61_90"]) if ap_aging else 0,
                overdue_90_plus=int(ap_aging["overdue_90_plus"]) if ap_aging else 0,
                nearest_supplier=nearest_ap["supplier_name"] if nearest_ap else None,
                nearest_days=int(nearest_ap["days_until_due"]) if nearest_ap else None,
                prev_total=ap_prev_total,
                change_pct=ap_change_pct,
            )

            # ============================
            # 4. KAS & BANK
            # ============================
            # Get cash and bank account balances from CoA + balances
            kas_bank_query = """
                SELECT
                    c.account_code,
                    c.name,
                    CASE
                        WHEN c.account_code LIKE '1-101%' THEN 'cash'
                        ELSE 'bank'
                    END as account_type,
                    COALESCE(b.debit_balance - b.credit_balance, 0) as balance
                FROM chart_of_accounts c
                LEFT JOIN (
                        SELECT
                            jl.account_id,
                            COALESCE(SUM(jl.debit), 0) as debit_balance,
                            COALESCE(SUM(jl.credit), 0) as credit_balance
                        FROM journal_lines jl
                        JOIN journal_entries je ON je.id = jl.journal_id
                        WHERE je.status = 'POSTED'
                        GROUP BY jl.account_id
                    ) b ON b.account_id = c.id
                WHERE c.tenant_id = $1
                  AND c.account_code LIKE '1-1%'
                  AND (c.account_code LIKE '1-101%' OR c.account_code LIKE '1-102%')
                ORDER BY c.account_code
            """
            kas_bank_rows = await conn.fetch(kas_bank_query, tenant_id)

            accounts = []
            total_kas = 0
            total_bank = 0

            for row in kas_bank_rows:
                balance = int(row["balance"])
                account = BankAccount(
                    id=row["account_code"],
                    name=row["name"],
                    account_type=row["account_type"],
                    balance=balance,
                    account_code=row["account_code"],
                )
                accounts.append(account)

                if row["account_type"] == "cash":
                    total_kas += balance
                else:
                    total_bank += balance

            # Previous period Kas/Bank comparison
            # Pure Ledger: compute balance as of prev_end_date
            prev_kas_bank_query = """
                SELECT COALESCE(SUM(jl.debit) - SUM(jl.credit), 0) as total_balance
                FROM journal_lines jl
                JOIN journal_entries je ON je.id = jl.journal_id
                JOIN chart_of_accounts coa ON coa.id = jl.account_id
                WHERE je.tenant_id = $1 AND je.status = 'POSTED'
                  AND je.journal_date <= $2
                  AND coa.account_code LIKE '1-1%%'
                  AND (coa.account_code LIKE '1-101%%' OR coa.account_code LIKE '1-102%%')
            """
            kas_bank_total = total_kas + total_bank
            prev_kb_row = await conn.fetchrow(
                prev_kas_bank_query, tenant_id, prev_end_date
            )
            kas_bank_prev_total = (
                int(prev_kb_row["total_balance"]) if prev_kb_row else 0
            )
            kas_bank_change_pct = calc_change_pct(kas_bank_total, kas_bank_prev_total)

            kas_bank = KasBankSummary(
                total=kas_bank_total,
                kas=total_kas,
                bank=total_bank,
                accounts=accounts,
                prev_total=kas_bank_prev_total,
                change_pct=kas_bank_change_pct,
            )

            # ============================
            # 5. KPI METRICS (DSO/DPO)
            # ============================
            # Use actual date range if available, otherwise derive from period
            if start_date and end_date:
                days_in_period = (end_date - start_date).days or 1
            else:
                days_in_period = get_days_in_period(period)

            # DSO = AR / daily_revenue
            daily_revenue = pendapatan / days_in_period if days_in_period > 0 else 0
            dso = round(piutang.total / daily_revenue, 1) if daily_revenue > 0 else 0.0
            dso_status = calculate_dso_status(dso)

            # DPO = AP / daily_purchases
            daily_purchases = pengeluaran / days_in_period if days_in_period > 0 else 0
            dpo = (
                round(hutang.total / daily_purchases, 1) if daily_purchases > 0 else 0.0
            )
            dpo_status = calculate_dpo_status(dpo)

            kpi = KPIMetrics(
                dso=dso,
                dso_benchmark=45,
                dso_status=dso_status,
                dpo=dpo,
                dpo_benchmark=30,
                dpo_status=dpo_status,
            )

            logger.info(
                f"Dashboard summary generated: tenant={tenant_id}, period={period}, dso={dso}, dpo={dpo}"
            )

            return DashboardSummaryResponse(
                laba_rugi=laba_rugi,
                piutang=piutang,
                hutang=hutang,
                kas_bank=kas_bank,
                kpi=kpi,
                generated_at=datetime.now().isoformat(),
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Dashboard summary error: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500, detail="Failed to generate dashboard summary"
        )


@router.get("/piutang", response_model=PiutangSummary)
async def get_piutang_detail(
    request: Request, filter: str = Query("all", regex="^(all|overdue)$")
):
    """
    Get detailed AR aging data for Piutang panel.

    Filter options:
    - all: All customers with debt
    - overdue: Only overdue customers
    """
    try:
        if not hasattr(request.state, "user") or not request.state.user:
            raise HTTPException(status_code=401, detail="Authentication required")

        tenant_id = request.state.user.get("tenant_id")
        if not tenant_id:
            raise HTTPException(status_code=401, detail="Invalid user context")

        pool = await get_db_pool()
        async with pool.acquire() as conn:
            await conn.execute("SET LOCAL statement_timeout = '5000'")
            # Pure Ledger: total piutang via compute_ar_summary()
            total_row = await conn.fetchrow(
                "SELECT total_outstanding AS total_piutang FROM compute_ar_summary($1)",
                tenant_id,
            )
            total_piutang = int(total_row["total_piutang"]) if total_row else 0

            overdue_filter = ""
            if filter == "overdue":
                overdue_filter = "AND due_date < CURRENT_DATE"

            # Pure Ledger: AR aging from journal-based per-invoice outstanding
            extra_filter = ""
            if overdue_filter:
                extra_filter = "AND due_date < CURRENT_DATE"
            # ARAP Rule 5/6: Use compute_ar_outstanding() — single source of truth
            aging = await conn.fetchrow(
                f"""
                SELECT COUNT(DISTINCT customer_name) as customer_count,
                    COALESCE(SUM(CASE WHEN due_date >= CURRENT_DATE THEN outstanding ELSE 0 END), 0) as current_amount,
                    COALESCE(SUM(CASE WHEN due_date < CURRENT_DATE AND due_date >= CURRENT_DATE - INTERVAL '30 days' THEN outstanding ELSE 0 END), 0) as overdue_1_30,
                    COALESCE(SUM(CASE WHEN due_date < CURRENT_DATE - INTERVAL '30 days' AND due_date >= CURRENT_DATE - INTERVAL '60 days' THEN outstanding ELSE 0 END), 0) as overdue_31_60,
                    COALESCE(SUM(CASE WHEN due_date < CURRENT_DATE - INTERVAL '60 days' AND due_date >= CURRENT_DATE - INTERVAL '90 days' THEN outstanding ELSE 0 END), 0) as overdue_61_90,
                    COALESCE(SUM(CASE WHEN due_date < CURRENT_DATE - INTERVAL '90 days' THEN outstanding ELSE 0 END), 0) as overdue_90_plus,
                    COUNT(CASE WHEN due_date < CURRENT_DATE THEN 1 END) as jatuh_tempo_count
                FROM compute_ar_outstanding($1) WHERE 1=1 {extra_filter}
            """,
                tenant_id,
            )

            return PiutangSummary(
                total=total_piutang,
                customer_count=int(aging["customer_count"]) if aging else 0,
                jatuh_tempo=int(aging["jatuh_tempo_count"]) if aging else 0,
                current=int(aging["current_amount"]) if aging else 0,
                overdue_1_30=int(aging["overdue_1_30"]) if aging else 0,
                overdue_31_60=int(aging["overdue_31_60"]) if aging else 0,
                overdue_61_90=int(aging["overdue_61_90"]) if aging else 0,
                overdue_90_plus=int(aging["overdue_90_plus"]) if aging else 0,
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Piutang detail error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get piutang detail")


@router.get("/hutang", response_model=HutangSummary)
async def get_hutang_detail(
    request: Request, filter: str = Query("all", regex="^(all|overdue)$")
):
    """
    Get detailed AP aging data for Hutang panel.
    """
    try:
        if not hasattr(request.state, "user") or not request.state.user:
            raise HTTPException(status_code=401, detail="Authentication required")

        tenant_id = request.state.user.get("tenant_id")
        if not tenant_id:
            raise HTTPException(status_code=401, detail="Invalid user context")

        pool = await get_db_pool()
        async with pool.acquire() as conn:
            await conn.execute("SET LOCAL statement_timeout = '5000'")
            # Pure Ledger: total hutang via compute_ap_summary()
            total_row = await conn.fetchrow(
                "SELECT total_outstanding AS total_hutang FROM compute_ap_summary($1)",
                tenant_id,
            )
            total_hutang = int(total_row["total_hutang"]) if total_row else 0

            overdue_filter = ""
            if filter == "overdue":
                overdue_filter = "AND due_date < CURRENT_DATE"

            # Pure Ledger: AP aging from journal-based per-bill outstanding
            extra_filter = ""
            if overdue_filter:
                extra_filter = "AND due_date < CURRENT_DATE"
            aging = await conn.fetchrow(
                f"""
                WITH ap_journal_outstanding AS (
                    SELECT
                        b.id as bill_id,
                        b.invoice_number,
                        b.vendor_name,
                        b.due_date,
                        b.status,
                        b.status_v2,
                        COALESCE((
                            SELECT SUM(jl2.credit)
                            FROM journal_lines jl2
                            JOIN journal_entries je2 ON je2.id = jl2.journal_id
                            JOIN chart_of_accounts coa2 ON coa2.id = jl2.account_id
                            WHERE je2.source_id = b.id
                              AND je2.source_type = 'BILL'
                              AND je2.tenant_id = $1
                              AND je2.status = 'POSTED'
                              AND coa2.account_code LIKE '2-101%%'
                        ), 0) as bill_credit,
                        COALESCE((
                            SELECT SUM(bpa.amount_applied)
                            FROM bill_payment_allocations bpa
                            JOIN bill_payments_v2 bpv2 ON bpv2.id = bpa.payment_id
                            WHERE bpa.bill_id = b.id
                              AND bpv2.tenant_id = $1
                              AND bpv2.status = 'posted'
                              AND bpv2.journal_id IS NOT NULL
                        ), 0) as payment_debit,
                        COALESCE((
                            SELECT SUM(jl2.debit)
                            FROM journal_lines jl2
                            JOIN journal_entries je2 ON je2.id = jl2.journal_id
                            JOIN chart_of_accounts coa2 ON coa2.id = jl2.account_id
                            WHERE je2.source_id = b.id
                              AND je2.source_type = 'ADJUSTMENT'
                              AND je2.tenant_id = $1
                              AND je2.status = 'POSTED'
                              AND coa2.account_code LIKE '2-101%%'
                        ), 0) as adjustment_debit
                    FROM bills b
                    WHERE b.tenant_id = $1
                      AND b.status_v2 NOT IN ('draft', 'void', 'paid')
                ),
                ap_with_outstanding AS (
                    SELECT *,
                        (bill_credit - payment_debit - adjustment_debit) as outstanding
                    FROM ap_journal_outstanding
                    WHERE (bill_credit - payment_debit - adjustment_debit) > 0
                )
                SELECT COUNT(DISTINCT vendor_name) as supplier_count,
                    COALESCE(SUM(CASE WHEN due_date >= CURRENT_DATE THEN outstanding ELSE 0 END), 0) as current_amount,
                    COALESCE(SUM(CASE WHEN due_date < CURRENT_DATE AND due_date >= CURRENT_DATE - INTERVAL '30 days' THEN outstanding ELSE 0 END), 0) as overdue_1_30,
                    COALESCE(SUM(CASE WHEN due_date < CURRENT_DATE - INTERVAL '30 days' AND due_date >= CURRENT_DATE - INTERVAL '60 days' THEN outstanding ELSE 0 END), 0) as overdue_31_60,
                    COALESCE(SUM(CASE WHEN due_date < CURRENT_DATE - INTERVAL '60 days' AND due_date >= CURRENT_DATE - INTERVAL '90 days' THEN outstanding ELSE 0 END), 0) as overdue_61_90,
                    COALESCE(SUM(CASE WHEN due_date < CURRENT_DATE - INTERVAL '90 days' THEN outstanding ELSE 0 END), 0) as overdue_90_plus,
                    COUNT(CASE WHEN due_date < CURRENT_DATE THEN 1 END) as jatuh_tempo_count
                FROM ap_with_outstanding WHERE 1=1 {extra_filter}
            """,
                tenant_id,
            )

            return HutangSummary(
                total=total_hutang,
                supplier_count=int(aging["supplier_count"]) if aging else 0,
                jatuh_tempo=int(aging["jatuh_tempo_count"]) if aging else 0,
                current=int(aging["current_amount"]) if aging else 0,
                overdue_1_30=int(aging["overdue_1_30"]) if aging else 0,
                overdue_31_60=int(aging["overdue_31_60"]) if aging else 0,
                overdue_61_90=int(aging["overdue_61_90"]) if aging else 0,
                overdue_90_plus=int(aging["overdue_90_plus"]) if aging else 0,
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Hutang detail error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get hutang detail")


@router.get("/kas-bank", response_model=KasBankSummary)
async def get_kas_bank_detail(request: Request):
    """
    Get detailed Kas & Bank data with individual account balances.
    """
    try:
        if not hasattr(request.state, "user") or not request.state.user:
            raise HTTPException(status_code=401, detail="Authentication required")

        tenant_id = request.state.user.get("tenant_id")
        if not tenant_id:
            raise HTTPException(status_code=401, detail="Invalid user context")

        pool = await get_db_pool()
        async with pool.acquire() as conn:
            await conn.execute("SET LOCAL statement_timeout = '5000'")
            query = """
                SELECT
                    c.account_code,
                    c.name,
                    CASE
                        WHEN c.account_code LIKE '1-101%' THEN 'cash'
                        ELSE 'bank'
                    END as account_type,
                    COALESCE(b.debit_balance - b.credit_balance, 0) as balance
                FROM chart_of_accounts c
                LEFT JOIN (
                        SELECT
                            jl.account_id,
                            COALESCE(SUM(jl.debit), 0) as debit_balance,
                            COALESCE(SUM(jl.credit), 0) as credit_balance
                        FROM journal_lines jl
                        JOIN journal_entries je ON je.id = jl.journal_id
                        WHERE je.status = 'POSTED'
                        GROUP BY jl.account_id
                    ) b ON b.account_id = c.id
                WHERE c.tenant_id = $1
                  AND c.account_code LIKE '1-1%'
                  AND (c.account_code LIKE '1-101%' OR c.account_code LIKE '1-102%')
                ORDER BY c.account_code
            """
            rows = await conn.fetch(query, tenant_id)

            accounts = []
            total_kas = 0
            total_bank = 0

            for row in rows:
                balance = int(row["balance"])
                account = BankAccount(
                    id=row["account_code"],
                    name=row["name"],
                    account_type=row["account_type"],
                    balance=balance,
                    account_code=row["account_code"],
                )
                accounts.append(account)

                if row["account_type"] == "cash":
                    total_kas += balance
                else:
                    total_bank += balance

            return KasBankSummary(
                total=total_kas + total_bank,
                kas=total_kas,
                bank=total_bank,
                accounts=accounts,
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Kas bank detail error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get kas bank detail")


@router.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "service": "dashboard_router"}


# ========================================
# Cash Flow Trends Endpoint
# ========================================


@router.get("/cash-flow-trends", response_model=CashFlowTrendsResponse)
async def get_cash_flow_trends(
    request: Request,
    period: str = Query("7d", regex="^(7d|30d|month)$"),
    start_date: Optional[str] = Query(
        None, description="Start date YYYY-MM-DD (overrides period)"
    ),
    end_date: Optional[str] = Query(
        None, description="End date YYYY-MM-DD (overrides period)"
    ),
):
    """
    Get cash flow trends (kas masuk/keluar) for chart visualization.

    Period options (legacy): 7d, 30d, month
    OR use start_date + end_date for fiscal period filtering.
    Auto-aggregates by month when range > 60 days.
    """
    try:
        if not hasattr(request.state, "user") or not request.state.user:
            raise HTTPException(status_code=401, detail="Authentication required")

        tenant_id = request.state.user.get("tenant_id")
        if not tenant_id:
            raise HTTPException(status_code=401, detail="Invalid user context")

        # Calculate date range
        now = datetime.now()
        if start_date and end_date:
            from datetime import date as date_type

            cf_start = date_type.fromisoformat(start_date)
            cf_end = date_type.fromisoformat(end_date)
        else:
            if period == "7d":
                cf_start = now.date() - timedelta(days=6)
            elif period == "30d":
                cf_start = now.date() - timedelta(days=29)
            else:  # month
                cf_start = date(now.year, now.month, 1)
            cf_end = now.date()

        # Determine aggregation: monthly for ranges > 60 days, daily otherwise
        range_days = (cf_end - cf_start).days
        use_monthly = range_days > 60

        pool = await get_db_pool()
        async with pool.acquire() as conn:
            await conn.execute("SET LOCAL statement_timeout = '5000'")
            if use_monthly:
                # Monthly aggregation for fiscal year views
                query = """
                    WITH monthly_flows AS (
                        SELECT
                            DATE_TRUNC('month', je.journal_date)::date as flow_date,
                            COALESCE(SUM(CASE
                                WHEN jl.debit > 0 AND c.account_code LIKE '1-10%%'
                                THEN jl.debit ELSE 0
                            END), 0) as kas_masuk,
                            COALESCE(SUM(CASE
                                WHEN jl.credit > 0 AND c.account_code LIKE '1-10%%'
                                THEN jl.credit ELSE 0
                            END), 0) as kas_keluar
                        FROM journal_entries je
                        JOIN journal_lines jl ON jl.journal_id = je.id
                        JOIN chart_of_accounts c ON c.id = jl.account_id
                        WHERE je.tenant_id = $1
                          AND je.journal_date >= $2 AND je.journal_date <= $3
                          AND je.status = 'POSTED'
                        GROUP BY DATE_TRUNC('month', je.journal_date)
                    )
                    SELECT flow_date, kas_masuk, kas_keluar FROM monthly_flows ORDER BY flow_date
                """
                rows = await conn.fetch(query, tenant_id, cf_start, cf_end)

                trends = []
                total_masuk = 0
                total_keluar = 0
                month_names = [
                    "Jan",
                    "Feb",
                    "Mar",
                    "Apr",
                    "Mei",
                    "Jun",
                    "Jul",
                    "Agu",
                    "Sep",
                    "Okt",
                    "Nov",
                    "Des",
                ]

                flow_by_month = {row["flow_date"]: row for row in rows}

                # Generate all months in range
                current_month = date(cf_start.year, cf_start.month, 1)
                while current_month <= cf_end:
                    flow = flow_by_month.get(current_month)
                    km = int(flow["kas_masuk"]) if flow else 0
                    kk = int(flow["kas_keluar"]) if flow else 0
                    trends.append(
                        CashFlowTrend(
                            date=current_month.isoformat(),
                            label=month_names[current_month.month - 1],
                            kas_masuk=km,
                            kas_keluar=kk,
                        )
                    )
                    total_masuk += km
                    total_keluar += kk
                    # Next month
                    if current_month.month == 12:
                        current_month = date(current_month.year + 1, 1, 1)
                    else:
                        current_month = date(
                            current_month.year, current_month.month + 1, 1
                        )
            else:
                # Daily aggregation (existing behavior)
                query = """
                    WITH daily_flows AS (
                        SELECT
                            DATE(je.journal_date) as flow_date,
                            COALESCE(SUM(CASE
                                WHEN jl.debit > 0 AND c.account_code LIKE '1-10%%'
                                THEN jl.debit ELSE 0
                            END), 0) as kas_masuk,
                            COALESCE(SUM(CASE
                                WHEN jl.credit > 0 AND c.account_code LIKE '1-10%%'
                                THEN jl.credit ELSE 0
                            END), 0) as kas_keluar
                        FROM journal_entries je
                        JOIN journal_lines jl ON jl.journal_id = je.id
                        JOIN chart_of_accounts c ON c.id = jl.account_id
                        WHERE je.tenant_id = $1
                          AND je.journal_date >= $2 AND je.journal_date <= $3
                          AND je.status = 'POSTED'
                        GROUP BY DATE(je.journal_date)
                    )
                    SELECT flow_date, kas_masuk, kas_keluar FROM daily_flows ORDER BY flow_date
                """
                rows = await conn.fetch(query, tenant_id, cf_start, cf_end)

                trends = []
                total_masuk = 0
                total_keluar = 0
                flow_by_date = {row["flow_date"]: row for row in rows}

                current = cf_start
                while current <= cf_end:
                    flow = flow_by_date.get(current)
                    km = int(flow["kas_masuk"]) if flow else 0
                    kk = int(flow["kas_keluar"]) if flow else 0
                    trends.append(
                        CashFlowTrend(
                            date=current.isoformat(),
                            label=get_day_label(
                                datetime.combine(current, datetime.min.time())
                            ),
                            kas_masuk=km,
                            kas_keluar=kk,
                        )
                    )
                    total_masuk += km
                    total_keluar += kk
                    current += timedelta(days=1)

            # Query today's transaction counts
            today_trx_query = """
                SELECT
                    COUNT(DISTINCT CASE
                        WHEN jl.debit > 0 AND c.account_code LIKE '1-10%'
                        THEN je.id
                    END) as trx_masuk,
                    COUNT(DISTINCT CASE
                        WHEN jl.credit > 0 AND c.account_code LIKE '1-10%'
                        THEN je.id
                    END) as trx_keluar
                FROM journal_entries je
                JOIN journal_lines jl ON jl.journal_id = je.id
                JOIN chart_of_accounts c ON c.id = jl.account_id
                WHERE je.tenant_id = $1
                  AND je.journal_date = CURRENT_DATE
                  AND je.status = 'POSTED'
            """
            today_trx = await conn.fetchrow(today_trx_query, tenant_id)

            return CashFlowTrendsResponse(
                kas_masuk=total_masuk,
                kas_keluar=total_keluar,
                net_flow=total_masuk - total_keluar,
                trends=trends,
                trx_masuk=int(today_trx["trx_masuk"])
                if today_trx and today_trx["trx_masuk"]
                else 0,
                trx_keluar=int(today_trx["trx_keluar"])
                if today_trx and today_trx["trx_keluar"]
                else 0,
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Cash flow trends error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get cash flow trends")


# ========================================
# Top Expenses Endpoint
# ========================================


@router.get("/top-expenses", response_model=TopExpensesResponse)
async def get_top_expenses(
    request: Request,
    period: str = Query("30d", regex="^(7d|30d|month)$"),
    limit: int = Query(5, ge=1, le=20),
    start_date: Optional[str] = Query(
        None, description="Start date YYYY-MM-DD (overrides period)"
    ),
    end_date: Optional[str] = Query(
        None, description="End date YYYY-MM-DD (overrides period)"
    ),
):
    """
    Get top expense categories breakdown for period.

    Period options (legacy): 7d, 30d, month
    OR use start_date + end_date for fiscal period filtering.
    Limit: Number of top categories to return (1-20, default 5)
    """
    try:
        if not hasattr(request.state, "user") or not request.state.user:
            raise HTTPException(status_code=401, detail="Authentication required")

        tenant_id = request.state.user.get("tenant_id")
        if not tenant_id:
            raise HTTPException(status_code=401, detail="Invalid user context")

        # Calculate date range
        now = datetime.now()
        if start_date and end_date:
            from datetime import date as date_type

            te_start = date_type.fromisoformat(start_date)
            te_end = date_type.fromisoformat(end_date)
        else:
            if period == "7d":
                te_start = now.date() - timedelta(days=7)
            elif period == "30d":
                te_start = now.date() - timedelta(days=30)
            else:  # month
                te_start = date(now.year, now.month, 1)
            te_end = now.date()

        pool = await get_db_pool()
        async with pool.acquire() as conn:
            await conn.execute("SET LOCAL statement_timeout = '5000'")
            # Query expenses grouped by account category
            # Expense accounts typically start with 5-xxx or 6-xxx
            query = """
                SELECT
                    COALESCE(c.category, c.name) as category,
                    SUM(jl.debit) as amount
                FROM journal_entries je
                JOIN journal_lines jl ON jl.journal_id = je.id
                JOIN chart_of_accounts c ON c.id = jl.account_id
                WHERE je.tenant_id = $1
                  AND je.journal_date >= $2
                  AND je.journal_date <= $3
                  AND je.status = 'POSTED'
                  AND (c.account_code LIKE '5-%%' OR c.account_code LIKE '6-%%')
                  AND jl.debit > 0
                GROUP BY COALESCE(c.category, c.name)
                ORDER BY amount DESC
                LIMIT $4
            """

            rows = await conn.fetch(query, tenant_id, te_start, te_end, limit)

            # Calculate total and percentages
            total = sum(int(row["amount"]) for row in rows)

            expenses = []
            for row in rows:
                amount = int(row["amount"])
                percentage = round((amount / total * 100), 1) if total > 0 else 0

                expenses.append(
                    TopExpense(
                        category=row["category"], amount=amount, percentage=percentage
                    )
                )

            return TopExpensesResponse(expenses=expenses, total=total)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Top expenses error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get top expenses")


# ========================================
# Overdue Invoices Endpoint
# ========================================


@router.get("/overdue-invoices", response_model=OverdueInvoicesResponse)
async def get_overdue_invoices(request: Request):
    """
    Get list of overdue AR invoices (due_date < today, status != PAID).
    """
    try:
        if not hasattr(request.state, "user") or not request.state.user:
            raise HTTPException(status_code=401, detail="Authentication required")

        tenant_id = request.state.user.get("tenant_id")
        if not tenant_id:
            raise HTTPException(status_code=401, detail="Invalid user context")

        pool = await get_db_pool()
        async with pool.acquire() as conn:
            await conn.execute("SET LOCAL statement_timeout = '5000'")
            # Pure Ledger: overdue invoices from journal-based outstanding
            query = """
                -- ARAP Rule 5/6: Use compute_ar_outstanding() — single source of truth
                SELECT
                    invoice_number,
                    customer_name,
                    due_date,
                    CURRENT_DATE - due_date as days_overdue,
                    outstanding
                FROM compute_ar_outstanding($1)
                WHERE due_date < CURRENT_DATE
                ORDER BY (CURRENT_DATE - due_date) DESC, outstanding DESC
            """

            rows = await conn.fetch(query, tenant_id)

            invoices = []
            total_outstanding = 0

            for row in rows:
                outstanding = int(row["outstanding"])
                invoices.append(
                    OverdueInvoice(
                        invoice_number=row["invoice_number"],
                        customer_name=row["customer_name"],
                        due_date=row["due_date"].isoformat() if row["due_date"] else "",
                        days_overdue=int(row["days_overdue"]),
                        outstanding=outstanding,
                    )
                )
                total_outstanding += outstanding

            return OverdueInvoicesResponse(
                invoices=invoices,
                total_outstanding=total_outstanding,
                count=len(invoices),
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Overdue invoices error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get overdue invoices")


# ========================================
# Overdue Bills Endpoint
# ========================================


@router.get("/overdue-bills", response_model=OverdueBillsResponse)
async def get_overdue_bills(request: Request):
    """
    Get list of overdue AP bills (due_date < today, status != PAID).
    """
    try:
        if not hasattr(request.state, "user") or not request.state.user:
            raise HTTPException(status_code=401, detail="Authentication required")

        tenant_id = request.state.user.get("tenant_id")
        if not tenant_id:
            raise HTTPException(status_code=401, detail="Invalid user context")

        pool = await get_db_pool()
        async with pool.acquire() as conn:
            await conn.execute("SET LOCAL statement_timeout = '5000'")
            # Pure Ledger: overdue bills via compute_ap_outstanding()
            # ARAP Rule 5/6: Use compute_ap_outstanding() — single source of truth
            query = """
                SELECT
                    bill_number,
                    vendor_name AS supplier_name,
                    due_date,
                    CURRENT_DATE - due_date as days_overdue,
                    outstanding
                FROM compute_ap_outstanding($1)
                WHERE due_date < CURRENT_DATE
                ORDER BY (CURRENT_DATE - due_date) DESC, outstanding DESC
            """

            rows = await conn.fetch(query, tenant_id)

            bills = []
            total_outstanding = 0

            for row in rows:
                outstanding = int(row["outstanding"])
                bills.append(
                    OverdueBill(
                        bill_number=row["bill_number"],
                        supplier_name=row["supplier_name"],
                        due_date=row["due_date"].isoformat() if row["due_date"] else "",
                        days_overdue=int(row["days_overdue"]),
                        outstanding=outstanding,
                    )
                )
                total_outstanding += outstanding

            return OverdueBillsResponse(
                bills=bills, total_outstanding=total_outstanding, count=len(bills)
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Overdue bills error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get overdue bills")


# ========================================
# AP Reconciliation Status Endpoint
# ========================================


@router.get("/reconciliation-status", response_model=APReconciliationResponse)
async def get_reconciliation_status(request: Request):
    """
    Get AP reconciliation status.

    Golden Rule: GL_AP_Balance == SUM(bills WHERE status NOT IN ('paid', 'void'))

    This endpoint checks if Bills, AP subledger, and GL are in sync.
    Any variance indicates a data integrity issue.
    """
    try:
        if not hasattr(request.state, "user") or not request.state.user:
            raise HTTPException(status_code=401, detail="Authentication required")

        tenant_id = request.state.user.get("tenant_id")
        if not tenant_id:
            raise HTTPException(status_code=401, detail="Invalid user context")

        pool = await get_db_pool()
        async with pool.acquire() as conn:
            await conn.execute("SET LOCAL statement_timeout = '5000'")
            # Pure Ledger: Outstanding Bills total from journals
            bills_query = """
                WITH ap_journal_outstanding AS (
                    SELECT
                        b.id as bill_id,
                        b.invoice_number,
                        b.vendor_name,
                        b.due_date,
                        b.status,
                        b.status_v2,
                        COALESCE((
                            SELECT SUM(jl2.credit)
                            FROM journal_lines jl2
                            JOIN journal_entries je2 ON je2.id = jl2.journal_id
                            JOIN chart_of_accounts coa2 ON coa2.id = jl2.account_id
                            WHERE je2.source_id = b.id
                              AND je2.source_type = 'BILL'
                              AND je2.tenant_id = $1
                              AND je2.status = 'POSTED'
                              AND coa2.account_code LIKE '2-101%%'
                        ), 0) as bill_credit,
                        COALESCE((
                            SELECT SUM(bpa.amount_applied)
                            FROM bill_payment_allocations bpa
                            JOIN bill_payments_v2 bpv2 ON bpv2.id = bpa.payment_id
                            WHERE bpa.bill_id = b.id
                              AND bpv2.tenant_id = $1
                              AND bpv2.status = 'posted'
                              AND bpv2.journal_id IS NOT NULL
                        ), 0) as payment_debit,
                        COALESCE((
                            SELECT SUM(jl2.debit)
                            FROM journal_lines jl2
                            JOIN journal_entries je2 ON je2.id = jl2.journal_id
                            JOIN chart_of_accounts coa2 ON coa2.id = jl2.account_id
                            WHERE je2.source_id = b.id
                              AND je2.source_type = 'ADJUSTMENT'
                              AND je2.tenant_id = $1
                              AND je2.status = 'POSTED'
                              AND coa2.account_code LIKE '2-101%%'
                        ), 0) as adjustment_debit
                    FROM bills b
                    WHERE b.tenant_id = $1
                      AND b.status_v2 NOT IN ('draft', 'void', 'paid')
                ),
                ap_with_outstanding AS (
                    SELECT *,
                        (bill_credit - payment_debit - adjustment_debit) as outstanding
                    FROM ap_journal_outstanding
                    WHERE (bill_credit - payment_debit - adjustment_debit) > 0
                )
                SELECT COALESCE(SUM(outstanding), 0) as total
                FROM ap_with_outstanding
            """
            bills_total = await conn.fetchval(bills_query, tenant_id)

            # Pure Ledger: AP Subledger total from GL
            ap_query = """
                SELECT COALESCE(SUM(jl.credit - jl.debit), 0) as balance
                FROM journal_lines jl
                JOIN journal_entries je ON je.id = jl.journal_id
                JOIN chart_of_accounts coa ON coa.id = jl.account_id
                WHERE je.tenant_id = $1
                  AND je.status = 'POSTED'
                  AND coa.account_code LIKE '2-101%%'
            """
            ap_total = await conn.fetchval(ap_query, tenant_id)

            # GL AP Account balance (account 2-10100)
            # Formula: SUM(credit - debit) for liability account
            gl_query = """
                SELECT COALESCE(SUM(jl.credit - jl.debit), 0) as balance
                FROM journal_lines jl
                JOIN journal_entries je ON je.id = jl.journal_id
                    JOIN chart_of_accounts coa ON coa.id = jl.account_id
                WHERE je.tenant_id = $1
                  AND je.status = 'POSTED'
                  AND coa.account_code = '2-10100'  -- Law 27: read filter, resolved via JOIN
            """
            gl_balance = await conn.fetchval(gl_query, tenant_id)

            # Find issues
            # Bills without AP
            bills_no_ap = await conn.fetchval(
                """
                SELECT COUNT(*) FROM bills
                WHERE tenant_id = $1 AND ap_id IS NULL AND status NOT IN ('void', 'paid')
            """,
                tenant_id,
            )

            # Bills without Journal
            bills_no_journal = await conn.fetchval(
                """
                SELECT COUNT(*) FROM bills
                WHERE tenant_id = $1 AND journal_id IS NULL AND status NOT IN ('void', 'paid')
            """,
                tenant_id,
            )

            # AP without Bill
            ap_no_bill = await conn.fetchval(
                """
                SELECT COUNT(*) FROM accounts_payable ap
                LEFT JOIN bills b ON b.ap_id = ap.id
                WHERE ap.tenant_id = $1 AND b.id IS NULL AND ap.status NOT IN ('VOID', 'PAID')
            """,
                tenant_id,
            )

            # Amount mismatch
            amount_mismatch = await conn.fetchval(
                """
                SELECT COUNT(*) FROM bills b
                JOIN accounts_payable ap ON ap.id = b.ap_id
                WHERE b.tenant_id = $1 AND b.amount != ap.amount::BIGINT
                  AND b.status NOT IN ('void', 'paid')
            """,
                tenant_id,
            )

            # Calculate variances
            variance_bills_ap = int(bills_total) - int(ap_total or 0)
            variance_ap_gl = int(ap_total or 0) - int(gl_balance or 0)

            # Check if in sync (int amounts, exact match)
            is_in_sync = variance_bills_ap == 0 and variance_ap_gl == 0

            total_issues = bills_no_ap + bills_no_journal + ap_no_bill + amount_mismatch

            return APReconciliationResponse(
                in_sync=is_in_sync and total_issues == 0,
                status="OK" if (is_in_sync and total_issues == 0) else "WARNING",
                bills_outstanding=int(bills_total),
                ap_subledger=int(ap_total or 0),
                gl_ap_balance=int(gl_balance or 0),
                variance_bills_ap=variance_bills_ap,
                variance_ap_gl=variance_ap_gl,
                issues_count=total_issues,
                issues={
                    "bills_without_ap": bills_no_ap,
                    "bills_without_journal": bills_no_journal,
                    "ap_without_bill": ap_no_bill,
                    "amount_mismatch": amount_mismatch,
                },
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Reconciliation status error: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500, detail="Failed to get reconciliation status"
        )


# ========================================
# Cash Flow Projection Models
# ========================================


class CashProjectionDay(BaseModel):
    """Daily cash projection data point"""

    date: str  # YYYY-MM-DD
    label: str  # Sen, Sel, etc.
    projected_in: int  # Expected cash in (from AR due dates)
    projected_out: int  # Expected cash out (from AP due dates)
    projected_balance: int  # Cumulative balance


class CashProjectionResponse(BaseModel):
    """Cash flow projection response"""

    current_balance: int  # Todays kas + bank
    projected_balance_7d: int  # Balance after 7 days
    total_expected_in: int  # Total AR coming due in 7 days
    total_expected_out: int  # Total AP coming due in 7 days
    net_projection: int  # Expected change
    projections: List[CashProjectionDay]  # Daily breakdown
    warning: Optional[str] = None  # Kas mungkin tidak cukup pada [date] if negative


# ========================================
# Cash Flow Projection Endpoint
# ========================================


@router.get("/cash-flow-projection", response_model=CashProjectionResponse)
async def get_cash_flow_projection(request: Request):
    """
    Project cash position for next 7 days.
    Shows expected inflows (AR due) and outflows (AP due) with cumulative balance.
    READ-ONLY endpoint.
    """
    try:
        if not hasattr(request.state, "user") or not request.state.user:
            raise HTTPException(status_code=401, detail="Authentication required")

        tenant_id = request.state.user.get("tenant_id")
        if not tenant_id:
            raise HTTPException(status_code=401, detail="Invalid user context")

        pool = await get_db_pool()
        async with pool.acquire() as conn:
            await conn.execute("SET LOCAL statement_timeout = '5000'")
            # 1. Get current kas + bank balance (same as /summary)
            kas_bank_query = """
                SELECT
                    COALESCE(SUM(b.debit_balance - b.credit_balance), 0) as total_balance
                FROM chart_of_accounts c
                LEFT JOIN (
                    SELECT
                        jl.account_id,
                        COALESCE(SUM(jl.debit), 0) as debit_balance,
                        COALESCE(SUM(jl.credit), 0) as credit_balance
                    FROM journal_lines jl
                    JOIN journal_entries je ON je.id = jl.journal_id
                    WHERE je.status = 'POSTED'
                    GROUP BY jl.account_id
                ) b ON b.account_id = c.id
                WHERE c.tenant_id = $1
                  AND c.account_code LIKE '1-1%'
                  AND (c.account_code LIKE '1-101%' OR c.account_code LIKE '1-102%')
            """
            current_balance = await conn.fetchval(kas_bank_query, tenant_id) or 0
            current_balance = int(current_balance)

            # 2. Get expected inflows (AR due in next 7 days)
            # Pure Ledger: expected inflows from journal-based AR outstanding
            # ARAP Rule 5/6: Use compute_ar_outstanding() — single source of truth
            ar_query = """
                SELECT due_date, SUM(outstanding) as expected
                FROM compute_ar_outstanding($1)
                WHERE due_date BETWEEN CURRENT_DATE AND CURRENT_DATE + INTERVAL '7 days'
                GROUP BY due_date
                ORDER BY due_date
            """
            ar_rows = await conn.fetch(ar_query, tenant_id)
            ar_by_date = {
                row["due_date"].strftime("%Y-%m-%d"): int(row["expected"])
                for row in ar_rows
            }

            # 3. Get expected outflows (AP/bills due in next 7 days)
            # Pure Ledger: expected outflows from journal-based AP outstanding
            ap_query = """
                WITH ap_journal_outstanding AS (
                    SELECT
                        b.id as bill_id,
                        b.invoice_number,
                        b.vendor_name,
                        b.due_date,
                        b.status,
                        b.status_v2,
                        COALESCE((
                            SELECT SUM(jl2.credit)
                            FROM journal_lines jl2
                            JOIN journal_entries je2 ON je2.id = jl2.journal_id
                            JOIN chart_of_accounts coa2 ON coa2.id = jl2.account_id
                            WHERE je2.source_id = b.id
                              AND je2.source_type = 'BILL'
                              AND je2.tenant_id = $1
                              AND je2.status = 'POSTED'
                              AND coa2.account_code LIKE '2-101%%'
                        ), 0) as bill_credit,
                        COALESCE((
                            SELECT SUM(bpa.amount_applied)
                            FROM bill_payment_allocations bpa
                            JOIN bill_payments_v2 bpv2 ON bpv2.id = bpa.payment_id
                            WHERE bpa.bill_id = b.id
                              AND bpv2.tenant_id = $1
                              AND bpv2.status = 'posted'
                              AND bpv2.journal_id IS NOT NULL
                        ), 0) as payment_debit,
                        COALESCE((
                            SELECT SUM(jl2.debit)
                            FROM journal_lines jl2
                            JOIN journal_entries je2 ON je2.id = jl2.journal_id
                            JOIN chart_of_accounts coa2 ON coa2.id = jl2.account_id
                            WHERE je2.source_id = b.id
                              AND je2.source_type = 'ADJUSTMENT'
                              AND je2.tenant_id = $1
                              AND je2.status = 'POSTED'
                              AND coa2.account_code LIKE '2-101%%'
                        ), 0) as adjustment_debit
                    FROM bills b
                    WHERE b.tenant_id = $1
                      AND b.status_v2 NOT IN ('draft', 'void', 'paid')
                ),
                ap_with_outstanding AS (
                    SELECT *,
                        (bill_credit - payment_debit - adjustment_debit) as outstanding
                    FROM ap_journal_outstanding
                    WHERE (bill_credit - payment_debit - adjustment_debit) > 0
                )
                SELECT due_date, SUM(outstanding) as expected
                FROM ap_with_outstanding
                WHERE due_date BETWEEN CURRENT_DATE AND CURRENT_DATE + INTERVAL '7 days'
                GROUP BY due_date
                ORDER BY due_date
            """
            ap_rows = await conn.fetch(ap_query, tenant_id)
            ap_by_date = {
                row["due_date"].strftime("%Y-%m-%d"): int(row["expected"])
                for row in ap_rows
            }

            # 4. Build daily projections with cumulative balance
            day_labels = {
                0: "Sen",  # Monday
                1: "Sel",  # Tuesday
                2: "Rab",  # Wednesday
                3: "Kam",  # Thursday
                4: "Jum",  # Friday
                5: "Sab",  # Saturday
                6: "Min",  # Sunday
            }

            projections = []
            running_balance = current_balance
            total_in = 0
            total_out = 0
            warning_date = None

            today = datetime.now().date()

            for i in range(7):
                proj_date = today + timedelta(days=i)
                date_str = proj_date.strftime("%Y-%m-%d")
                day_label = day_labels[proj_date.weekday()]

                projected_in = ar_by_date.get(date_str, 0)
                projected_out = ap_by_date.get(date_str, 0)

                running_balance = running_balance + projected_in - projected_out
                total_in += projected_in
                total_out += projected_out

                # Check for negative balance warning
                if running_balance < 0 and warning_date is None:
                    warning_date = date_str

                projections.append(
                    CashProjectionDay(
                        date=date_str,
                        label=day_label,
                        projected_in=projected_in,
                        projected_out=projected_out,
                        projected_balance=running_balance,
                    )
                )

            # Build warning message if needed
            warning = None
            if warning_date:
                warning = f"Kas mungkin tidak cukup pada {warning_date}"

            return CashProjectionResponse(
                current_balance=current_balance,
                projected_balance_7d=running_balance,
                total_expected_in=total_in,
                total_expected_out=total_out,
                net_projection=total_in - total_out,
                projections=projections,
                warning=warning,
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Cash flow projection error: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500, detail="Failed to get cash flow projection"
        )


# ─── Sales Daily Trends ────────────────────────────────────────────────────────


class SalesDailyTrend(BaseModel):
    date: str
    label: str
    revenue: float
    trx_count: int


class SalesDailyResponse(BaseModel):
    total_revenue: float
    trx_count: int
    granularity: str
    trends: list[SalesDailyTrend]


@router.get("/sales-daily", response_model=SalesDailyResponse)
async def get_sales_daily(
    request: Request,
    days: int = Query(30, ge=7, le=365),
    granularity: str = Query("daily"),
):
    """
    Sales revenue trends — daily or monthly granularity.
    Law 1/16: reads from journal_lines (REVENUE accounts), not sales_invoices.
    Law 2: reversed_by_id IS NULL guard.
    """
    try:
        if not hasattr(request.state, "user") or not request.state.user:
            raise HTTPException(status_code=401, detail="Authentication required")
        tenant_id = request.state.user.get("tenant_id")
        if not tenant_id:
            raise HTTPException(status_code=401, detail="Tenant not found")
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, false)", str(tenant_id)
            )

            if granularity == "monthly":
                # Last 12 months
                query = """
                    WITH months AS (
                        SELECT generate_series(
                            date_trunc('month', CURRENT_DATE - INTERVAL '11 months'),
                            date_trunc('month', CURRENT_DATE),
                            '1 month'::interval
                        )::date AS period_start
                    ),
                    sales AS (
                        SELECT
                            date_trunc('month', je.journal_date)::date AS period_start,
                            SUM(jl.credit) AS revenue,
                            COUNT(DISTINCT je.id) AS trx_count
                        FROM journal_lines jl
                        JOIN journal_entries je ON je.id = jl.journal_id
                        JOIN chart_of_accounts coa ON coa.id = jl.account_id
                        WHERE je.tenant_id = $1
                          AND coa.account_type = 'REVENUE'
                          AND jl.credit > 0
                          AND je.status = 'POSTED'
                          AND je.reversed_by_id IS NULL
                          AND je.journal_date >= CURRENT_DATE - INTERVAL '11 months'
                        GROUP BY 1
                    )
                    SELECT
                        m.period_start AS date,
                        COALESCE(s.revenue, 0) AS revenue,
                        COALESCE(s.trx_count, 0) AS trx_count
                    FROM months m
                    LEFT JOIN sales s ON s.period_start = m.period_start
                    ORDER BY m.period_start
                """
                rows = await conn.fetch(query, tenant_id)
                trends = []
                for r in rows:
                    d = r["date"]
                    trends.append(
                        SalesDailyTrend(
                            date=d.strftime("%Y-%m-%d"),
                            label=d.strftime("%-m/%Y")
                            if hasattr(d, "strftime")
                            else str(d),
                            revenue=float(r["revenue"]),
                            trx_count=int(r["trx_count"]),
                        )
                    )
            else:
                # Daily — last N days
                query = """
                    WITH days AS (
                        SELECT generate_series(
                            CURRENT_DATE - ($2 - 1) * INTERVAL '1 day',
                            CURRENT_DATE,
                            '1 day'::interval
                        )::date AS day
                    ),
                    sales AS (
                        SELECT
                            je.journal_date::date AS day,
                            SUM(jl.credit) AS revenue,
                            COUNT(DISTINCT je.id) AS trx_count
                        FROM journal_lines jl
                        JOIN journal_entries je ON je.id = jl.journal_id
                        JOIN chart_of_accounts coa ON coa.id = jl.account_id
                        WHERE je.tenant_id = $1
                          AND coa.account_type = 'REVENUE'
                          AND jl.credit > 0
                          AND je.status = 'POSTED'
                          AND je.reversed_by_id IS NULL
                          AND je.journal_date >= CURRENT_DATE - ($2 - 1) * INTERVAL '1 day'
                        GROUP BY 1
                    )
                    SELECT
                        d.day AS date,
                        COALESCE(s.revenue, 0) AS revenue,
                        COALESCE(s.trx_count, 0) AS trx_count
                    FROM days d
                    LEFT JOIN sales s ON s.day = d.day
                    ORDER BY d.day
                """
                rows = await conn.fetch(query, tenant_id, days)
                trends = []
                for r in rows:
                    d = r["date"]
                    trends.append(
                        SalesDailyTrend(
                            date=d.strftime("%Y-%m-%d"),
                            label=d.strftime("%-d/%-m"),
                            revenue=float(r["revenue"]),
                            trx_count=int(r["trx_count"]),
                        )
                    )

            total_revenue = sum(t.revenue for t in trends)
            total_trx = sum(t.trx_count for t in trends)
            return SalesDailyResponse(
                total_revenue=total_revenue,
                trx_count=total_trx,
                granularity=granularity,
                trends=trends,
            )

    except Exception as e:
        logger.error(f"sales-daily error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get sales daily trends")


@router.get("/daily-transactions")
async def get_daily_transactions(request: Request, date: str = Query(...)):
    """All transactions for a specific date — invoices, bills, expenses, payments."""
    tenant_id = request.state.user.get("tenant_id")
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, false)", tenant_id)
        from datetime import date as _dt_date

        _date_obj = _dt_date.fromisoformat(date)

        invoices = await conn.fetch(
            """
            SELECT si.invoice_number, si.invoice_date::text as date,
                   si.total_amount, si.accounting_status as status,
                   c.display_name as customer_name
            FROM sales_invoices si
            LEFT JOIN customers c ON c.id::text = si.customer_id::text AND c.tenant_id = $2
            WHERE si.tenant_id = $2 AND si.invoice_date = $1::date
            ORDER BY si.invoice_number
        """,
            _date_obj,
            tenant_id,
        )

        bills = await conn.fetch(
            """
            SELECT b.invoice_number, b.issue_date::text as date,
                   b.grand_total as total_amount, b.accounting_status as status,
                   v.name as vendor_name
            FROM bills b
            LEFT JOIN vendors v ON v.id = b.vendor_id AND v.tenant_id = $2
            WHERE b.tenant_id = $2 AND b.issue_date = $1::date
            ORDER BY b.invoice_number
        """,
            _date_obj,
            tenant_id,
        )

        expenses = await conn.fetch(
            """
            SELECT e.expense_number, e.expense_date::text as date,
                   e.total_amount, e.accounting_status as status
            FROM expenses e
            WHERE e.tenant_id = $2 AND e.expense_date = $1::date
            ORDER BY e.expense_number
        """,
            _date_obj,
            tenant_id,
        )

        recv = await conn.fetch(
            """
            SELECT rp.reference_number, rp.payment_date::text as date,
                   rp.total_amount, rp.status,
                   c.display_name as customer_name
            FROM receive_payments rp
            LEFT JOIN customers c ON c.id::text = rp.customer_id AND c.tenant_id = $2
            WHERE rp.tenant_id = $2 AND rp.payment_date = $1::date
            ORDER BY rp.reference_number
        """,
            _date_obj,
            tenant_id,
        )

        bp = await conn.fetch(
            """
            SELECT bp.payment_number, bp.payment_date::text as date,
                   bp.total_amount, bp.accounting_status as status,
                   v.name as vendor_name
            FROM bill_payments_v2 bp
            LEFT JOIN vendors v ON v.id = bp.vendor_id AND v.tenant_id = $2
            WHERE bp.tenant_id = $2 AND bp.payment_date = $1::date
            ORDER BY bp.payment_number
        """,
            _date_obj,
            tenant_id,
        )

        def _fmt(rows, name_key="customer_name"):
            out = []
            for r in rows:
                doc = (
                    r.get("invoice_number")
                    or r.get("expense_number")
                    or r.get("reference_number")
                    or r.get("payment_number")
                    or "-"
                )
                out.append(
                    {
                        "doc_number": doc,
                        "date": r["date"],
                        "amount": float(r["total_amount"] or 0),
                        "status": r.get("status") or "-",
                        "party": r.get(name_key) or "-",
                    }
                )
            return out

        return {
            "success": True,
            "date": date,
            "sales_invoices": _fmt(invoices),
            "bills": _fmt(bills, "vendor_name"),
            "expenses": _fmt(expenses),
            "receive_payments": _fmt(recv),
            "bill_payments": _fmt(bp, "vendor_name"),
            "summary": {
                "total_transactions": len(invoices)
                + len(bills)
                + len(expenses)
                + len(recv)
                + len(bp),
                "total_invoice_amount": sum(
                    float(r["total_amount"] or 0) for r in invoices
                ),
                "total_bill_amount": sum(float(r["total_amount"] or 0) for r in bills),
                "total_expense_amount": sum(
                    float(r["total_amount"] or 0) for r in expenses
                ),
                "total_received_amount": sum(
                    float(r["total_amount"] or 0) for r in recv
                ),
                "total_paid_amount": sum(float(r["total_amount"] or 0) for r in bp),
            },
        }
