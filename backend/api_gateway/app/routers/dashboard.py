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


class DashboardSummaryResponse(BaseModel):
    """Combined dashboard summary response"""
    laba_rugi: LabaRugiSummary
    piutang: PiutangSummary
    hutang: HutangSummary
    kas_bank: KasBankSummary
    generated_at: str


# ========================================
# Helper Functions
# ========================================

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

            laba_rugi = LabaRugiSummary(
                profit=profit,
                pendapatan=pendapatan,
                pengeluaran=pengeluaran,
                period=period_label,
                margin_persen=margin_persen
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

            piutang = PiutangSummary(
                total=int(ar_row['total']) if ar_row else 0,
                customer_count=int(ar_row['customer_count']) if ar_row else 0,
                jatuh_tempo=int(ar_row['jatuh_tempo']) if ar_row else 0,
                current=int(ar_row['current_amount']) if ar_row else 0,
                overdue_1_30=int(ar_row['overdue_1_30']) if ar_row else 0,
                overdue_31_60=int(ar_row['overdue_31_60']) if ar_row else 0,
                overdue_61_90=int(ar_row['overdue_61_90']) if ar_row else 0,
                overdue_90_plus=int(ar_row['overdue_90_plus']) if ar_row else 0
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

            hutang = HutangSummary(
                total=int(ap_row['total']) if ap_row else 0,
                supplier_count=int(ap_row['supplier_count']) if ap_row else 0,
                jatuh_tempo=int(ap_row['jatuh_tempo']) if ap_row else 0,
                current=int(ap_row['current_amount']) if ap_row else 0,
                overdue_1_30=int(ap_row['overdue_1_30']) if ap_row else 0,
                overdue_31_60=int(ap_row['overdue_31_60']) if ap_row else 0,
                overdue_61_90=int(ap_row['overdue_61_90']) if ap_row else 0,
                overdue_90_plus=int(ap_row['overdue_90_plus']) if ap_row else 0
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
                LEFT JOIN account_balances_daily b ON b.account_id = c.id
                    AND b.balance_date = (
                        SELECT MAX(balance_date)
                        FROM account_balances_daily
                        WHERE account_id = c.id
                    )
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

            kas_bank = KasBankSummary(
                total=total_kas + total_bank,
                kas=total_kas,
                bank=total_bank,
                accounts=accounts
            )

            logger.info(f"Dashboard summary generated: tenant={tenant_id}, period={period}")

            return DashboardSummaryResponse(
                laba_rugi=laba_rugi,
                piutang=piutang,
                hutang=hutang,
                kas_bank=kas_bank,
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
                LEFT JOIN account_balances_daily b ON b.account_id = c.id
                    AND b.balance_date = (
                        SELECT MAX(balance_date)
                        FROM account_balances_daily
                        WHERE account_id = c.id
                    )
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
