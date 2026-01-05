"""
Cash Flow Statement Generator
=============================

Generates Cash Flow Statement (Indirect Method) following SAK EMKM:
- Arus Kas dari Aktivitas Operasi (Operating Activities)
- Arus Kas dari Aktivitas Investasi (Investing Activities)
- Arus Kas dari Aktivitas Pendanaan (Financing Activities)
"""
from datetime import date, datetime
from decimal import Decimal
from typing import List, Dict
from dataclasses import dataclass, field
from uuid import UUID

import asyncpg


@dataclass
class CashFlowLine:
    """Single line item in cash flow statement"""
    description: str
    amount: Decimal = Decimal("0")
    is_subtotal: bool = False


@dataclass
class CashFlowSection:
    """Section of the Cash Flow Statement"""
    title: str
    lines: List[CashFlowLine] = field(default_factory=list)
    subtotal: Decimal = Decimal("0")


@dataclass
class CashFlowReport:
    """
    Cash Flow Statement (Laporan Arus Kas)

    Indirect Method:
    1. Start with Net Income
    2. Add back non-cash expenses (depreciation)
    3. Adjust for changes in working capital
    4. = Cash from Operating Activities

    Plus:
    5. Cash from Investing Activities
    6. Cash from Financing Activities

    = Net Change in Cash
    + Beginning Cash Balance
    = Ending Cash Balance
    """
    tenant_id: str
    period_start: date
    period_end: date
    company_name: str = ""

    # Starting point
    net_income: Decimal = Decimal("0")

    # Operating Activities
    operating_activities: CashFlowSection = field(
        default_factory=lambda: CashFlowSection("Arus Kas dari Aktivitas Operasi")
    )
    cash_from_operating: Decimal = Decimal("0")

    # Investing Activities
    investing_activities: CashFlowSection = field(
        default_factory=lambda: CashFlowSection("Arus Kas dari Aktivitas Investasi")
    )
    cash_from_investing: Decimal = Decimal("0")

    # Financing Activities
    financing_activities: CashFlowSection = field(
        default_factory=lambda: CashFlowSection("Arus Kas dari Aktivitas Pendanaan")
    )
    cash_from_financing: Decimal = Decimal("0")

    # Summary
    net_change_in_cash: Decimal = Decimal("0")
    beginning_cash: Decimal = Decimal("0")
    ending_cash: Decimal = Decimal("0")

    # Verification
    actual_ending_cash: Decimal = Decimal("0")
    is_balanced: bool = False

    generated_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> Dict:
        """Convert to dictionary for JSON serialization"""
        return {
            "tenant_id": str(self.tenant_id),
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "company_name": self.company_name,
            "net_income": float(self.net_income),
            "operating_activities": {
                "title": self.operating_activities.title,
                "lines": [
                    {
                        "description": l.description,
                        "amount": float(l.amount)
                    }
                    for l in self.operating_activities.lines
                ],
                "subtotal": float(self.cash_from_operating)
            },
            "investing_activities": {
                "title": self.investing_activities.title,
                "lines": [
                    {
                        "description": l.description,
                        "amount": float(l.amount)
                    }
                    for l in self.investing_activities.lines
                ],
                "subtotal": float(self.cash_from_investing)
            },
            "financing_activities": {
                "title": self.financing_activities.title,
                "lines": [
                    {
                        "description": l.description,
                        "amount": float(l.amount)
                    }
                    for l in self.financing_activities.lines
                ],
                "subtotal": float(self.cash_from_financing)
            },
            "net_change_in_cash": float(self.net_change_in_cash),
            "beginning_cash": float(self.beginning_cash),
            "ending_cash": float(self.ending_cash),
            "actual_ending_cash": float(self.actual_ending_cash),
            "is_balanced": self.is_balanced,
            "generated_at": self.generated_at.isoformat()
        }


class CashFlowGenerator:
    """
    Generates Cash Flow Statement using the Indirect Method.

    The indirect method starts with net income and adjusts for:
    - Non-cash items (depreciation, amortization)
    - Changes in working capital (AR, AP, Inventory)
    """

    # Account code patterns for classification
    CASH_ACCOUNTS = ['1-10100', '1-10200', '1-10300']  # Cash, Bank, Petty Cash
    AR_ACCOUNTS = ['1-10400', '1-10500']  # AR
    INVENTORY_ACCOUNTS = ['1-10600']  # Inventory
    AP_ACCOUNTS = ['2-10100', '2-10200']  # AP
    FIXED_ASSET_ACCOUNTS = ['1-2']  # Fixed Assets
    DEPRECIATION_ACCOUNTS = ['1-20900']  # Accumulated Depreciation
    LOAN_ACCOUNTS = ['2-2']  # Long-term Liabilities
    EQUITY_ACCOUNTS = ['3-1', '3-3', '3-4']  # Capital, Distributions

    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    async def generate(
        self,
        tenant_id: str,
        period_start: date,
        period_end: date,
        company_name: str = ""
    ) -> CashFlowReport:
        """
        Generate Cash Flow Statement for the specified period.

        Uses the indirect method:
        1. Calculate net income
        2. Adjust for non-cash items
        3. Calculate changes in working capital
        4. Calculate investing and financing activities
        """
        report = CashFlowReport(
            tenant_id=tenant_id,
            period_start=period_start,
            period_end=period_end,
            company_name=company_name
        )

        async with self.pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true)",
                str(tenant_id)
            )

            # 1. Calculate Net Income
            report.net_income = await self._get_net_income(
                conn, tenant_id, period_start, period_end
            )

            # 2. Get beginning and ending cash balances
            report.beginning_cash = await self._get_cash_balance(
                conn, tenant_id, period_start
            )
            report.actual_ending_cash = await self._get_cash_balance(
                conn, tenant_id, period_end
            )

            # 3. Calculate changes in working capital items
            ar_change = await self._get_account_change(
                conn, tenant_id, period_start, period_end, '1-104'  # AR
            )
            inventory_change = await self._get_account_change(
                conn, tenant_id, period_start, period_end, '1-106'  # Inventory
            )
            ap_change = await self._get_account_change(
                conn, tenant_id, period_start, period_end, '2-1'  # AP
            )

            # 4. Get depreciation expense
            depreciation = await self._get_depreciation(
                conn, tenant_id, period_start, period_end
            )

            # 5. Get investing activities (fixed asset changes)
            fixed_asset_change = await self._get_account_change(
                conn, tenant_id, period_start, period_end, '1-2'  # Fixed Assets
            )

            # 6. Get financing activities
            loan_change = await self._get_account_change(
                conn, tenant_id, period_start, period_end, '2-2'  # Loans
            )
            capital_change = await self._get_account_change(
                conn, tenant_id, period_start, period_end, '3-1'  # Capital
            )

        # Build Operating Activities Section
        report.operating_activities.lines.append(
            CashFlowLine("Laba Bersih", report.net_income)
        )

        # Add back depreciation (non-cash expense)
        if depreciation != 0:
            report.operating_activities.lines.append(
                CashFlowLine("Penyusutan", depreciation)
            )

        # Working capital changes
        # Increase in AR = cash outflow (negative)
        if ar_change != 0:
            report.operating_activities.lines.append(
                CashFlowLine(
                    "Perubahan Piutang Usaha",
                    -ar_change  # Increase in AR reduces cash
                )
            )

        # Increase in Inventory = cash outflow (negative)
        if inventory_change != 0:
            report.operating_activities.lines.append(
                CashFlowLine(
                    "Perubahan Persediaan",
                    -inventory_change  # Increase in inventory reduces cash
                )
            )

        # Increase in AP = cash inflow (positive)
        if ap_change != 0:
            report.operating_activities.lines.append(
                CashFlowLine(
                    "Perubahan Hutang Usaha",
                    ap_change  # Increase in AP increases cash
                )
            )

        # Calculate operating cash flow
        report.cash_from_operating = (
            report.net_income
            + depreciation
            - ar_change
            - inventory_change
            + ap_change
        )

        # Build Investing Activities Section
        if fixed_asset_change != 0:
            report.investing_activities.lines.append(
                CashFlowLine(
                    "Pembelian Aset Tetap" if fixed_asset_change > 0 else "Penjualan Aset Tetap",
                    -fixed_asset_change  # Purchase = negative cash flow
                )
            )
        report.cash_from_investing = -fixed_asset_change

        # Build Financing Activities Section
        if loan_change != 0:
            report.financing_activities.lines.append(
                CashFlowLine(
                    "Pinjaman" if loan_change > 0 else "Pembayaran Pinjaman",
                    loan_change
                )
            )

        if capital_change != 0:
            report.financing_activities.lines.append(
                CashFlowLine(
                    "Setoran Modal" if capital_change > 0 else "Penarikan Modal",
                    capital_change
                )
            )

        report.cash_from_financing = loan_change + capital_change

        # Calculate summary
        report.net_change_in_cash = (
            report.cash_from_operating
            + report.cash_from_investing
            + report.cash_from_financing
        )
        report.ending_cash = report.beginning_cash + report.net_change_in_cash

        # Verify against actual ending cash
        report.is_balanced = abs(
            report.ending_cash - report.actual_ending_cash
        ) < Decimal("1.00")  # Allow for small rounding differences

        return report

    async def _get_net_income(
        self,
        conn: asyncpg.Connection,
        tenant_id: str,
        period_start: date,
        period_end: date
    ) -> Decimal:
        """Calculate net income for the period."""
        query = """
            SELECT
                COALESCE(SUM(
                    CASE WHEN c.account_type = 'INCOME' THEN jl.credit - jl.debit
                         WHEN c.account_type = 'EXPENSE' THEN -(jl.debit - jl.credit)
                         ELSE 0
                    END
                ), 0) as net_income
            FROM journal_lines jl
            JOIN journal_entries je ON je.id = jl.journal_id
            JOIN chart_of_accounts c ON c.id = jl.account_id
            WHERE je.tenant_id = $1
                AND je.journal_date BETWEEN $2 AND $3
                AND je.status = 'POSTED'
                AND c.account_type IN ('INCOME', 'EXPENSE')
        """
        result = await conn.fetchval(query, tenant_id, period_start, period_end)
        return Decimal(str(result)) if result else Decimal("0")

    async def _get_cash_balance(
        self,
        conn: asyncpg.Connection,
        tenant_id: str,
        as_of_date: date
    ) -> Decimal:
        """Get total cash balance as of date."""
        query = """
            SELECT
                COALESCE(SUM(jl.debit - jl.credit), 0) as balance
            FROM journal_lines jl
            JOIN journal_entries je ON je.id = jl.journal_id
            JOIN chart_of_accounts c ON c.id = jl.account_id
            WHERE je.tenant_id = $1
                AND je.journal_date <= $2
                AND je.status = 'POSTED'
                AND c.account_code LIKE '1-10%'
                AND c.account_code IN ('1-10100', '1-10200', '1-10300')
        """
        result = await conn.fetchval(query, tenant_id, as_of_date)
        return Decimal(str(result)) if result else Decimal("0")

    async def _get_account_change(
        self,
        conn: asyncpg.Connection,
        tenant_id: str,
        period_start: date,
        period_end: date,
        account_prefix: str
    ) -> Decimal:
        """Get change in account balance during period."""
        # Get beginning balance
        begin_query = """
            SELECT
                COALESCE(SUM(jl.debit - jl.credit), 0) as balance
            FROM journal_lines jl
            JOIN journal_entries je ON je.id = jl.journal_id
            JOIN chart_of_accounts c ON c.id = jl.account_id
            WHERE je.tenant_id = $1
                AND je.journal_date < $2
                AND je.status = 'POSTED'
                AND c.account_code LIKE $3
        """

        # Get ending balance
        end_query = """
            SELECT
                COALESCE(SUM(jl.debit - jl.credit), 0) as balance
            FROM journal_lines jl
            JOIN journal_entries je ON je.id = jl.journal_id
            JOIN chart_of_accounts c ON c.id = jl.account_id
            WHERE je.tenant_id = $1
                AND je.journal_date <= $2
                AND je.status = 'POSTED'
                AND c.account_code LIKE $3
        """

        prefix_pattern = f"{account_prefix}%"

        begin_balance = await conn.fetchval(
            begin_query, tenant_id, period_start, prefix_pattern
        )
        end_balance = await conn.fetchval(
            end_query, tenant_id, period_end, prefix_pattern
        )

        begin = Decimal(str(begin_balance)) if begin_balance else Decimal("0")
        end = Decimal(str(end_balance)) if end_balance else Decimal("0")

        return end - begin

    async def _get_depreciation(
        self,
        conn: asyncpg.Connection,
        tenant_id: str,
        period_start: date,
        period_end: date
    ) -> Decimal:
        """Get depreciation expense for the period."""
        query = """
            SELECT
                COALESCE(SUM(jl.debit - jl.credit), 0) as depreciation
            FROM journal_lines jl
            JOIN journal_entries je ON je.id = jl.journal_id
            JOIN chart_of_accounts c ON c.id = jl.account_id
            WHERE je.tenant_id = $1
                AND je.journal_date BETWEEN $2 AND $3
                AND je.status = 'POSTED'
                AND c.account_code LIKE '5-3%'
        """
        result = await conn.fetchval(query, tenant_id, period_start, period_end)
        return Decimal(str(result)) if result else Decimal("0")
