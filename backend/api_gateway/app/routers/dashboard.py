"""
Dashboard Router - Aggregated KPIs for Dashboard Summary Cards
Combines data from: P&L, AR aging, AP aging, and Kas/Bank balances
"""
from fastapi import APIRouter, HTTPException, Request, Query
from pydantic import BaseModel
from typing import List, Optional
import logging
import asyncpg
from datetime import datetime, timedelta

# Import centralized config
from ..config import settings

logger = logging.getLogger(__name__)
router = APIRouter()


# Database connection helper
async def get_db_connection():
    """Get database connection using centralized config"""
    db_config = settings.get_db_config()
    return await asyncpg.connect(**db_config)


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
    nearest_days: Optional[int] = None  # Days until due (positive = future, negative = overdue)
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
    dso: float                   # Days Sales Outstanding
    dso_benchmark: int = 45      # Industry benchmark
    dso_status: str              # "good" | "ok" | "warning" | "critical"
    dpo: float                   # Days Payable Outstanding
    dpo_benchmark: int = 30      # Industry benchmark
    dpo_status: str              # "good" | "ok" | "warning"


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
    ap_subledger: float
    gl_ap_balance: float
    variance_bills_ap: float
    variance_ap_gl: float
    issues_count: int
    issues: dict


# ========================================
# Helper Functions
# ========================================

# Indonesian day name abbreviations
DAY_NAMES = ['Sen', 'Sel', 'Rab', 'Kam', 'Jum', 'Sab', 'Min']


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


def get_period_dates(period: str) -> tuple:
    """
    Get date range for period.
    period: '7d' | '30d' | 'month'
    Returns: (start_epoch_ms, end_epoch_ms, period_label)

    Note: transaksi_harian.timestamp is BIGINT (epoch milliseconds)
    """
    now = datetime.now()

    if period == '7d':
        start_date = now - timedelta(days=7)
        period_label = "7 Hari"
    elif period == '30d':
        start_date = now - timedelta(days=30)
        period_label = "30 Hari"
    else:  # month
        start_date = datetime(now.year, now.month, 1)
        period_label = "Bulan Ini"

    # Convert to epoch milliseconds for BIGINT comparison
    start_epoch = int(start_date.timestamp() * 1000)
    end_epoch = int(now.timestamp() * 1000)

    return start_epoch, end_epoch, period_label


def get_prev_period_dates(period: str) -> tuple:
    """
    Get date range for PREVIOUS period (for comparison).
    - If current period is "30d" (last 30 days), previous is 30-60 days ago
    - If current period is "7d", previous is 7-14 days ago
    - If current period is "month", previous is last month
    
    Returns: (start_epoch_ms, end_epoch_ms)
    """
    now = datetime.now()

    if period == '7d':
        # Previous 7 days: 7-14 days ago
        start_date = now - timedelta(days=14)
        end_date = now - timedelta(days=7)
    elif period == '30d':
        # Previous 30 days: 30-60 days ago
        start_date = now - timedelta(days=60)
        end_date = now - timedelta(days=30)
    else:  # month
        # Previous month
        first_of_current_month = datetime(now.year, now.month, 1)
        # Last day of previous month
        end_date = first_of_current_month - timedelta(days=1)
        # First day of previous month
        start_date = datetime(end_date.year, end_date.month, 1)

    # Convert to epoch milliseconds for BIGINT comparison
    start_epoch = int(start_date.timestamp() * 1000)
    end_epoch = int(end_date.timestamp() * 1000)

    return start_epoch, end_epoch


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

@router.get("/summary", response_model=DashboardSummaryResponse)
async def get_dashboard_summary(
    request: Request,
    period: str = Query("30d", regex="^(7d|30d|month)$")
):
    """
    Get aggregated dashboard summary for all 4 cards.

    Period options:
    - 7d: Last 7 days
    - 30d: Last 30 days (default)
    - month: Current month
    """
    try:
        # Get tenant_id from auth context
        if not hasattr(request.state, 'user') or not request.state.user:
            raise HTTPException(status_code=401, detail="Authentication required")

        tenant_id = request.state.user.get("tenant_id")
        if not tenant_id:
            raise HTTPException(status_code=401, detail="Invalid user context")

        start_date, end_date, period_label = get_period_dates(period)
        prev_start_date, prev_end_date = get_prev_period_dates(period)

        conn = await get_db_connection()
        try:
            # ============================
            # 1. LABA RUGI (P&L Summary)
            # ============================
            pl_query = """
                SELECT
                    COALESCE(SUM(CASE WHEN jenis_transaksi = 'penjualan' THEN total_nominal ELSE 0 END), 0) as pendapatan,
                    COALESCE(SUM(CASE WHEN jenis_transaksi = 'pembelian' THEN total_nominal ELSE 0 END), 0) as hpp,
                    COALESCE(SUM(CASE WHEN jenis_transaksi = 'beban' THEN total_nominal ELSE 0 END), 0) as beban
                FROM public.transaksi_harian
                WHERE tenant_id = $1
                  AND timestamp >= $2
                  AND timestamp <= $3
                  AND status = 'approved'
            """
            pl_row = await conn.fetchrow(pl_query, tenant_id, start_date, end_date)

            pendapatan = int(pl_row['pendapatan']) if pl_row else 0
            hpp = int(pl_row['hpp']) if pl_row else 0
            beban = int(pl_row['beban']) if pl_row else 0
            pengeluaran = hpp + beban
            profit = pendapatan - pengeluaran
            margin_persen = round((profit / pendapatan * 100), 1) if pendapatan > 0 else 0.0

            # Previous period P&L
            prev_pl_row = await conn.fetchrow(pl_query, tenant_id, prev_start_date, prev_end_date)
            prev_pendapatan = int(prev_pl_row['pendapatan']) if prev_pl_row else 0
            prev_hpp = int(prev_pl_row['hpp']) if prev_pl_row else 0
            prev_beban = int(prev_pl_row['beban']) if prev_pl_row else 0
            prev_profit = prev_pendapatan - (prev_hpp + prev_beban)
            profit_change_pct = calc_change_pct(profit, prev_profit)

            laba_rugi = LabaRugiSummary(
                profit=profit,
                pendapatan=pendapatan,
                pengeluaran=pengeluaran,
                period=period_label,
                margin_persen=margin_persen,
                prev_profit=prev_profit,
                prev_pendapatan=prev_pendapatan,
                profit_change_pct=profit_change_pct
            )

            # ============================
            # 2. PIUTANG (AR Aging)
            # ============================
            # Get customers with positive saldo_hutang (they owe us)
            ar_query = """
                SELECT
                    COUNT(*) as customer_count,
                    COALESCE(SUM(saldo_hutang), 0) as total,
                    COUNT(CASE WHEN last_transaction_at < NOW() - INTERVAL '30 days' THEN 1 END) as jatuh_tempo,
                    COALESCE(SUM(CASE WHEN last_transaction_at >= NOW() - INTERVAL '30 days' THEN saldo_hutang ELSE 0 END), 0) as current_amount,
                    COALESCE(SUM(CASE WHEN last_transaction_at < NOW() - INTERVAL '30 days' AND last_transaction_at >= NOW() - INTERVAL '60 days' THEN saldo_hutang ELSE 0 END), 0) as overdue_1_30,
                    COALESCE(SUM(CASE WHEN last_transaction_at < NOW() - INTERVAL '60 days' AND last_transaction_at >= NOW() - INTERVAL '90 days' THEN saldo_hutang ELSE 0 END), 0) as overdue_31_60,
                    COALESCE(SUM(CASE WHEN last_transaction_at < NOW() - INTERVAL '90 days' AND last_transaction_at >= NOW() - INTERVAL '120 days' THEN saldo_hutang ELSE 0 END), 0) as overdue_61_90,
                    COALESCE(SUM(CASE WHEN last_transaction_at < NOW() - INTERVAL '120 days' THEN saldo_hutang ELSE 0 END), 0) as overdue_90_plus
                FROM customers
                WHERE tenant_id = $1
                  AND tipe = 'pelanggan'
                  AND saldo_hutang > 0
            """
            ar_row = await conn.fetchrow(ar_query, tenant_id)

            # Query oldest overdue AR invoice
            oldest_ar_query = """
                SELECT customer_name, CURRENT_DATE - due_date as days_overdue
                FROM accounts_receivable
                WHERE tenant_id = $1
                  AND due_date < CURRENT_DATE
                  AND status != 'PAID'
                ORDER BY due_date ASC
                LIMIT 1
            """
            oldest_ar = await conn.fetchrow(oldest_ar_query, tenant_id)

            # Previous period AR comparison (simplified: sum of AR transactions in prev period)
            prev_ar_query = """
                SELECT COALESCE(SUM(saldo_hutang), 0) as total
                FROM customers
                WHERE tenant_id = $1
                  AND tipe = 'pelanggan'
                  AND saldo_hutang > 0
            """
            # Note: AR balance is point-in-time, so prev_total represents same metric
            # For true comparison, we'd need historical snapshots
            ar_total = int(ar_row['total']) if ar_row else 0
            # Estimate prev by looking at transaction volume change
            prev_ar_estimate_query = """
                SELECT COALESCE(SUM(CASE WHEN jenis_transaksi = 'penjualan' THEN total_nominal ELSE 0 END), 0) as sales
                FROM public.transaksi_harian
                WHERE tenant_id = $1
                  AND timestamp >= $2 AND timestamp <= $3
                  AND status = 'approved'
            """
            prev_ar_sales = await conn.fetchrow(prev_ar_estimate_query, tenant_id, prev_start_date, prev_end_date)
            curr_ar_sales = await conn.fetchrow(prev_ar_estimate_query, tenant_id, start_date, end_date)
            prev_sales_val = int(prev_ar_sales['sales']) if prev_ar_sales else 0
            curr_sales_val = int(curr_ar_sales['sales']) if curr_ar_sales else 0
            # Estimate prev AR based on sales ratio
            if curr_sales_val > 0 and prev_sales_val > 0:
                ar_prev_total = int(ar_total * prev_sales_val / curr_sales_val)
            else:
                ar_prev_total = 0
            ar_change_pct = calc_change_pct(ar_total, ar_prev_total)

            piutang = PiutangSummary(
                total=ar_total,
                customer_count=int(ar_row['customer_count']) if ar_row else 0,
                jatuh_tempo=int(ar_row['jatuh_tempo']) if ar_row else 0,
                current=int(ar_row['current_amount']) if ar_row else 0,
                overdue_1_30=int(ar_row['overdue_1_30']) if ar_row else 0,
                overdue_31_60=int(ar_row['overdue_31_60']) if ar_row else 0,
                overdue_61_90=int(ar_row['overdue_61_90']) if ar_row else 0,
                overdue_90_plus=int(ar_row['overdue_90_plus']) if ar_row else 0,
                oldest_customer=oldest_ar['customer_name'] if oldest_ar else None,
                oldest_days=int(oldest_ar['days_overdue']) if oldest_ar else None,
                prev_total=ar_prev_total,
                change_pct=ar_change_pct
            )

            # ============================
            # 3. HUTANG (AP Aging)
            # ============================
            # Get suppliers with negative saldo_hutang (we owe them)
            ap_query = """
                SELECT
                    COUNT(*) as supplier_count,
                    COALESCE(SUM(ABS(saldo_hutang)), 0) as total,
                    COUNT(CASE WHEN last_transaction_at < NOW() - INTERVAL '30 days' THEN 1 END) as jatuh_tempo,
                    COALESCE(SUM(CASE WHEN last_transaction_at >= NOW() - INTERVAL '30 days' THEN ABS(saldo_hutang) ELSE 0 END), 0) as current_amount,
                    COALESCE(SUM(CASE WHEN last_transaction_at < NOW() - INTERVAL '30 days' AND last_transaction_at >= NOW() - INTERVAL '60 days' THEN ABS(saldo_hutang) ELSE 0 END), 0) as overdue_1_30,
                    COALESCE(SUM(CASE WHEN last_transaction_at < NOW() - INTERVAL '60 days' AND last_transaction_at >= NOW() - INTERVAL '90 days' THEN ABS(saldo_hutang) ELSE 0 END), 0) as overdue_31_60,
                    COALESCE(SUM(CASE WHEN last_transaction_at < NOW() - INTERVAL '90 days' AND last_transaction_at >= NOW() - INTERVAL '120 days' THEN ABS(saldo_hutang) ELSE 0 END), 0) as overdue_61_90,
                    COALESCE(SUM(CASE WHEN last_transaction_at < NOW() - INTERVAL '120 days' THEN ABS(saldo_hutang) ELSE 0 END), 0) as overdue_90_plus
                FROM customers
                WHERE tenant_id = $1
                  AND tipe = 'supplier'
                  AND saldo_hutang < 0
            """
            ap_row = await conn.fetchrow(ap_query, tenant_id)

            # Query nearest AP bill (due soonest or already overdue)
            # Updated to use consolidated bills table
            nearest_ap_query = """
                SELECT vendor_name as supplier_name, due_date - CURRENT_DATE as days_until_due
                FROM bills
                WHERE tenant_id = $1
                  AND status NOT IN ('paid', 'void')
                ORDER BY due_date ASC
                LIMIT 1
            """
            nearest_ap = await conn.fetchrow(nearest_ap_query, tenant_id)

            # Previous period AP comparison (estimate based on purchase ratio)
            prev_ap_estimate_query = """
                SELECT COALESCE(SUM(CASE WHEN jenis_transaksi = 'pembelian' THEN total_nominal ELSE 0 END), 0) as purchases
                FROM public.transaksi_harian
                WHERE tenant_id = $1
                  AND timestamp >= $2 AND timestamp <= $3
                  AND status = 'approved'
            """
            ap_total = int(ap_row['total']) if ap_row else 0
            prev_ap_purchases = await conn.fetchrow(prev_ap_estimate_query, tenant_id, prev_start_date, prev_end_date)
            curr_ap_purchases = await conn.fetchrow(prev_ap_estimate_query, tenant_id, start_date, end_date)
            prev_purchases_val = int(prev_ap_purchases['purchases']) if prev_ap_purchases else 0
            curr_purchases_val = int(curr_ap_purchases['purchases']) if curr_ap_purchases else 0
            # Estimate prev AP based on purchases ratio
            if curr_purchases_val > 0 and prev_purchases_val > 0:
                ap_prev_total = int(ap_total * prev_purchases_val / curr_purchases_val)
            else:
                ap_prev_total = 0
            ap_change_pct = calc_change_pct(ap_total, ap_prev_total)

            hutang = HutangSummary(
                total=ap_total,
                supplier_count=int(ap_row['supplier_count']) if ap_row else 0,
                jatuh_tempo=int(ap_row['jatuh_tempo']) if ap_row else 0,
                current=int(ap_row['current_amount']) if ap_row else 0,
                overdue_1_30=int(ap_row['overdue_1_30']) if ap_row else 0,
                overdue_31_60=int(ap_row['overdue_31_60']) if ap_row else 0,
                overdue_61_90=int(ap_row['overdue_61_90']) if ap_row else 0,
                overdue_90_plus=int(ap_row['overdue_90_plus']) if ap_row else 0,
                nearest_supplier=nearest_ap['supplier_name'] if nearest_ap else None,
                nearest_days=int(nearest_ap['days_until_due']) if nearest_ap else None,
                prev_total=ap_prev_total,
                change_pct=ap_change_pct
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
                balance = int(row['balance'])
                account = BankAccount(
                    id=row['account_code'],
                    name=row['name'],
                    account_type=row['account_type'],
                    balance=balance,
                    account_code=row['account_code']
                )
                accounts.append(account)

                if row['account_type'] == 'cash':
                    total_kas += balance
                else:
                    total_bank += balance

            # Previous period Kas/Bank comparison
            # Query net cash flow for previous period to estimate prev balance
            prev_cashflow_query = """
                SELECT 
                    COALESCE(SUM(CASE WHEN jenis_transaksi IN ('penjualan', 'penerimaan') THEN total_nominal ELSE 0 END), 0) as inflow,
                    COALESCE(SUM(CASE WHEN jenis_transaksi IN ('pembelian', 'beban', 'pembayaran') THEN total_nominal ELSE 0 END), 0) as outflow
                FROM public.transaksi_harian
                WHERE tenant_id = $1
                  AND timestamp >= $2 AND timestamp <= $3
                  AND status = 'approved'
            """
            kas_bank_total = total_kas + total_bank
            # Get current period net flow
            curr_flow = await conn.fetchrow(prev_cashflow_query, tenant_id, start_date, end_date)
            curr_net = (int(curr_flow['inflow']) if curr_flow else 0) - (int(curr_flow['outflow']) if curr_flow else 0)
            # Estimate prev balance = current - net flow of current period
            kas_bank_prev_total = kas_bank_total - curr_net if kas_bank_total > 0 else 0
            kas_bank_change_pct = calc_change_pct(kas_bank_total, kas_bank_prev_total)

            kas_bank = KasBankSummary(
                total=kas_bank_total,
                kas=total_kas,
                bank=total_bank,
                accounts=accounts,
                prev_total=kas_bank_prev_total,
                change_pct=kas_bank_change_pct
            )

            # ============================
            # 5. KPI METRICS (DSO/DPO)
            # ============================
            days_in_period = get_days_in_period(period)
            
            # DSO = AR / daily_revenue
            daily_revenue = pendapatan / days_in_period if days_in_period > 0 else 0
            dso = round(piutang.total / daily_revenue, 1) if daily_revenue > 0 else 0.0
            dso_status = calculate_dso_status(dso)
            
            # DPO = AP / daily_purchases (hpp)
            daily_purchases = hpp / days_in_period if days_in_period > 0 else 0
            dpo = round(hutang.total / daily_purchases, 1) if daily_purchases > 0 else 0.0
            dpo_status = calculate_dpo_status(dpo)
            
            kpi = KPIMetrics(
                dso=dso,
                dso_benchmark=45,
                dso_status=dso_status,
                dpo=dpo,
                dpo_benchmark=30,
                dpo_status=dpo_status
            )

            logger.info(f"Dashboard summary generated: tenant={tenant_id}, period={period}, dso={dso}, dpo={dpo}")

            return DashboardSummaryResponse(
                laba_rugi=laba_rugi,
                piutang=piutang,
                hutang=hutang,
                kas_bank=kas_bank,
                kpi=kpi,
                generated_at=datetime.now().isoformat()
            )

        finally:
            await conn.close()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Dashboard summary error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate dashboard summary")


@router.get("/piutang", response_model=PiutangSummary)
async def get_piutang_detail(
    request: Request,
    filter: str = Query("all", regex="^(all|overdue)$")
):
    """
    Get detailed AR aging data for Piutang panel.

    Filter options:
    - all: All customers with debt
    - overdue: Only overdue customers
    """
    try:
        if not hasattr(request.state, 'user') or not request.state.user:
            raise HTTPException(status_code=401, detail="Authentication required")

        tenant_id = request.state.user.get("tenant_id")
        if not tenant_id:
            raise HTTPException(status_code=401, detail="Invalid user context")

        conn = await get_db_connection()
        try:
            # Aging buckets based on last_transaction_at
            query = """
                SELECT
                    COUNT(*) as customer_count,
                    COALESCE(SUM(saldo_hutang), 0) as total,
                    COALESCE(SUM(CASE
                        WHEN last_transaction_at >= NOW() - INTERVAL '30 days' THEN saldo_hutang
                        ELSE 0
                    END), 0) as current,
                    COALESCE(SUM(CASE
                        WHEN last_transaction_at < NOW() - INTERVAL '30 days'
                             AND last_transaction_at >= NOW() - INTERVAL '60 days'
                        THEN saldo_hutang ELSE 0
                    END), 0) as overdue_1_30,
                    COALESCE(SUM(CASE
                        WHEN last_transaction_at < NOW() - INTERVAL '60 days'
                             AND last_transaction_at >= NOW() - INTERVAL '90 days'
                        THEN saldo_hutang ELSE 0
                    END), 0) as overdue_31_60,
                    COALESCE(SUM(CASE
                        WHEN last_transaction_at < NOW() - INTERVAL '90 days'
                             AND last_transaction_at >= NOW() - INTERVAL '120 days'
                        THEN saldo_hutang ELSE 0
                    END), 0) as overdue_61_90,
                    COALESCE(SUM(CASE
                        WHEN last_transaction_at < NOW() - INTERVAL '120 days'
                        THEN saldo_hutang ELSE 0
                    END), 0) as overdue_90_plus,
                    COUNT(CASE
                        WHEN last_transaction_at < NOW() - INTERVAL '30 days'
                        THEN 1
                    END) as jatuh_tempo_count
                FROM customers
                WHERE tenant_id = $1
                  AND tipe = 'pelanggan'
                  AND saldo_hutang > 0
            """

            if filter == "overdue":
                query = query.replace(
                    "AND saldo_hutang > 0",
                    "AND saldo_hutang > 0 AND last_transaction_at < NOW() - INTERVAL '30 days'"
                )

            row = await conn.fetchrow(query, tenant_id)

            return PiutangSummary(
                total=int(row['total']) if row else 0,
                customer_count=int(row['customer_count']) if row else 0,
                jatuh_tempo=int(row['jatuh_tempo_count']) if row else 0,
                current=int(row['current']) if row else 0,
                overdue_1_30=int(row['overdue_1_30']) if row else 0,
                overdue_31_60=int(row['overdue_31_60']) if row else 0,
                overdue_61_90=int(row['overdue_61_90']) if row else 0,
                overdue_90_plus=int(row['overdue_90_plus']) if row else 0
            )

        finally:
            await conn.close()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Piutang detail error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get piutang detail")


@router.get("/hutang", response_model=HutangSummary)
async def get_hutang_detail(
    request: Request,
    filter: str = Query("all", regex="^(all|overdue)$")
):
    """
    Get detailed AP aging data for Hutang panel.
    """
    try:
        if not hasattr(request.state, 'user') or not request.state.user:
            raise HTTPException(status_code=401, detail="Authentication required")

        tenant_id = request.state.user.get("tenant_id")
        if not tenant_id:
            raise HTTPException(status_code=401, detail="Invalid user context")

        conn = await get_db_connection()
        try:
            query = """
                SELECT
                    COUNT(*) as supplier_count,
                    COALESCE(SUM(ABS(saldo_hutang)), 0) as total,
                    COALESCE(SUM(CASE
                        WHEN last_transaction_at >= NOW() - INTERVAL '30 days' THEN ABS(saldo_hutang)
                        ELSE 0
                    END), 0) as current,
                    COALESCE(SUM(CASE
                        WHEN last_transaction_at < NOW() - INTERVAL '30 days'
                             AND last_transaction_at >= NOW() - INTERVAL '60 days'
                        THEN ABS(saldo_hutang) ELSE 0
                    END), 0) as overdue_1_30,
                    COALESCE(SUM(CASE
                        WHEN last_transaction_at < NOW() - INTERVAL '60 days'
                             AND last_transaction_at >= NOW() - INTERVAL '90 days'
                        THEN ABS(saldo_hutang) ELSE 0
                    END), 0) as overdue_31_60,
                    COALESCE(SUM(CASE
                        WHEN last_transaction_at < NOW() - INTERVAL '90 days'
                             AND last_transaction_at >= NOW() - INTERVAL '120 days'
                        THEN ABS(saldo_hutang) ELSE 0
                    END), 0) as overdue_61_90,
                    COALESCE(SUM(CASE
                        WHEN last_transaction_at < NOW() - INTERVAL '120 days'
                        THEN ABS(saldo_hutang) ELSE 0
                    END), 0) as overdue_90_plus,
                    COUNT(CASE
                        WHEN last_transaction_at < NOW() - INTERVAL '30 days'
                        THEN 1
                    END) as jatuh_tempo_count
                FROM customers
                WHERE tenant_id = $1
                  AND tipe = 'supplier'
                  AND saldo_hutang < 0
            """

            if filter == "overdue":
                query = query.replace(
                    "AND saldo_hutang < 0",
                    "AND saldo_hutang < 0 AND last_transaction_at < NOW() - INTERVAL '30 days'"
                )

            row = await conn.fetchrow(query, tenant_id)

            return HutangSummary(
                total=int(row['total']) if row else 0,
                supplier_count=int(row['supplier_count']) if row else 0,
                jatuh_tempo=int(row['jatuh_tempo_count']) if row else 0,
                current=int(row['current']) if row else 0,
                overdue_1_30=int(row['overdue_1_30']) if row else 0,
                overdue_31_60=int(row['overdue_31_60']) if row else 0,
                overdue_61_90=int(row['overdue_61_90']) if row else 0,
                overdue_90_plus=int(row['overdue_90_plus']) if row else 0
            )

        finally:
            await conn.close()

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
        if not hasattr(request.state, 'user') or not request.state.user:
            raise HTTPException(status_code=401, detail="Authentication required")

        tenant_id = request.state.user.get("tenant_id")
        if not tenant_id:
            raise HTTPException(status_code=401, detail="Invalid user context")

        conn = await get_db_connection()
        try:
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
                balance = int(row['balance'])
                account = BankAccount(
                    id=row['account_code'],
                    name=row['name'],
                    account_type=row['account_type'],
                    balance=balance,
                    account_code=row['account_code']
                )
                accounts.append(account)

                if row['account_type'] == 'cash':
                    total_kas += balance
                else:
                    total_bank += balance

            return KasBankSummary(
                total=total_kas + total_bank,
                kas=total_kas,
                bank=total_bank,
                accounts=accounts
            )

        finally:
            await conn.close()

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
    period: str = Query("7d", regex="^(7d|30d|month)$")
):
    """
    Get daily cash flow trends (kas masuk/keluar) for chart visualization.

    Period options:
    - 7d: Last 7 days (default)
    - 30d: Last 30 days
    - month: Current month
    """
    try:
        if not hasattr(request.state, 'user') or not request.state.user:
            raise HTTPException(status_code=401, detail="Authentication required")

        tenant_id = request.state.user.get("tenant_id")
        if not tenant_id:
            raise HTTPException(status_code=401, detail="Invalid user context")

        # Calculate date range
        now = datetime.now()
        if period == '7d':
            start_date = now - timedelta(days=6)  # Include today = 7 days
        elif period == '30d':
            start_date = now - timedelta(days=29)
        else:  # month
            start_date = datetime(now.year, now.month, 1)

        conn = await get_db_connection()
        try:
            # Query daily cash flow from journal entries
            # Kas Masuk = Debit to Cash/Bank accounts (money coming in)
            # Kas Keluar = Credit from Cash/Bank accounts (money going out)
            query = """
                WITH daily_flows AS (
                    SELECT
                        DATE(je.journal_date) as flow_date,
                        COALESCE(SUM(CASE
                            WHEN jl.debit > 0 AND c.account_code LIKE '1-10%'
                            THEN jl.debit
                            ELSE 0
                        END), 0) as kas_masuk,
                        COALESCE(SUM(CASE
                            WHEN jl.credit > 0 AND c.account_code LIKE '1-10%'
                            THEN jl.credit
                            ELSE 0
                        END), 0) as kas_keluar
                    FROM journal_entries je
                    JOIN journal_lines jl ON jl.journal_id = je.id
                    JOIN chart_of_accounts c ON c.id = jl.account_id
                    WHERE je.tenant_id = $1
                      AND je.journal_date >= $2
                      AND je.journal_date <= $3
                      AND je.status = 'POSTED'
                    GROUP BY DATE(je.journal_date)
                )
                SELECT flow_date, kas_masuk, kas_keluar
                FROM daily_flows
                ORDER BY flow_date
            """

            rows = await conn.fetch(query, tenant_id, start_date.date(), now.date())

            # Build date range with all days (even if no transactions)
            trends = []
            total_masuk = 0
            total_keluar = 0

            # Create a dict for quick lookup
            flow_by_date = {row['flow_date']: row for row in rows}

            # Generate all dates in range
            current = start_date.date()
            end = now.date()
            while current <= end:
                flow = flow_by_date.get(current)
                kas_masuk = int(flow['kas_masuk']) if flow else 0
                kas_keluar = int(flow['kas_keluar']) if flow else 0

                trends.append(CashFlowTrend(
                    date=current.isoformat(),
                    label=get_day_label(datetime.combine(current, datetime.min.time())),
                    kas_masuk=kas_masuk,
                    kas_keluar=kas_keluar
                ))

                total_masuk += kas_masuk
                total_keluar += kas_keluar
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
                trx_masuk=int(today_trx['trx_masuk']) if today_trx and today_trx['trx_masuk'] else 0,
                trx_keluar=int(today_trx['trx_keluar']) if today_trx and today_trx['trx_keluar'] else 0
            )

        finally:
            await conn.close()

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
    limit: int = Query(5, ge=1, le=20)
):
    """
    Get top expense categories breakdown for period.

    Period options:
    - 7d: Last 7 days
    - 30d: Last 30 days (default)
    - month: Current month

    Limit: Number of top categories to return (1-20, default 5)
    """
    try:
        if not hasattr(request.state, 'user') or not request.state.user:
            raise HTTPException(status_code=401, detail="Authentication required")

        tenant_id = request.state.user.get("tenant_id")
        if not tenant_id:
            raise HTTPException(status_code=401, detail="Invalid user context")

        # Calculate date range
        now = datetime.now()
        if period == '7d':
            start_date = now - timedelta(days=7)
        elif period == '30d':
            start_date = now - timedelta(days=30)
        else:  # month
            start_date = datetime(now.year, now.month, 1)

        conn = await get_db_connection()
        try:
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
                  AND (c.account_code LIKE '5-%' OR c.account_code LIKE '6-%')
                  AND jl.debit > 0
                GROUP BY COALESCE(c.category, c.name)
                ORDER BY amount DESC
                LIMIT $4
            """

            rows = await conn.fetch(query, tenant_id, start_date.date(), now.date(), limit)

            # Calculate total and percentages
            total = sum(int(row['amount']) for row in rows)

            expenses = []
            for row in rows:
                amount = int(row['amount'])
                percentage = round((amount / total * 100), 1) if total > 0 else 0

                expenses.append(TopExpense(
                    category=row['category'],
                    amount=amount,
                    percentage=percentage
                ))

            return TopExpensesResponse(
                expenses=expenses,
                total=total
            )

        finally:
            await conn.close()

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
        if not hasattr(request.state, 'user') or not request.state.user:
            raise HTTPException(status_code=401, detail="Authentication required")

        tenant_id = request.state.user.get("tenant_id")
        if not tenant_id:
            raise HTTPException(status_code=401, detail="Invalid user context")

        conn = await get_db_connection()
        try:
            query = """
                SELECT
                    invoice_number,
                    customer_name,
                    due_date,
                    CURRENT_DATE - due_date as days_overdue,
                    (amount - amount_paid) as outstanding
                FROM accounts_receivable
                WHERE tenant_id = $1
                  AND due_date < CURRENT_DATE
                  AND status != 'PAID'
                ORDER BY days_overdue DESC, outstanding DESC
            """

            rows = await conn.fetch(query, tenant_id)

            invoices = []
            total_outstanding = 0

            for row in rows:
                outstanding = int(row['outstanding'])
                invoices.append(OverdueInvoice(
                    invoice_number=row['invoice_number'],
                    customer_name=row['customer_name'],
                    due_date=row['due_date'].isoformat() if row['due_date'] else '',
                    days_overdue=int(row['days_overdue']),
                    outstanding=outstanding
                ))
                total_outstanding += outstanding

            return OverdueInvoicesResponse(
                invoices=invoices,
                total_outstanding=total_outstanding,
                count=len(invoices)
            )

        finally:
            await conn.close()

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
        if not hasattr(request.state, 'user') or not request.state.user:
            raise HTTPException(status_code=401, detail="Authentication required")

        tenant_id = request.state.user.get("tenant_id")
        if not tenant_id:
            raise HTTPException(status_code=401, detail="Invalid user context")

        conn = await get_db_connection()
        try:
            # Updated to use consolidated bills table
            query = """
                SELECT
                    invoice_number as bill_number,
                    vendor_name as supplier_name,
                    due_date,
                    CURRENT_DATE - due_date as days_overdue,
                    (amount - amount_paid) as outstanding
                FROM bills
                WHERE tenant_id = $1
                  AND due_date < CURRENT_DATE
                  AND status NOT IN ('paid', 'void')
                ORDER BY days_overdue DESC, outstanding DESC
            """

            rows = await conn.fetch(query, tenant_id)

            bills = []
            total_outstanding = 0

            for row in rows:
                outstanding = int(row['outstanding'])
                bills.append(OverdueBill(
                    bill_number=row['bill_number'],
                    supplier_name=row['supplier_name'],
                    due_date=row['due_date'].isoformat() if row['due_date'] else '',
                    days_overdue=int(row['days_overdue']),
                    outstanding=outstanding
                ))
                total_outstanding += outstanding

            return OverdueBillsResponse(
                bills=bills,
                total_outstanding=total_outstanding,
                count=len(bills)
            )

        finally:
            await conn.close()

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
        if not hasattr(request.state, 'user') or not request.state.user:
            raise HTTPException(status_code=401, detail="Authentication required")

        tenant_id = request.state.user.get("tenant_id")
        if not tenant_id:
            raise HTTPException(status_code=401, detail="Invalid user context")

        conn = await get_db_connection()
        try:
            # Outstanding Bills total
            bills_query = """
                SELECT COALESCE(SUM(amount - amount_paid), 0) as total
                FROM bills
                WHERE tenant_id = $1 AND status NOT IN ('paid', 'void')
            """
            bills_total = await conn.fetchval(bills_query, tenant_id)

            # AP Subledger total
            ap_query = """
                SELECT COALESCE(SUM(amount - amount_paid), 0) as total
                FROM accounts_payable
                WHERE tenant_id = $1 AND status IN ('OPEN', 'PARTIAL')
            """
            ap_total = await conn.fetchval(ap_query, tenant_id)

            # GL AP Account balance (account 2-10100)
            # Formula: SUM(credit - debit) for liability account
            gl_query = """
                SELECT COALESCE(SUM(jl.credit - jl.debit), 0) as balance
                FROM journal_lines jl
                JOIN journal_entries je ON je.id = jl.journal_id
                    AND je.journal_date = jl.journal_date
                JOIN chart_of_accounts coa ON coa.id = jl.account_id
                WHERE je.tenant_id = $1
                  AND je.status = 'POSTED'
                  AND coa.code = '2-10100'
            """
            gl_balance = await conn.fetchval(gl_query, tenant_id)

            # Find issues
            # Bills without AP
            bills_no_ap = await conn.fetchval("""
                SELECT COUNT(*) FROM bills
                WHERE tenant_id = $1 AND ap_id IS NULL AND status NOT IN ('void', 'paid')
            """, tenant_id)

            # Bills without Journal
            bills_no_journal = await conn.fetchval("""
                SELECT COUNT(*) FROM bills
                WHERE tenant_id = $1 AND journal_id IS NULL AND status NOT IN ('void', 'paid')
            """, tenant_id)

            # AP without Bill
            ap_no_bill = await conn.fetchval("""
                SELECT COUNT(*) FROM accounts_payable ap
                LEFT JOIN bills b ON b.ap_id = ap.id
                WHERE ap.tenant_id = $1 AND b.id IS NULL AND ap.status NOT IN ('VOID', 'PAID')
            """, tenant_id)

            # Amount mismatch
            amount_mismatch = await conn.fetchval("""
                SELECT COUNT(*) FROM bills b
                JOIN accounts_payable ap ON ap.id = b.ap_id
                WHERE b.tenant_id = $1 AND b.amount != ap.amount::BIGINT
                  AND b.status NOT IN ('void', 'paid')
            """, tenant_id)

            # Calculate variances
            variance_bills_ap = float(bills_total) - float(ap_total or 0)
            variance_ap_gl = float(ap_total or 0) - float(gl_balance or 0)

            # Check if in sync (tolerance: 0.01)
            is_in_sync = (abs(variance_bills_ap) < 0.01 and abs(variance_ap_gl) < 0.01)

            total_issues = bills_no_ap + bills_no_journal + ap_no_bill + amount_mismatch

            return APReconciliationResponse(
                in_sync=is_in_sync and total_issues == 0,
                status="OK" if (is_in_sync and total_issues == 0) else "WARNING",
                bills_outstanding=int(bills_total),
                ap_subledger=float(ap_total or 0),
                gl_ap_balance=float(gl_balance or 0),
                variance_bills_ap=variance_bills_ap,
                variance_ap_gl=variance_ap_gl,
                issues_count=total_issues,
                issues={
                    "bills_without_ap": bills_no_ap,
                    "bills_without_journal": bills_no_journal,
                    "ap_without_bill": ap_no_bill,
                    "amount_mismatch": amount_mismatch
                }
            )

        finally:
            await conn.close()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Reconciliation status error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get reconciliation status")




# ========================================
# Cash Flow Projection Models
# ========================================

class CashProjectionDay(BaseModel):
    """Daily cash projection data point"""
    date: str                    # YYYY-MM-DD
    label: str                   # Sen, Sel, etc.
    projected_in: int            # Expected cash in (from AR due dates)
    projected_out: int           # Expected cash out (from AP due dates)
    projected_balance: int       # Cumulative balance


class CashProjectionResponse(BaseModel):
    """Cash flow projection response"""
    current_balance: int         # Todays kas + bank
    projected_balance_7d: int    # Balance after 7 days
    total_expected_in: int       # Total AR coming due in 7 days
    total_expected_out: int      # Total AP coming due in 7 days
    net_projection: int          # Expected change
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

        conn = await get_db_connection()
        try:
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
            ar_query = """
                SELECT due_date, SUM(amount - amount_paid) as expected
                FROM accounts_receivable
                WHERE tenant_id = $1 
                  AND status != 'PAID'
                  AND due_date BETWEEN CURRENT_DATE AND CURRENT_DATE + INTERVAL '7 days'
                GROUP BY due_date
                ORDER BY due_date
            """
            ar_rows = await conn.fetch(ar_query, tenant_id)
            ar_by_date = {row["due_date"].strftime("%Y-%m-%d"): int(row["expected"]) for row in ar_rows}

            # 3. Get expected outflows (AP/bills due in next 7 days)
            ap_query = """
                SELECT due_date, SUM(amount - amount_paid) as expected
                FROM bills
                WHERE tenant_id = $1 
                  AND status NOT IN ('paid', 'void')
                  AND due_date BETWEEN CURRENT_DATE AND CURRENT_DATE + INTERVAL '7 days'
                GROUP BY due_date
                ORDER BY due_date
            """
            ap_rows = await conn.fetch(ap_query, tenant_id)
            ap_by_date = {row["due_date"].strftime("%Y-%m-%d"): int(row["expected"]) for row in ap_rows}

            # 4. Build daily projections with cumulative balance
            day_labels = {
                0: "Sen",  # Monday
                1: "Sel",  # Tuesday
                2: "Rab",  # Wednesday
                3: "Kam",  # Thursday
                4: "Jum",  # Friday
                5: "Sab",  # Saturday
                6: "Min"   # Sunday
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

                projections.append(CashProjectionDay(
                    date=date_str,
                    label=day_label,
                    projected_in=projected_in,
                    projected_out=projected_out,
                    projected_balance=running_balance
                ))

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
                warning=warning
            )

        finally:
            await conn.close()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Cash flow projection error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get cash flow projection")
