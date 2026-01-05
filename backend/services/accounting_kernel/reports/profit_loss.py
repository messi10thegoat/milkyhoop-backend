"""
Profit & Loss (Laba Rugi) Report Generator
==========================================

Generates Profit & Loss statement following SAK EMKM format:
- Pendapatan (Income/Revenue)
- Beban Usaha (Operating Expenses)
- Laba/Rugi Usaha (Operating Income)
- Pendapatan/Beban Lain-lain (Other Income/Expenses)
- Laba/Rugi Bersih (Net Income)
"""
from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional, Dict
from dataclasses import dataclass, field
from uuid import UUID

import asyncpg

from ..constants import AccountType


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
    """Section of the P&L report"""
    title: str
    lines: List[AccountLine] = field(default_factory=list)
    subtotal: Decimal = Decimal("0")


@dataclass
class ProfitLossReport:
    """
    Profit & Loss Report (Laporan Laba Rugi)

    Structure:
    - Pendapatan Usaha (Operating Revenue)
    - Beban Pokok Penjualan (Cost of Goods Sold)
    - Laba Kotor (Gross Profit)
    - Beban Usaha (Operating Expenses)
    - Laba Usaha (Operating Income)
    - Pendapatan/Beban Lain-lain (Other Income/Expenses)
    - Laba Bersih Sebelum Pajak (Net Income Before Tax)
    - Pajak (Tax)
    - Laba Bersih (Net Income)
    """
    tenant_id: str
    period_start: date
    period_end: date
    company_name: str = ""

    # Revenue Section
    revenue: ReportSection = field(
        default_factory=lambda: ReportSection("Pendapatan Usaha")
    )
    total_revenue: Decimal = Decimal("0")

    # Cost of Goods Sold
    cogs: ReportSection = field(
        default_factory=lambda: ReportSection("Beban Pokok Penjualan")
    )
    total_cogs: Decimal = Decimal("0")

    # Gross Profit
    gross_profit: Decimal = Decimal("0")

    # Operating Expenses
    operating_expenses: ReportSection = field(
        default_factory=lambda: ReportSection("Beban Usaha")
    )
    total_operating_expenses: Decimal = Decimal("0")

    # Operating Income
    operating_income: Decimal = Decimal("0")

    # Other Income/Expenses
    other_income: ReportSection = field(
        default_factory=lambda: ReportSection("Pendapatan Lain-lain")
    )
    total_other_income: Decimal = Decimal("0")

    other_expenses: ReportSection = field(
        default_factory=lambda: ReportSection("Beban Lain-lain")
    )
    total_other_expenses: Decimal = Decimal("0")

    # Net Income
    net_income_before_tax: Decimal = Decimal("0")
    tax_expense: Decimal = Decimal("0")
    net_income: Decimal = Decimal("0")

    generated_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> Dict:
        """Convert to dictionary for JSON serialization"""
        return {
            "tenant_id": str(self.tenant_id),
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "company_name": self.company_name,
            "revenue": {
                "title": self.revenue.title,
                "lines": [
                    {
                        "account_code": l.account_code,
                        "account_name": l.account_name,
                        "amount": float(l.amount)
                    }
                    for l in self.revenue.lines
                ],
                "subtotal": float(self.total_revenue)
            },
            "cogs": {
                "title": self.cogs.title,
                "lines": [
                    {
                        "account_code": l.account_code,
                        "account_name": l.account_name,
                        "amount": float(l.amount)
                    }
                    for l in self.cogs.lines
                ],
                "subtotal": float(self.total_cogs)
            },
            "gross_profit": float(self.gross_profit),
            "operating_expenses": {
                "title": self.operating_expenses.title,
                "lines": [
                    {
                        "account_code": l.account_code,
                        "account_name": l.account_name,
                        "amount": float(l.amount)
                    }
                    for l in self.operating_expenses.lines
                ],
                "subtotal": float(self.total_operating_expenses)
            },
            "operating_income": float(self.operating_income),
            "other_income": {
                "title": self.other_income.title,
                "lines": [
                    {
                        "account_code": l.account_code,
                        "account_name": l.account_name,
                        "amount": float(l.amount)
                    }
                    for l in self.other_income.lines
                ],
                "subtotal": float(self.total_other_income)
            },
            "other_expenses": {
                "title": self.other_expenses.title,
                "lines": [
                    {
                        "account_code": l.account_code,
                        "account_name": l.account_name,
                        "amount": float(l.amount)
                    }
                    for l in self.other_expenses.lines
                ],
                "subtotal": float(self.total_other_expenses)
            },
            "net_income_before_tax": float(self.net_income_before_tax),
            "tax_expense": float(self.tax_expense),
            "net_income": float(self.net_income),
            "generated_at": self.generated_at.isoformat()
        }


class ProfitLossGenerator:
    """
    Generates Profit & Loss report from journal entries.

    Follows double-entry accounting principles:
    - Income accounts have CREDIT normal balance (negative = debit balance)
    - Expense accounts have DEBIT normal balance (positive = debit balance)
    """

    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    async def generate(
        self,
        tenant_id: str,
        period_start: date,
        period_end: date,
        company_name: str = ""
    ) -> ProfitLossReport:
        """
        Generate P&L report for the specified period.

        Args:
            tenant_id: Tenant UUID
            period_start: Start date of reporting period
            period_end: End date of reporting period
            company_name: Company name for report header

        Returns:
            ProfitLossReport with all sections populated
        """
        # Query to get all income/expense account balances for the period
        query = """
            SELECT
                c.account_code,
                c.name as account_name,
                c.account_type,
                c.parent_code,
                COALESCE(SUM(jl.debit), 0) as total_debit,
                COALESCE(SUM(jl.credit), 0) as total_credit
            FROM chart_of_accounts c
            LEFT JOIN journal_lines jl ON jl.account_id = c.id
            LEFT JOIN journal_entries je ON je.id = jl.journal_id
                AND je.tenant_id = $1
                AND je.journal_date BETWEEN $2 AND $3
                AND je.status = 'POSTED'
            WHERE c.tenant_id = $1
                AND c.is_active = true
                AND c.account_type IN ('INCOME', 'EXPENSE')
            GROUP BY c.id, c.account_code, c.name, c.account_type, c.parent_code
            HAVING COALESCE(SUM(jl.debit), 0) != 0 OR COALESCE(SUM(jl.credit), 0) != 0
            ORDER BY c.account_code
        """

        async with self.pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true)",
                str(tenant_id)
            )

            rows = await conn.fetch(query, tenant_id, period_start, period_end)

        report = ProfitLossReport(
            tenant_id=tenant_id,
            period_start=period_start,
            period_end=period_end,
            company_name=company_name
        )

        for row in rows:
            account_code = row['account_code']
            account_name = row['account_name']
            account_type = row['account_type']
            total_debit = Decimal(str(row['total_debit']))
            total_credit = Decimal(str(row['total_credit']))

            # For income: credit - debit = positive income
            # For expense: debit - credit = positive expense
            if account_type == 'INCOME':
                amount = total_credit - total_debit  # Net credit balance
            else:  # EXPENSE
                amount = total_debit - total_credit  # Net debit balance

            # Skip zero balances
            if amount == 0:
                continue

            line = AccountLine(
                account_code=account_code,
                account_name=account_name,
                amount=abs(amount)
            )

            # Categorize based on account code patterns
            if account_type == 'INCOME':
                if account_code.startswith('4-1'):  # Sales Revenue
                    report.revenue.lines.append(line)
                    report.total_revenue += abs(amount)
                elif account_code.startswith('4-9'):  # Other Income
                    report.other_income.lines.append(line)
                    report.total_other_income += abs(amount)
                else:
                    report.revenue.lines.append(line)
                    report.total_revenue += abs(amount)
            else:  # EXPENSE
                if account_code.startswith('5-1'):  # COGS
                    report.cogs.lines.append(line)
                    report.total_cogs += abs(amount)
                elif account_code.startswith('5-9'):  # Other Expenses
                    report.other_expenses.lines.append(line)
                    report.total_other_expenses += abs(amount)
                elif account_code.startswith('5-8'):  # Tax
                    report.tax_expense += abs(amount)
                else:  # Operating Expenses (5-2 to 5-7)
                    report.operating_expenses.lines.append(line)
                    report.total_operating_expenses += abs(amount)

        # Calculate derived values
        report.gross_profit = report.total_revenue - report.total_cogs
        report.operating_income = report.gross_profit - report.total_operating_expenses
        report.net_income_before_tax = (
            report.operating_income
            + report.total_other_income
            - report.total_other_expenses
        )
        report.net_income = report.net_income_before_tax - report.tax_expense

        return report

    async def generate_comparative(
        self,
        tenant_id: str,
        period1_start: date,
        period1_end: date,
        period2_start: date,
        period2_end: date,
        company_name: str = ""
    ) -> Dict:
        """
        Generate comparative P&L report for two periods.

        Returns dictionary with both periods and variance.
        """
        report1 = await self.generate(
            tenant_id, period1_start, period1_end, company_name
        )
        report2 = await self.generate(
            tenant_id, period2_start, period2_end, company_name
        )

        return {
            "period1": report1.to_dict(),
            "period2": report2.to_dict(),
            "variance": {
                "total_revenue": float(report1.total_revenue - report2.total_revenue),
                "gross_profit": float(report1.gross_profit - report2.gross_profit),
                "operating_income": float(report1.operating_income - report2.operating_income),
                "net_income": float(report1.net_income - report2.net_income),
                "revenue_change_pct": (
                    float((report1.total_revenue - report2.total_revenue) / report2.total_revenue * 100)
                    if report2.total_revenue != 0 else 0
                ),
                "net_income_change_pct": (
                    float((report1.net_income - report2.net_income) / abs(report2.net_income) * 100)
                    if report2.net_income != 0 else 0
                )
            }
        }
