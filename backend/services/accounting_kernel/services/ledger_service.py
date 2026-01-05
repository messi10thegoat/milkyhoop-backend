"""
Ledger Service (Read Model)
===========================

Provides read operations for accounting data:
- Trial Balance
- Account Ledger (transaction history)
- Account Balances
- General Ledger queries
"""
from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional, Dict, Any, Tuple
from dataclasses import dataclass, field
from uuid import UUID

import asyncpg

from ..constants import AccountType, NormalBalance, JournalStatus
from ..config import settings


@dataclass
class TrialBalanceRow:
    """Single row in Trial Balance report"""
    account_code: str
    account_name: str
    account_type: AccountType
    normal_balance: NormalBalance
    debit_balance: Decimal = Decimal("0")
    credit_balance: Decimal = Decimal("0")

    @property
    def net_balance(self) -> Decimal:
        """Calculate net balance based on normal balance"""
        if self.normal_balance == NormalBalance.DEBIT:
            return self.debit_balance - self.credit_balance
        else:
            return self.credit_balance - self.debit_balance


@dataclass
class TrialBalance:
    """Trial Balance report"""
    tenant_id: str
    as_of_date: date
    rows: List[TrialBalanceRow] = field(default_factory=list)
    total_debit: Decimal = Decimal("0")
    total_credit: Decimal = Decimal("0")
    generated_at: datetime = field(default_factory=datetime.utcnow)

    @property
    def is_balanced(self) -> bool:
        """Check if trial balance is balanced"""
        return abs(self.total_debit - self.total_credit) < Decimal("0.01")

    @property
    def difference(self) -> Decimal:
        """Calculate difference between debits and credits"""
        return abs(self.total_debit - self.total_credit)


@dataclass
class LedgerEntry:
    """Single entry in account ledger"""
    journal_id: UUID
    journal_number: str
    journal_date: date
    description: str
    debit: Decimal
    credit: Decimal
    running_balance: Decimal
    source_type: Optional[str] = None
    source_id: Optional[UUID] = None
    memo: Optional[str] = None
    created_at: Optional[datetime] = None


@dataclass
class AccountLedger:
    """Account Ledger (transaction history for an account)"""
    tenant_id: str
    account_id: UUID
    account_code: str
    account_name: str
    account_type: AccountType
    normal_balance: NormalBalance
    start_date: date
    end_date: date
    opening_balance: Decimal = Decimal("0")
    entries: List[LedgerEntry] = field(default_factory=list)
    closing_balance: Decimal = Decimal("0")
    total_debit: Decimal = Decimal("0")
    total_credit: Decimal = Decimal("0")


@dataclass
class AccountBalanceSummary:
    """Account balance at a point in time"""
    account_id: UUID
    account_code: str
    account_name: str
    account_type: AccountType
    normal_balance: NormalBalance
    debit_total: Decimal = Decimal("0")
    credit_total: Decimal = Decimal("0")
    balance: Decimal = Decimal("0")


class LedgerService:
    """
    Ledger Service - Read model for accounting data.

    Provides efficient queries for:
    - Trial Balance (summary of all account balances)
    - Account Ledger (transaction history per account)
    - Account Balances (current balances)
    """

    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    async def get_trial_balance(
        self,
        tenant_id: str,
        as_of_date: Optional[date] = None,
        account_type: Optional[AccountType] = None,
        include_zero_balances: bool = False
    ) -> TrialBalance:
        """
        Generate Trial Balance report.

        Args:
            tenant_id: Tenant UUID
            as_of_date: Date for balance calculation (default: today)
            account_type: Filter by account type
            include_zero_balances: Include accounts with zero balance

        Returns:
            TrialBalance with all account balances
        """
        if as_of_date is None:
            as_of_date = date.today()

        query = """
            WITH account_totals AS (
                SELECT
                    c.id as account_id,
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
                    AND ($3::text IS NULL OR c.account_type = $3)
                GROUP BY c.id, c.account_code, c.name, c.account_type, c.normal_balance
                ORDER BY c.account_code
            )
            SELECT
                account_code,
                account_name,
                account_type,
                normal_balance,
                CASE
                    WHEN normal_balance = 'DEBIT' THEN
                        GREATEST(total_debit - total_credit, 0)
                    ELSE
                        GREATEST(total_credit - total_debit, 0)
                END as debit_balance,
                CASE
                    WHEN normal_balance = 'CREDIT' THEN
                        GREATEST(total_credit - total_debit, 0)
                    ELSE
                        GREATEST(total_debit - total_credit, 0)
                END as credit_balance,
                total_debit,
                total_credit
            FROM account_totals
            WHERE ($4 = true OR (total_debit != 0 OR total_credit != 0))
        """

        async with self.pool.acquire() as conn:
            # Set tenant context for RLS
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true)",
                str(tenant_id)
            )

            rows = await conn.fetch(
                query,
                tenant_id,
                as_of_date,
                account_type.value if account_type else None,
                include_zero_balances
            )

        trial_balance = TrialBalance(
            tenant_id=tenant_id,
            as_of_date=as_of_date
        )

        for row in rows:
            # Calculate proper debit/credit presentation
            total_debit = Decimal(str(row['total_debit']))
            total_credit = Decimal(str(row['total_credit']))
            normal_balance = NormalBalance(row['normal_balance'])

            net = total_debit - total_credit

            if normal_balance == NormalBalance.DEBIT:
                if net >= 0:
                    debit_balance = net
                    credit_balance = Decimal("0")
                else:
                    debit_balance = Decimal("0")
                    credit_balance = abs(net)
            else:  # CREDIT normal balance
                if net <= 0:
                    credit_balance = abs(net)
                    debit_balance = Decimal("0")
                else:
                    credit_balance = Decimal("0")
                    debit_balance = net

            tb_row = TrialBalanceRow(
                account_code=row['account_code'],
                account_name=row['account_name'],
                account_type=AccountType(row['account_type']),
                normal_balance=normal_balance,
                debit_balance=debit_balance,
                credit_balance=credit_balance
            )

            trial_balance.rows.append(tb_row)
            trial_balance.total_debit += debit_balance
            trial_balance.total_credit += credit_balance

        return trial_balance

    async def get_account_ledger(
        self,
        tenant_id: str,
        account_code: str,
        start_date: date,
        end_date: date,
        include_opening_balance: bool = True
    ) -> AccountLedger:
        """
        Get account ledger (transaction history) for a specific account.

        Args:
            tenant_id: Tenant UUID
            account_code: Account code to query
            start_date: Start date of period
            end_date: End date of period
            include_opening_balance: Calculate opening balance before start_date

        Returns:
            AccountLedger with transaction history
        """
        # Get account info
        account_query = """
            SELECT id, account_code, name, account_type, normal_balance
            FROM chart_of_accounts
            WHERE tenant_id = $1 AND account_code = $2
        """

        # Get opening balance (all transactions before start_date)
        opening_balance_query = """
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

        # Get transactions in period
        entries_query = """
            SELECT
                je.id as journal_id,
                je.journal_number,
                je.journal_date,
                je.description,
                jl.debit,
                jl.credit,
                jl.memo,
                je.source_type,
                je.source_id,
                je.created_at
            FROM journal_lines jl
            JOIN journal_entries je ON je.id = jl.journal_id
            WHERE je.tenant_id = $1
                AND jl.account_id = $2
                AND je.journal_date BETWEEN $3 AND $4
                AND je.status = 'POSTED'
            ORDER BY je.journal_date, je.journal_number
        """

        async with self.pool.acquire() as conn:
            # Set tenant context
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true)",
                str(tenant_id)
            )

            # Get account
            account_row = await conn.fetchrow(account_query, tenant_id, account_code)
            if not account_row:
                raise ValueError(f"Account not found: {account_code}")

            account_id = account_row['id']
            normal_balance = NormalBalance(account_row['normal_balance'])

            # Calculate opening balance
            opening_balance = Decimal("0")
            if include_opening_balance:
                ob_row = await conn.fetchrow(
                    opening_balance_query,
                    tenant_id,
                    account_id,
                    start_date
                )
                if ob_row:
                    ob_debit = Decimal(str(ob_row['total_debit']))
                    ob_credit = Decimal(str(ob_row['total_credit']))
                    if normal_balance == NormalBalance.DEBIT:
                        opening_balance = ob_debit - ob_credit
                    else:
                        opening_balance = ob_credit - ob_debit

            # Get entries
            entry_rows = await conn.fetch(
                entries_query,
                tenant_id,
                account_id,
                start_date,
                end_date
            )

        # Build ledger
        ledger = AccountLedger(
            tenant_id=tenant_id,
            account_id=account_id,
            account_code=account_row['account_code'],
            account_name=account_row['name'],
            account_type=AccountType(account_row['account_type']),
            normal_balance=normal_balance,
            start_date=start_date,
            end_date=end_date,
            opening_balance=opening_balance
        )

        running_balance = opening_balance
        total_debit = Decimal("0")
        total_credit = Decimal("0")

        for row in entry_rows:
            debit = Decimal(str(row['debit']))
            credit = Decimal(str(row['credit']))

            # Update running balance based on normal balance
            if normal_balance == NormalBalance.DEBIT:
                running_balance += debit - credit
            else:
                running_balance += credit - debit

            total_debit += debit
            total_credit += credit

            entry = LedgerEntry(
                journal_id=row['journal_id'],
                journal_number=row['journal_number'],
                journal_date=row['journal_date'],
                description=row['description'],
                debit=debit,
                credit=credit,
                running_balance=running_balance,
                source_type=row['source_type'],
                source_id=row['source_id'],
                memo=row['memo'],
                created_at=row['created_at']
            )
            ledger.entries.append(entry)

        ledger.total_debit = total_debit
        ledger.total_credit = total_credit
        ledger.closing_balance = running_balance

        return ledger

    async def get_account_balance(
        self,
        tenant_id: str,
        account_code: str,
        as_of_date: Optional[date] = None
    ) -> AccountBalanceSummary:
        """
        Get balance for a specific account.

        Args:
            tenant_id: Tenant UUID
            account_code: Account code
            as_of_date: Date for balance (default: today)

        Returns:
            AccountBalanceSummary
        """
        if as_of_date is None:
            as_of_date = date.today()

        query = """
            SELECT
                c.id as account_id,
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
                AND je.journal_date <= $3
                AND je.status = 'POSTED'
            WHERE c.tenant_id = $1 AND c.account_code = $2
            GROUP BY c.id, c.account_code, c.name, c.account_type, c.normal_balance
        """

        async with self.pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true)",
                str(tenant_id)
            )

            row = await conn.fetchrow(query, tenant_id, account_code, as_of_date)

        if not row:
            raise ValueError(f"Account not found: {account_code}")

        total_debit = Decimal(str(row['total_debit']))
        total_credit = Decimal(str(row['total_credit']))
        normal_balance = NormalBalance(row['normal_balance'])

        # Calculate balance based on normal balance
        if normal_balance == NormalBalance.DEBIT:
            balance = total_debit - total_credit
        else:
            balance = total_credit - total_debit

        return AccountBalanceSummary(
            account_id=row['account_id'],
            account_code=row['account_code'],
            account_name=row['account_name'],
            account_type=AccountType(row['account_type']),
            normal_balance=normal_balance,
            debit_total=total_debit,
            credit_total=total_credit,
            balance=balance
        )

    async def get_all_account_balances(
        self,
        tenant_id: str,
        as_of_date: Optional[date] = None,
        account_type: Optional[AccountType] = None
    ) -> List[AccountBalanceSummary]:
        """
        Get balances for all accounts.

        Args:
            tenant_id: Tenant UUID
            as_of_date: Date for balance (default: today)
            account_type: Filter by account type

        Returns:
            List of AccountBalanceSummary
        """
        if as_of_date is None:
            as_of_date = date.today()

        query = """
            SELECT
                c.id as account_id,
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
                AND ($3::text IS NULL OR c.account_type = $3)
            GROUP BY c.id, c.account_code, c.name, c.account_type, c.normal_balance
            ORDER BY c.account_code
        """

        async with self.pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true)",
                str(tenant_id)
            )

            rows = await conn.fetch(
                query,
                tenant_id,
                as_of_date,
                account_type.value if account_type else None
            )

        balances = []
        for row in rows:
            total_debit = Decimal(str(row['total_debit']))
            total_credit = Decimal(str(row['total_credit']))
            normal_balance = NormalBalance(row['normal_balance'])

            if normal_balance == NormalBalance.DEBIT:
                balance = total_debit - total_credit
            else:
                balance = total_credit - total_debit

            balances.append(AccountBalanceSummary(
                account_id=row['account_id'],
                account_code=row['account_code'],
                account_name=row['account_name'],
                account_type=AccountType(row['account_type']),
                normal_balance=normal_balance,
                debit_total=total_debit,
                credit_total=total_credit,
                balance=balance
            ))

        return balances

    async def get_balance_from_cache(
        self,
        tenant_id: str,
        account_id: UUID,
        as_of_date: date
    ) -> Optional[Tuple[Decimal, Decimal]]:
        """
        Get balance from account_balances_daily cache table.

        Args:
            tenant_id: Tenant UUID
            account_id: Account UUID
            as_of_date: Date for balance

        Returns:
            Tuple of (debit_balance, credit_balance) or None if not cached
        """
        query = """
            SELECT debit_balance, credit_balance
            FROM account_balances_daily
            WHERE tenant_id = $1
                AND account_id = $2
                AND balance_date = $3
        """

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(query, tenant_id, account_id, as_of_date)

        if row:
            return (
                Decimal(str(row['debit_balance'])),
                Decimal(str(row['credit_balance']))
            )
        return None

    async def get_journals_by_source(
        self,
        tenant_id: str,
        source_type: str,
        source_id: UUID
    ) -> List[Dict[str, Any]]:
        """
        Get all journal entries for a specific source document.

        Args:
            tenant_id: Tenant UUID
            source_type: Source type (e.g., 'INVOICE', 'BILL', 'POS')
            source_id: Source document UUID

        Returns:
            List of journal entry dictionaries
        """
        query = """
            SELECT
                je.id,
                je.journal_number,
                je.journal_date,
                je.description,
                je.status,
                je.total_debit,
                je.total_credit,
                je.created_at,
                json_agg(json_build_object(
                    'account_code', c.account_code,
                    'account_name', c.name,
                    'debit', jl.debit,
                    'credit', jl.credit,
                    'memo', jl.memo
                ) ORDER BY jl.line_number) as lines
            FROM journal_entries je
            JOIN journal_lines jl ON jl.journal_id = je.id
            JOIN chart_of_accounts c ON c.id = jl.account_id
            WHERE je.tenant_id = $1
                AND je.source_type = $2
                AND je.source_id = $3
            GROUP BY je.id
            ORDER BY je.journal_date, je.journal_number
        """

        async with self.pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true)",
                str(tenant_id)
            )

            rows = await conn.fetch(query, tenant_id, source_type, str(source_id))

        return [dict(row) for row in rows]

    async def get_period_totals(
        self,
        tenant_id: str,
        start_date: date,
        end_date: date,
        account_type: Optional[AccountType] = None
    ) -> Dict[str, Decimal]:
        """
        Get period totals for reporting.

        Args:
            tenant_id: Tenant UUID
            start_date: Period start date
            end_date: Period end date
            account_type: Filter by account type

        Returns:
            Dictionary with total_debit, total_credit, and net
        """
        query = """
            SELECT
                COALESCE(SUM(jl.debit), 0) as total_debit,
                COALESCE(SUM(jl.credit), 0) as total_credit
            FROM journal_lines jl
            JOIN journal_entries je ON je.id = jl.journal_id
            JOIN chart_of_accounts c ON c.id = jl.account_id
            WHERE je.tenant_id = $1
                AND je.journal_date BETWEEN $2 AND $3
                AND je.status = 'POSTED'
                AND ($4::text IS NULL OR c.account_type = $4)
        """

        async with self.pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true)",
                str(tenant_id)
            )

            row = await conn.fetchrow(
                query,
                tenant_id,
                start_date,
                end_date,
                account_type.value if account_type else None
            )

        total_debit = Decimal(str(row['total_debit']))
        total_credit = Decimal(str(row['total_credit']))

        return {
            "total_debit": total_debit,
            "total_credit": total_credit,
            "net": total_debit - total_credit
        }
