"""
Balance Sheet (Neraca) Report Generator
=======================================

Generates Balance Sheet following SAK EMKM format:
- Aset (Assets)
  - Aset Lancar (Current Assets)
  - Aset Tetap (Fixed Assets)
- Liabilitas (Liabilities)
  - Liabilitas Jangka Pendek (Current Liabilities)
  - Liabilitas Jangka Panjang (Long-term Liabilities)
- Ekuitas (Equity)

Accounting Equation: Assets = Liabilities + Equity
"""
from datetime import date, datetime
from decimal import Decimal
from typing import List, Dict
from dataclasses import dataclass, field
from uuid import UUID

import asyncpg

from ..constants import AccountType, NormalBalance


@dataclass
class AccountLine:
    """Single account line in the report"""
    account_code: str
    account_name: str
    amount: Decimal = Decimal("0")
    is_subtotal: bool = False
    indent_level: int = 0


@dataclass
class ReportSection:
    """Section of the Balance Sheet"""
    title: str
    lines: List[AccountLine] = field(default_factory=list)
    subtotal: Decimal = Decimal("0")


@dataclass
class BalanceSheetReport:
    """
    Balance Sheet Report (Neraca)

    Structure:
    ASET (ASSETS)
    - Aset Lancar (Current Assets)
    - Aset Tetap (Fixed Assets)
    - Aset Lainnya (Other Assets)

    LIABILITAS (LIABILITIES)
    - Liabilitas Jangka Pendek (Current Liabilities)
    - Liabilitas Jangka Panjang (Long-term Liabilities)

    EKUITAS (EQUITY)
    - Modal (Capital)
    - Laba Ditahan (Retained Earnings)
    - Laba Periode Berjalan (Current Period Net Income)
    """
    tenant_id: str
    as_of_date: date
    company_name: str = ""

    # Assets
    current_assets: ReportSection = field(
        default_factory=lambda: ReportSection("Aset Lancar")
    )
    total_current_assets: Decimal = Decimal("0")

    fixed_assets: ReportSection = field(
        default_factory=lambda: ReportSection("Aset Tetap")
    )
    total_fixed_assets: Decimal = Decimal("0")

    other_assets: ReportSection = field(
        default_factory=lambda: ReportSection("Aset Lainnya")
    )
    total_other_assets: Decimal = Decimal("0")

    total_assets: Decimal = Decimal("0")

    # Liabilities
    current_liabilities: ReportSection = field(
        default_factory=lambda: ReportSection("Liabilitas Jangka Pendek")
    )
    total_current_liabilities: Decimal = Decimal("0")

    long_term_liabilities: ReportSection = field(
        default_factory=lambda: ReportSection("Liabilitas Jangka Panjang")
    )
    total_long_term_liabilities: Decimal = Decimal("0")

    total_liabilities: Decimal = Decimal("0")

    # Equity
    equity: ReportSection = field(
        default_factory=lambda: ReportSection("Ekuitas")
    )
    total_equity: Decimal = Decimal("0")

    # Retained earnings and current period income
    retained_earnings: Decimal = Decimal("0")
    current_period_income: Decimal = Decimal("0")

    # Balance check
    total_liabilities_equity: Decimal = Decimal("0")
    is_balanced: bool = False

    generated_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> Dict:
        """Convert to dictionary for JSON serialization"""
        return {
            "tenant_id": str(self.tenant_id),
            "as_of_date": self.as_of_date.isoformat(),
            "company_name": self.company_name,
            "assets": {
                "current_assets": {
                    "title": self.current_assets.title,
                    "lines": [
                        {
                            "account_code": l.account_code,
                            "account_name": l.account_name,
                            "amount": float(l.amount)
                        }
                        for l in self.current_assets.lines
                    ],
                    "subtotal": float(self.total_current_assets)
                },
                "fixed_assets": {
                    "title": self.fixed_assets.title,
                    "lines": [
                        {
                            "account_code": l.account_code,
                            "account_name": l.account_name,
                            "amount": float(l.amount)
                        }
                        for l in self.fixed_assets.lines
                    ],
                    "subtotal": float(self.total_fixed_assets)
                },
                "other_assets": {
                    "title": self.other_assets.title,
                    "lines": [
                        {
                            "account_code": l.account_code,
                            "account_name": l.account_name,
                            "amount": float(l.amount)
                        }
                        for l in self.other_assets.lines
                    ],
                    "subtotal": float(self.total_other_assets)
                },
                "total": float(self.total_assets)
            },
            "liabilities": {
                "current_liabilities": {
                    "title": self.current_liabilities.title,
                    "lines": [
                        {
                            "account_code": l.account_code,
                            "account_name": l.account_name,
                            "amount": float(l.amount)
                        }
                        for l in self.current_liabilities.lines
                    ],
                    "subtotal": float(self.total_current_liabilities)
                },
                "long_term_liabilities": {
                    "title": self.long_term_liabilities.title,
                    "lines": [
                        {
                            "account_code": l.account_code,
                            "account_name": l.account_name,
                            "amount": float(l.amount)
                        }
                        for l in self.long_term_liabilities.lines
                    ],
                    "subtotal": float(self.total_long_term_liabilities)
                },
                "total": float(self.total_liabilities)
            },
            "equity": {
                "title": self.equity.title,
                "lines": [
                    {
                        "account_code": l.account_code,
                        "account_name": l.account_name,
                        "amount": float(l.amount)
                    }
                    for l in self.equity.lines
                ],
                "retained_earnings": float(self.retained_earnings),
                "current_period_income": float(self.current_period_income),
                "total": float(self.total_equity)
            },
            "total_liabilities_equity": float(self.total_liabilities_equity),
            "is_balanced": self.is_balanced,
            "generated_at": self.generated_at.isoformat()
        }


class BalanceSheetGenerator:
    """
    Generates Balance Sheet report from journal entries.

    Assets = Liabilities + Equity

    Normal Balances:
    - Assets: DEBIT (positive = debit balance)
    - Liabilities: CREDIT (positive = credit balance)
    - Equity: CREDIT (positive = credit balance)
    """

    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    async def generate(
        self,
        tenant_id: str,
        as_of_date: date,
        company_name: str = ""
    ) -> BalanceSheetReport:
        """
        Generate Balance Sheet as of specified date.

        Args:
            tenant_id: Tenant UUID
            as_of_date: Report date
            company_name: Company name for report header

        Returns:
            BalanceSheetReport with all sections populated
        """
        # Query for Asset, Liability, Equity account balances
        balance_query = """
            SELECT
                c.account_code,
                c.name as account_name,
                c.account_type,
                c.normal_balance,
                COALESCE(SUM(jl.debit), 0) as total_debit,
                COALESCE(SUM(jl.credit), 0) as total_credit
            FROM chart_of_accounts c
            LEFT JOIN journal_lines jl ON jl.account_id = c.id
            LEFT JOIN journal_entries je ON je.id = jl.journal_id
                AND je.tenant_id = $1
                AND je.journal_date <= $2
                AND je.status = 'POSTED'
            WHERE c.tenant_id = $1
                AND c.is_active = true
                AND c.account_type IN ('ASSET', 'LIABILITY', 'EQUITY')
            GROUP BY c.id, c.account_code, c.name, c.account_type, c.normal_balance
            ORDER BY c.account_code
        """

        # Query for current period income (for equity section)
        income_query = """
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
                AND je.journal_date <= $2
                AND je.journal_date >= DATE_TRUNC('year', $2::date)
                AND je.status = 'POSTED'
                AND c.account_type IN ('INCOME', 'EXPENSE')
        """

        async with self.pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true)",
                str(tenant_id)
            )

            balance_rows = await conn.fetch(balance_query, tenant_id, as_of_date)
            income_row = await conn.fetchrow(income_query, tenant_id, as_of_date)

        report = BalanceSheetReport(
            tenant_id=tenant_id,
            as_of_date=as_of_date,
            company_name=company_name
        )

        # Current period net income
        if income_row:
            report.current_period_income = Decimal(str(income_row['net_income']))

        for row in balance_rows:
            account_code = row['account_code']
            account_name = row['account_name']
            account_type = row['account_type']
            normal_balance = row['normal_balance']
            total_debit = Decimal(str(row['total_debit']))
            total_credit = Decimal(str(row['total_credit']))

            # Calculate balance based on normal balance
            if normal_balance == 'DEBIT':
                balance = total_debit - total_credit
            else:  # CREDIT
                balance = total_credit - total_debit

            # Skip zero balances
            if balance == 0:
                continue

            line = AccountLine(
                account_code=account_code,
                account_name=account_name,
                amount=abs(balance)
            )

            # Categorize based on account type and code
            if account_type == 'ASSET':
                if account_code.startswith('1-1'):  # Current Assets
                    report.current_assets.lines.append(line)
                    # Assets: debit balance is positive
                    if normal_balance == 'DEBIT':
                        report.total_current_assets += balance
                    else:  # Contra asset (e.g., accumulated depreciation)
                        report.total_current_assets -= balance
                elif account_code.startswith('1-2'):  # Fixed Assets
                    report.fixed_assets.lines.append(line)
                    if normal_balance == 'DEBIT':
                        report.total_fixed_assets += balance
                    else:
                        report.total_fixed_assets -= balance
                else:  # Other Assets
                    report.other_assets.lines.append(line)
                    if normal_balance == 'DEBIT':
                        report.total_other_assets += balance
                    else:
                        report.total_other_assets -= balance

            elif account_type == 'LIABILITY':
                if account_code.startswith('2-1'):  # Current Liabilities
                    report.current_liabilities.lines.append(line)
                    report.total_current_liabilities += balance
                else:  # Long-term Liabilities
                    report.long_term_liabilities.lines.append(line)
                    report.total_long_term_liabilities += balance

            elif account_type == 'EQUITY':
                report.equity.lines.append(line)
                if account_code.startswith('3-2'):  # Retained Earnings
                    report.retained_earnings += balance
                report.total_equity += balance

        # Add current period income to equity
        report.total_equity += report.current_period_income

        # Calculate totals
        report.total_assets = (
            report.total_current_assets
            + report.total_fixed_assets
            + report.total_other_assets
        )
        report.total_liabilities = (
            report.total_current_liabilities
            + report.total_long_term_liabilities
        )
        report.total_liabilities_equity = (
            report.total_liabilities + report.total_equity
        )

        # Check if balanced (should be within small tolerance)
        report.is_balanced = abs(
            report.total_assets - report.total_liabilities_equity
        ) < Decimal("0.01")

        return report

    async def generate_comparative(
        self,
        tenant_id: str,
        date1: date,
        date2: date,
        company_name: str = ""
    ) -> Dict:
        """
        Generate comparative Balance Sheet for two dates.

        Returns dictionary with both periods and variance.
        """
        report1 = await self.generate(tenant_id, date1, company_name)
        report2 = await self.generate(tenant_id, date2, company_name)

        return {
            "period1": report1.to_dict(),
            "period2": report2.to_dict(),
            "variance": {
                "total_assets": float(report1.total_assets - report2.total_assets),
                "total_liabilities": float(
                    report1.total_liabilities - report2.total_liabilities
                ),
                "total_equity": float(report1.total_equity - report2.total_equity),
                "current_assets_change_pct": (
                    float(
                        (report1.total_current_assets - report2.total_current_assets)
                        / report2.total_current_assets * 100
                    )
                    if report2.total_current_assets != 0 else 0
                )
            }
        }

    async def get_working_capital(
        self,
        tenant_id: str,
        as_of_date: date
    ) -> Dict:
        """
        Calculate working capital metrics.

        Working Capital = Current Assets - Current Liabilities
        Current Ratio = Current Assets / Current Liabilities
        """
        report = await self.generate(tenant_id, as_of_date)

        working_capital = report.total_current_assets - report.total_current_liabilities
        current_ratio = (
            float(report.total_current_assets / report.total_current_liabilities)
            if report.total_current_liabilities != 0 else 0
        )

        return {
            "working_capital": float(working_capital),
            "current_ratio": round(current_ratio, 2),
            "current_assets": float(report.total_current_assets),
            "current_liabilities": float(report.total_current_liabilities),
            "as_of_date": as_of_date.isoformat()
        }
