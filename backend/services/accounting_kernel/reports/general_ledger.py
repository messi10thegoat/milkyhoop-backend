"""
General Ledger Report Generator
===============================

Generates detailed General Ledger report showing all journal entries
with running balances for each account.
"""
from datetime import date, datetime
from decimal import Decimal
from typing import List, Dict, Optional
from dataclasses import dataclass, field
from uuid import UUID

import asyncpg

from ..constants import AccountType, NormalBalance


@dataclass
class LedgerTransaction:
    """Single transaction in the general ledger"""
    journal_date: date
    journal_number: str
    description: str
    debit: Decimal = Decimal("0")
    credit: Decimal = Decimal("0")
    balance: Decimal = Decimal("0")
    source_type: Optional[str] = None
    memo: Optional[str] = None


@dataclass
class AccountLedger:
    """Ledger for a single account"""
    account_code: str
    account_name: str
    account_type: AccountType
    normal_balance: NormalBalance
    opening_balance: Decimal = Decimal("0")
    transactions: List[LedgerTransaction] = field(default_factory=list)
    total_debit: Decimal = Decimal("0")
    total_credit: Decimal = Decimal("0")
    closing_balance: Decimal = Decimal("0")


@dataclass
class GeneralLedgerReport:
    """
    General Ledger Report

    Shows detailed transaction history for all accounts
    with running balances.
    """
    tenant_id: str
    period_start: date
    period_end: date
    company_name: str = ""
    accounts: List[AccountLedger] = field(default_factory=list)
    grand_total_debit: Decimal = Decimal("0")
    grand_total_credit: Decimal = Decimal("0")
    generated_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> Dict:
        """Convert to dictionary for JSON serialization"""
        return {
            "tenant_id": str(self.tenant_id),
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "company_name": self.company_name,
            "accounts": [
                {
                    "account_code": a.account_code,
                    "account_name": a.account_name,
                    "account_type": a.account_type.value,
                    "normal_balance": a.normal_balance.value,
                    "opening_balance": float(a.opening_balance),
                    "transactions": [
                        {
                            "journal_date": t.journal_date.isoformat(),
                            "journal_number": t.journal_number,
                            "description": t.description,
                            "debit": float(t.debit),
                            "credit": float(t.credit),
                            "balance": float(t.balance),
                            "source_type": t.source_type,
                            "memo": t.memo
                        }
                        for t in a.transactions
                    ],
                    "total_debit": float(a.total_debit),
                    "total_credit": float(a.total_credit),
                    "closing_balance": float(a.closing_balance)
                }
                for a in self.accounts
            ],
            "grand_total_debit": float(self.grand_total_debit),
            "grand_total_credit": float(self.grand_total_credit),
            "generated_at": self.generated_at.isoformat()
        }


class GeneralLedgerGenerator:
    """
    Generates General Ledger report with full transaction details.
    """

    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    async def generate(
        self,
        tenant_id: str,
        period_start: date,
        period_end: date,
        company_name: str = "",
        account_codes: Optional[List[str]] = None,
        account_type: Optional[AccountType] = None
    ) -> GeneralLedgerReport:
        """
        Generate General Ledger report.

        Args:
            tenant_id: Tenant UUID
            period_start: Start date
            period_end: End date
            company_name: Company name for header
            account_codes: Filter by specific account codes
            account_type: Filter by account type

        Returns:
            GeneralLedgerReport with all accounts and transactions
        """
        # Get accounts query
        accounts_query = """
            SELECT
                id,
                account_code,
                name,
                account_type,
                normal_balance
            FROM chart_of_accounts
            WHERE tenant_id = $1
                AND is_active = true
                AND ($2::text[] IS NULL OR account_code = ANY($2))
                AND ($3::text IS NULL OR account_type = $3)
            ORDER BY account_code
        """

        # Opening balance query (before period start)
        opening_query = """
            SELECT
                COALESCE(SUM(jl.debit), 0) as total_debit,
                COALESCE(SUM(jl.credit), 0) as total_credit
            FROM journal_lines jl
            JOIN journal_entries je ON je.id = jl.journal_id
            WHERE je.tenant_id = $1
                AND jl.account_id = $2
                AND je.journal_date < $3
                AND je.status = 'POSTED'
        """

        # Transactions query
        transactions_query = """
            SELECT
                je.journal_date,
                je.journal_number,
                je.description,
                je.source_type,
                jl.debit,
                jl.credit,
                jl.memo
            FROM journal_lines jl
            JOIN journal_entries je ON je.id = jl.journal_id
            WHERE je.tenant_id = $1
                AND jl.account_id = $2
                AND je.journal_date BETWEEN $3 AND $4
                AND je.status = 'POSTED'
            ORDER BY je.journal_date, je.journal_number
        """

        report = GeneralLedgerReport(
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

            # Get accounts
            account_rows = await conn.fetch(
                accounts_query,
                tenant_id,
                account_codes,
                account_type.value if account_type else None
            )

            for acc_row in account_rows:
                account_id = acc_row['id']
                normal_balance = NormalBalance(acc_row['normal_balance'])

                # Get opening balance
                opening_row = await conn.fetchrow(
                    opening_query,
                    tenant_id,
                    account_id,
                    period_start
                )

                opening_debit = Decimal(str(opening_row['total_debit']))
                opening_credit = Decimal(str(opening_row['total_credit']))

                if normal_balance == NormalBalance.DEBIT:
                    opening_balance = opening_debit - opening_credit
                else:
                    opening_balance = opening_credit - opening_debit

                # Create account ledger
                account_ledger = AccountLedger(
                    account_code=acc_row['account_code'],
                    account_name=acc_row['name'],
                    account_type=AccountType(acc_row['account_type']),
                    normal_balance=normal_balance,
                    opening_balance=opening_balance
                )

                # Get transactions
                trans_rows = await conn.fetch(
                    transactions_query,
                    tenant_id,
                    account_id,
                    period_start,
                    period_end
                )

                running_balance = opening_balance

                for trans_row in trans_rows:
                    debit = Decimal(str(trans_row['debit']))
                    credit = Decimal(str(trans_row['credit']))

                    # Update running balance
                    if normal_balance == NormalBalance.DEBIT:
                        running_balance += debit - credit
                    else:
                        running_balance += credit - debit

                    transaction = LedgerTransaction(
                        journal_date=trans_row['journal_date'],
                        journal_number=trans_row['journal_number'],
                        description=trans_row['description'],
                        debit=debit,
                        credit=credit,
                        balance=running_balance,
                        source_type=trans_row['source_type'],
                        memo=trans_row['memo']
                    )

                    account_ledger.transactions.append(transaction)
                    account_ledger.total_debit += debit
                    account_ledger.total_credit += credit

                account_ledger.closing_balance = running_balance

                # Only include accounts with activity or balance
                if (account_ledger.transactions or
                        account_ledger.opening_balance != 0):
                    report.accounts.append(account_ledger)
                    report.grand_total_debit += account_ledger.total_debit
                    report.grand_total_credit += account_ledger.total_credit

        return report

    async def generate_for_account(
        self,
        tenant_id: str,
        account_code: str,
        period_start: date,
        period_end: date
    ) -> Optional[AccountLedger]:
        """
        Generate ledger for a single account.

        Args:
            tenant_id: Tenant UUID
            account_code: Account code
            period_start: Start date
            period_end: End date

        Returns:
            AccountLedger or None if account not found
        """
        report = await self.generate(
            tenant_id=tenant_id,
            period_start=period_start,
            period_end=period_end,
            account_codes=[account_code]
        )

        if report.accounts:
            return report.accounts[0]
        return None

    async def get_account_summary(
        self,
        tenant_id: str,
        period_start: date,
        period_end: date,
        account_type: Optional[AccountType] = None
    ) -> List[Dict]:
        """
        Get summary of account activity (no transaction details).

        Returns list of accounts with opening/closing balances and totals.
        """
        query = """
            WITH account_activity AS (
                SELECT
                    c.account_code,
                    c.name as account_name,
                    c.account_type,
                    c.normal_balance,
                    -- Opening balance (before period)
                    COALESCE(SUM(
                        CASE WHEN je.journal_date < $3 THEN jl.debit ELSE 0 END
                    ), 0) as opening_debit,
                    COALESCE(SUM(
                        CASE WHEN je.journal_date < $3 THEN jl.credit ELSE 0 END
                    ), 0) as opening_credit,
                    -- Period activity
                    COALESCE(SUM(
                        CASE WHEN je.journal_date BETWEEN $3 AND $4 THEN jl.debit ELSE 0 END
                    ), 0) as period_debit,
                    COALESCE(SUM(
                        CASE WHEN je.journal_date BETWEEN $3 AND $4 THEN jl.credit ELSE 0 END
                    ), 0) as period_credit
                FROM chart_of_accounts c
                LEFT JOIN journal_lines jl ON jl.account_id = c.id
                LEFT JOIN journal_entries je ON je.id = jl.journal_id
                    AND je.tenant_id = $1
                    AND je.journal_date <= $4
                    AND je.status = 'POSTED'
                WHERE c.tenant_id = $1
                    AND c.is_active = true
                    AND ($2::text IS NULL OR c.account_type = $2)
                GROUP BY c.id, c.account_code, c.name, c.account_type, c.normal_balance
                HAVING (
                    SUM(jl.debit) IS NOT NULL
                    OR SUM(jl.credit) IS NOT NULL
                )
                ORDER BY c.account_code
            )
            SELECT
                account_code,
                account_name,
                account_type,
                normal_balance,
                CASE
                    WHEN normal_balance = 'DEBIT' THEN opening_debit - opening_credit
                    ELSE opening_credit - opening_debit
                END as opening_balance,
                period_debit,
                period_credit,
                CASE
                    WHEN normal_balance = 'DEBIT' THEN
                        (opening_debit + period_debit) - (opening_credit + period_credit)
                    ELSE
                        (opening_credit + period_credit) - (opening_debit + period_debit)
                END as closing_balance
            FROM account_activity
        """

        async with self.pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true)",
                str(tenant_id)
            )

            rows = await conn.fetch(
                query,
                tenant_id,
                account_type.value if account_type else None,
                period_start,
                period_end
            )

        return [
            {
                "account_code": row['account_code'],
                "account_name": row['account_name'],
                "account_type": row['account_type'],
                "normal_balance": row['normal_balance'],
                "opening_balance": float(row['opening_balance']),
                "period_debit": float(row['period_debit']),
                "period_credit": float(row['period_credit']),
                "closing_balance": float(row['closing_balance'])
            }
            for row in rows
        ]

    async def export_to_csv(
        self,
        report: GeneralLedgerReport
    ) -> str:
        """
        Export General Ledger to CSV format.

        Returns CSV string.
        """
        lines = []

        # Header
        lines.append(f"General Ledger Report - {report.company_name}")
        lines.append(f"Period: {report.period_start} to {report.period_end}")
        lines.append("")
        lines.append("Account Code,Account Name,Date,Journal#,Description,Debit,Credit,Balance")

        for account in report.accounts:
            # Account header
            lines.append(
                f"{account.account_code},{account.account_name},"
                f",,Opening Balance,,,{account.opening_balance:.2f}"
            )

            # Transactions
            for trans in account.transactions:
                lines.append(
                    f"{account.account_code},{account.account_name},"
                    f"{trans.journal_date},{trans.journal_number},"
                    f'"{trans.description}",'
                    f"{trans.debit:.2f},{trans.credit:.2f},{trans.balance:.2f}"
                )

            # Account totals
            lines.append(
                f"{account.account_code},{account.account_name},"
                f",,Totals,{account.total_debit:.2f},{account.total_credit:.2f},"
                f"{account.closing_balance:.2f}"
            )
            lines.append("")

        # Grand totals
        lines.append(
            f",,,,Grand Total,"
            f"{report.grand_total_debit:.2f},{report.grand_total_credit:.2f},"
        )

        return "\n".join(lines)
