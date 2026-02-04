"""
Accounting Facade
=================

Provides a simplified, unified interface to the Accounting Kernel
for use by other services.

This is the primary interface that external services should use
to interact with the accounting system.
"""
from datetime import date
from decimal import Decimal
from typing import Dict, Any, List, Optional
from uuid import UUID

import asyncpg

from ..services import (
    CoAService,
    JournalService,
    LedgerService,
    ARService,
    APService,
    AutoPostingService,
    FiscalPeriodService
)
from ..models.fiscal_period import (
    CreatePeriodRequest,
    ClosePeriodRequest,
    LockPeriodRequest,
    UnlockPeriodRequest
)
from ..constants import PeriodStatus
from ..reports import (
    ProfitLossGenerator,
    BalanceSheetGenerator,
    CashFlowGenerator,
    GeneralLedgerGenerator
)


class AccountingFacade:
    """
    Unified facade for the Accounting Kernel.

    Provides simplified methods for:
    - Recording transactions
    - Generating reports
    - Querying account balances
    - Managing AR/AP
    """

    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool
        self._initialized = False

    async def _ensure_initialized(self):
        """Lazily initialize all services."""
        if not self._initialized:
            self.coa = CoAService(self.pool)
            self.journal = JournalService(self.pool, self.coa)
            self.ledger = LedgerService(self.pool)
            self.ar = ARService(self.pool, self.journal)
            self.ap = APService(self.pool, self.journal)
            self.auto_posting = AutoPostingService(self.pool, self.journal)
            self.fiscal_period = FiscalPeriodService(self.pool)

            self.profit_loss = ProfitLossGenerator(self.pool)
            self.balance_sheet = BalanceSheetGenerator(self.pool)
            self.cash_flow = CashFlowGenerator(self.pool)
            self.general_ledger = GeneralLedgerGenerator(self.pool)

            self._initialized = True

    # ==================== Chart of Accounts ====================

    async def setup_chart_of_accounts(self, tenant_id: str) -> int:
        """
        Set up default Chart of Accounts for a new tenant.

        Returns count of accounts created.
        """
        await self._ensure_initialized()
        return await self.coa.seed_default_accounts(tenant_id)

    async def get_accounts(
        self,
        tenant_id: str,
        account_type: Optional[str] = None
    ) -> List[Dict]:
        """Get list of accounts for a tenant."""
        await self._ensure_initialized()
        accounts = await self.coa.list_accounts(tenant_id, account_type=account_type)
        return [acc.to_dict() for acc in accounts]

    async def get_account_balance(
        self,
        tenant_id: str,
        account_code: str,
        as_of_date: Optional[date] = None
    ) -> Dict:
        """Get balance for a specific account."""
        await self._ensure_initialized()
        balance = await self.ledger.get_account_balance(
            tenant_id, account_code, as_of_date
        )
        return {
            "account_code": balance.account_code,
            "account_name": balance.account_name,
            "balance": float(balance.balance),
            "debit_total": float(balance.debit_total),
            "credit_total": float(balance.credit_total)
        }

    # ==================== Transaction Recording ====================

    async def record_sale(
        self,
        tenant_id: str,
        transaction_id: UUID,
        amount: Decimal,
        payment_method: str,
        customer_name: str = "Customer",
        transaction_date: Optional[date] = None,
        description: Optional[str] = None
    ) -> Dict:
        """
        Record a sale transaction.

        Returns journal entry details.
        """
        await self._ensure_initialized()

        result = await self.auto_posting.post_pos_sale(
            tenant_id=tenant_id,
            transaction_date=transaction_date or date.today(),
            transaction_id=transaction_id,
            total_amount=amount,
            payment_method=payment_method,
            customer_name=customer_name,
            description=description
        )

        return {
            "success": result.success,
            "journal_id": str(result.journal_id) if result.journal_id else None,
            "journal_number": result.journal_number,
            "error": result.error
        }

    async def record_purchase(
        self,
        tenant_id: str,
        transaction_id: UUID,
        amount: Decimal,
        payment_method: str,
        supplier_name: str,
        transaction_date: Optional[date] = None,
        description: Optional[str] = None,
        is_inventory: bool = True
    ) -> Dict:
        """
        Record a purchase transaction (kulakan).

        Returns journal entry details.
        """
        await self._ensure_initialized()

        result = await self.auto_posting.post_purchase(
            tenant_id=tenant_id,
            transaction_date=transaction_date or date.today(),
            transaction_id=transaction_id,
            total_amount=amount,
            payment_method=payment_method,
            supplier_name=supplier_name,
            description=description,
            is_inventory=is_inventory
        )

        return {
            "success": result.success,
            "journal_id": str(result.journal_id) if result.journal_id else None,
            "journal_number": result.journal_number,
            "error": result.error
        }

    async def record_expense(
        self,
        tenant_id: str,
        expense_id: UUID,
        expense_account: str,
        amount: Decimal,
        payment_method: str,
        description: str,
        expense_date: Optional[date] = None,
        vendor_name: Optional[str] = None
    ) -> Dict:
        """Record an expense."""
        await self._ensure_initialized()

        result = await self.auto_posting.post_expense(
            tenant_id=tenant_id,
            expense_date=expense_date or date.today(),
            expense_id=expense_id,
            expense_account=expense_account,
            amount=amount,
            payment_method=payment_method,
            description=description,
            vendor_name=vendor_name
        )

        return {
            "success": result.success,
            "journal_id": str(result.journal_id) if result.journal_id else None,
            "journal_number": result.journal_number,
            "error": result.error
        }

    # ==================== Reports ====================

    async def get_profit_loss(
        self,
        tenant_id: str,
        period_start: date,
        period_end: date,
        company_name: str = ""
    ) -> Dict:
        """Generate Profit & Loss report."""
        await self._ensure_initialized()
        report = await self.profit_loss.generate(
            tenant_id, period_start, period_end, company_name
        )
        return report.to_dict()

    async def get_balance_sheet(
        self,
        tenant_id: str,
        as_of_date: date,
        company_name: str = ""
    ) -> Dict:
        """Generate Balance Sheet report."""
        await self._ensure_initialized()
        report = await self.balance_sheet.generate(
            tenant_id, as_of_date, company_name
        )
        return report.to_dict()

    async def get_cash_flow(
        self,
        tenant_id: str,
        period_start: date,
        period_end: date,
        company_name: str = ""
    ) -> Dict:
        """Generate Cash Flow Statement."""
        await self._ensure_initialized()
        report = await self.cash_flow.generate(
            tenant_id, period_start, period_end, company_name
        )
        return report.to_dict()

    async def get_trial_balance(
        self,
        tenant_id: str,
        as_of_date: Optional[date] = None
    ) -> Dict:
        """Generate Trial Balance."""
        await self._ensure_initialized()
        report = await self.ledger.get_trial_balance(
            tenant_id, as_of_date
        )
        return {
            "as_of_date": report.as_of_date.isoformat(),
            "rows": [
                {
                    "account_code": row.account_code,
                    "account_name": row.account_name,
                    "debit_balance": float(row.debit_balance),
                    "credit_balance": float(row.credit_balance)
                }
                for row in report.rows
            ],
            "total_debit": float(report.total_debit),
            "total_credit": float(report.total_credit),
            "is_balanced": report.is_balanced
        }

    async def get_general_ledger(
        self,
        tenant_id: str,
        period_start: date,
        period_end: date,
        account_code: Optional[str] = None,
        company_name: str = ""
    ) -> Dict:
        """Generate General Ledger report."""
        await self._ensure_initialized()

        account_codes = [account_code] if account_code else None

        report = await self.general_ledger.generate(
            tenant_id, period_start, period_end, company_name,
            account_codes=account_codes
        )
        return report.to_dict()

    async def get_account_ledger(
        self,
        tenant_id: str,
        account_code: str,
        start_date: date,
        end_date: date
    ) -> Dict:
        """Get ledger (transaction history) for a specific account."""
        await self._ensure_initialized()

        ledger = await self.ledger.get_account_ledger(
            tenant_id, account_code, start_date, end_date
        )

        return {
            "account_code": ledger.account_code,
            "account_name": ledger.account_name,
            "opening_balance": float(ledger.opening_balance),
            "entries": [
                {
                    "date": entry.journal_date.isoformat(),
                    "journal_number": entry.journal_number,
                    "description": entry.description,
                    "debit": float(entry.debit),
                    "credit": float(entry.credit),
                    "balance": float(entry.running_balance)
                }
                for entry in ledger.entries
            ],
            "total_debit": float(ledger.total_debit),
            "total_credit": float(ledger.total_credit),
            "closing_balance": float(ledger.closing_balance)
        }

    # ==================== AR/AP ====================

    async def get_ar_aging(
        self,
        tenant_id: str,
        as_of_date: Optional[date] = None
    ) -> Dict:
        """Get AR Aging report."""
        await self._ensure_initialized()
        report = await self.ar.get_aging_report(tenant_id, as_of_date)
        return {
            "as_of_date": report.as_of_date.isoformat(),
            "rows": [
                {
                    "customer_name": row.customer_name,
                    "current": float(row.current),
                    "1-30": float(row.days_1_30),
                    "31-60": float(row.days_31_60),
                    "61-90": float(row.days_61_90),
                    "90+": float(row.days_over_90),
                    "total": float(row.total)
                }
                for row in report.rows
            ],
            "totals": {
                "current": float(report.totals.current),
                "1-30": float(report.totals.days_1_30),
                "31-60": float(report.totals.days_31_60),
                "61-90": float(report.totals.days_61_90),
                "90+": float(report.totals.days_over_90),
                "total": float(report.totals.total)
            }
        }

    async def get_ap_aging(
        self,
        tenant_id: str,
        as_of_date: Optional[date] = None
    ) -> Dict:
        """Get AP Aging report."""
        await self._ensure_initialized()
        report = await self.ap.get_aging_report(tenant_id, as_of_date)
        return {
            "as_of_date": report.as_of_date.isoformat(),
            "rows": [
                {
                    "supplier_name": row.supplier_name,
                    "current": float(row.current),
                    "1-30": float(row.days_1_30),
                    "31-60": float(row.days_31_60),
                    "61-90": float(row.days_61_90),
                    "90+": float(row.days_over_90),
                    "total": float(row.total)
                }
                for row in report.rows
            ],
            "totals": {
                "current": float(report.totals.current),
                "1-30": float(report.totals.days_1_30),
                "31-60": float(report.totals.days_31_60),
                "61-90": float(report.totals.days_61_90),
                "90+": float(report.totals.days_over_90),
                "total": float(report.totals.total)
            }
        }

    async def get_outstanding_ar(self, tenant_id: str) -> Decimal:
        """Get total outstanding AR balance."""
        await self._ensure_initialized()
        return await self.ar.get_total_outstanding(tenant_id)

    async def get_outstanding_ap(self, tenant_id: str) -> Decimal:
        """Get total outstanding AP balance."""
        await self._ensure_initialized()
        return await self.ap.get_total_outstanding(tenant_id)

    # ==================== Quick Dashboard Metrics ====================

    async def get_dashboard_metrics(
        self,
        tenant_id: str,
        period_start: date,
        period_end: date
    ) -> Dict:
        """
        Get key metrics for dashboard display.

        Returns summary of:
        - Revenue and expenses
        - Net income
        - Cash balance
        - AR/AP balances
        """
        await self._ensure_initialized()

        # Get P&L summary
        pl = await self.profit_loss.generate(tenant_id, period_start, period_end)

        # Get cash balance
        try:
            cash = await self.ledger.get_account_balance(tenant_id, "1-10100")
            cash_balance = cash.balance
        except ValueError:
            cash_balance = Decimal("0")

        # Get AR/AP
        ar_balance = await self.ar.get_total_outstanding(tenant_id)
        ap_balance = await self.ap.get_total_outstanding(tenant_id)

        return {
            "period": {
                "start": period_start.isoformat(),
                "end": period_end.isoformat()
            },
            "revenue": float(pl.total_revenue),
            "expenses": float(pl.total_operating_expenses + pl.total_cogs),
            "gross_profit": float(pl.gross_profit),
            "net_income": float(pl.net_income),
            "cash_balance": float(cash_balance),
            "accounts_receivable": float(ar_balance),
            "accounts_payable": float(ap_balance),
            "working_capital": float(cash_balance + ar_balance - ap_balance)
        }

    # ==================== Fiscal Period Management ====================

    async def create_period(
        self,
        tenant_id: str,
        period_name: str,
        start_date: date,
        end_date: date,
        created_by: Optional[UUID] = None
    ) -> Dict:
        """
        Create a new fiscal period.

        Args:
            tenant_id: Tenant identifier
            period_name: Period name (e.g., "2026-01")
            start_date: Period start date
            end_date: Period end date
            created_by: User creating the period

        Returns:
            Dict with success status and period details
        """
        await self._ensure_initialized()

        request = CreatePeriodRequest(
            tenant_id=tenant_id,
            period_name=period_name,
            start_date=start_date,
            end_date=end_date,
            created_by=created_by
        )

        result = await self.fiscal_period.create_period(request)
        return {
            "success": result.success,
            "period_id": str(result.period_id) if result.period_id else None,
            "period_name": result.period_name,
            "message": result.message,
            "errors": result.errors
        }

    async def close_period(
        self,
        tenant_id: str,
        period_name: str,
        closed_by: UUID,
        create_closing_entries: bool = True
    ) -> Dict:
        """
        Close a fiscal period (OPEN → CLOSED).

        Creates closing journal entries and snapshots account balances.

        Args:
            tenant_id: Tenant identifier
            period_name: Period name to close (e.g., "2026-01")
            closed_by: User closing the period
            create_closing_entries: Whether to create closing entries

        Returns:
            Dict with closing details including snapshot
        """
        await self._ensure_initialized()

        request = ClosePeriodRequest(
            tenant_id=tenant_id,
            period_name=period_name,
            closed_by=closed_by,
            create_closing_entries=create_closing_entries
        )

        result = await self.fiscal_period.close_period(request)
        return {
            "success": result.success,
            "period_id": str(result.period_id) if result.period_id else None,
            "period_name": result.period_name,
            "closing_journal_id": str(result.closing_journal_id) if result.closing_journal_id else None,
            "closing_snapshot": result.closing_snapshot,
            "message": result.message,
            "errors": result.errors
        }

    async def lock_period(
        self,
        tenant_id: str,
        period_id: UUID,
        locked_by: UUID,
        reason: str = ""
    ) -> Dict:
        """
        Lock a fiscal period (CLOSED → LOCKED).

        A locked period is immutable - no entries can be added or modified.

        Args:
            tenant_id: Tenant identifier
            period_id: Period UUID to lock
            locked_by: User locking the period
            reason: Reason for locking (e.g., "Audit finalized")

        Returns:
            Dict with lock status
        """
        await self._ensure_initialized()

        request = LockPeriodRequest(
            tenant_id=tenant_id,
            period_id=period_id,
            locked_by=locked_by,
            reason=reason
        )

        result = await self.fiscal_period.lock_period(request)
        return {
            "success": result.success,
            "period_id": str(result.period_id) if result.period_id else None,
            "period_name": result.period_name,
            "locked_at": result.locked_at.isoformat() if result.locked_at else None,
            "message": result.message,
            "errors": result.errors
        }

    async def unlock_period(
        self,
        tenant_id: str,
        period_id: UUID,
        unlocked_by: UUID,
        reason: str
    ) -> Dict:
        """
        Unlock a fiscal period (LOCKED → CLOSED).

        This is an admin-only operation that requires a reason.

        Args:
            tenant_id: Tenant identifier
            period_id: Period UUID to unlock
            unlocked_by: Admin unlocking the period
            reason: Required reason for unlocking

        Returns:
            Dict with unlock status
        """
        await self._ensure_initialized()

        request = UnlockPeriodRequest(
            tenant_id=tenant_id,
            period_id=period_id,
            unlocked_by=unlocked_by,
            reason=reason
        )

        result = await self.fiscal_period.unlock_period(request)
        return {
            "success": result.success,
            "period_id": str(result.period_id) if result.period_id else None,
            "period_name": result.period_name,
            "message": result.message,
            "errors": result.errors
        }

    async def get_period_status(
        self,
        tenant_id: str,
        target_date: date
    ) -> Dict:
        """
        Get period status for a specific date.

        Args:
            tenant_id: Tenant identifier
            target_date: Date to check

        Returns:
            Dict with period info or None if no period defined
        """
        await self._ensure_initialized()

        period = await self.fiscal_period.get_period_by_date(tenant_id, target_date)

        if not period:
            return {
                "period_defined": False,
                "can_post": True,
                "message": "No period defined for this date"
            }

        return {
            "period_defined": True,
            "period_id": str(period.id),
            "period_name": period.period_name,
            "status": period.status.value,
            "is_open": period.is_open,
            "is_closed": period.is_closed,
            "is_locked": period.is_locked,
            "can_manual_post": period.can_manual_post,
            "can_system_post": period.can_system_post,
            "start_date": period.start_date.isoformat(),
            "end_date": period.end_date.isoformat()
        }

    async def list_periods(
        self,
        tenant_id: str,
        status: Optional[str] = None
    ) -> List[Dict]:
        """
        List all fiscal periods for a tenant.

        Args:
            tenant_id: Tenant identifier
            status: Optional filter by status (OPEN, CLOSED, LOCKED)

        Returns:
            List of period dicts
        """
        await self._ensure_initialized()

        period_status = PeriodStatus(status) if status else None
        periods = await self.fiscal_period.list_periods(tenant_id, period_status)

        return [period.to_dict() for period in periods]

    async def can_post_to_date(
        self,
        tenant_id: str,
        target_date: date,
        is_system_generated: bool = False
    ) -> Dict:
        """
        Check if posting is allowed for a specific date.

        Args:
            tenant_id: Tenant identifier
            target_date: Date to check
            is_system_generated: True if system-generated entry

        Returns:
            Dict with can_post boolean and optional error message
        """
        await self._ensure_initialized()

        can_post, error = await self.fiscal_period.can_post_to_date(
            tenant_id, target_date, is_system_generated
        )

        return {
            "can_post": can_post,
            "error": error
        }

    # ==================== Journal Reversal ====================

    async def reverse_journal(
        self,
        tenant_id: str,
        journal_id: UUID,
        reversal_date: date,
        reversed_by: UUID,
        reason: str
    ) -> Dict:
        """
        Reverse a journal entry (first-class reversal).

        Creates a new journal entry with debit/credit swapped,
        linked to the original via reversal_of_id.

        Rules:
        - Original journal cannot already be reversed
        - Original period cannot be LOCKED
        - Reversal must be posted to OPEN period
        - Reason is mandatory

        Args:
            tenant_id: Tenant identifier
            journal_id: Original journal UUID to reverse
            reversal_date: Date for the reversal entry
            reversed_by: User performing the reversal
            reason: Mandatory reason for reversal

        Returns:
            Dict with success status and reversal journal details
        """
        await self._ensure_initialized()

        result = await self.journal.reverse_journal(
            tenant_id=tenant_id,
            journal_id=journal_id,
            reversal_date=reversal_date,
            reversed_by=reversed_by,
            reason=reason
        )

        return {
            "success": result.success,
            "reversal_journal_id": str(result.journal_id) if result.journal_id else None,
            "reversal_journal_number": result.journal_number,
            "message": result.message,
            "errors": result.errors
        }

    async def get_journal(
        self,
        tenant_id: str,
        journal_id: UUID
    ) -> Optional[Dict]:
        """
        Get a journal entry by ID.

        Args:
            tenant_id: Tenant identifier
            journal_id: Journal UUID

        Returns:
            Dict with journal details or None if not found
        """
        await self._ensure_initialized()

        journal = await self.journal.get_journal(tenant_id, journal_id)
        if journal:
            return journal.to_dict()
        return None

    # ==================== Trial Balance ====================

    async def get_trial_balance(
        self,
        tenant_id: str,
        as_of_date: Optional[date] = None,
        period_id: Optional[UUID] = None
    ) -> Dict:
        """
        Get trial balance as of a specific date.

        Trial balance shows all accounts with their debit/credit totals
        and calculated balance. Essential for period close validation
        and financial reporting.

        Args:
            tenant_id: Tenant identifier
            as_of_date: Date for balance calculation (default: today)
            period_id: Optional period filter

        Returns:
            Dict with trial balance data:
            {
                "as_of_date": "2026-01-31",
                "total_debit": 1000000,
                "total_credit": 1000000,
                "is_balanced": true,
                "accounts": [
                    {
                        "account_code": "1-10100",
                        "account_name": "Kas",
                        "account_type": "ASSET",
                        "total_debit": 500000,
                        "total_credit": 100000,
                        "balance": 400000
                    },
                    ...
                ]
            }
        """
        await self._ensure_initialized()

        if as_of_date is None:
            as_of_date = date.today()

        async with self.pool.acquire() as conn:
            # Use the DB function for efficient calculation
            rows = await conn.fetch(
                "SELECT * FROM get_trial_balance($1, $2, $3)",
                tenant_id,
                as_of_date,
                period_id
            )

            accounts = []
            total_debit = Decimal("0")
            total_credit = Decimal("0")

            for row in rows:
                debit = Decimal(str(row['total_debit']))
                credit = Decimal(str(row['total_credit']))
                balance = Decimal(str(row['balance']))

                accounts.append({
                    "account_id": str(row['account_id']),
                    "account_code": row['account_code'],
                    "account_name": row['account_name'],
                    "account_type": row['account_type'],
                    "normal_balance": row['normal_balance'],
                    "total_debit": float(debit),
                    "total_credit": float(credit),
                    "balance": float(balance),
                })

                total_debit += debit
                total_credit += credit

            is_balanced = abs(total_debit - total_credit) < Decimal("0.01")

            return {
                "tenant_id": tenant_id,
                "as_of_date": as_of_date.isoformat(),
                "period_id": str(period_id) if period_id else None,
                "total_debit": float(total_debit),
                "total_credit": float(total_credit),
                "is_balanced": is_balanced,
                "account_count": len(accounts),
                "accounts": accounts,
            }

    async def get_trial_balance_summary(
        self,
        tenant_id: str,
        as_of_date: Optional[date] = None
    ) -> Dict:
        """
        Get trial balance summary grouped by account type.

        Useful for quick overview and balance sheet preparation.

        Returns:
            Dict with summary by account type:
            {
                "ASSET": {"debit": 1000000, "credit": 200000, "balance": 800000},
                "LIABILITY": {...},
                "EQUITY": {...},
                "INCOME": {...},
                "EXPENSE": {...},
                "is_balanced": true
            }
        """
        await self._ensure_initialized()

        tb = await self.get_trial_balance(tenant_id, as_of_date)

        summary = {}
        for account in tb['accounts']:
            acc_type = account['account_type']
            if acc_type not in summary:
                summary[acc_type] = {
                    "total_debit": 0,
                    "total_credit": 0,
                    "balance": 0,
                    "account_count": 0
                }
            summary[acc_type]['total_debit'] += account['total_debit']
            summary[acc_type]['total_credit'] += account['total_credit']
            summary[acc_type]['balance'] += account['balance']
            summary[acc_type]['account_count'] += 1

        return {
            "tenant_id": tenant_id,
            "as_of_date": tb['as_of_date'],
            "total_debit": tb['total_debit'],
            "total_credit": tb['total_credit'],
            "is_balanced": tb['is_balanced'],
            "by_type": summary
        }

    # ==================== Accounts Payable ====================

    async def create_payable(
        self,
        tenant_id: str,
        supplier_name: str,
        bill_number: str,
        bill_date: date,
        due_date: date,
        amount: Decimal,
        source_type: str = "BILL",
        source_id: Optional[UUID] = None,
        supplier_id: Optional[UUID] = None,
        description: Optional[str] = None,
        currency: str = "IDR"
    ) -> Dict:
        """
        Create an accounts payable record and corresponding journal entry.

        Args:
            tenant_id: Tenant UUID
            supplier_name: Supplier name for display
            bill_number: Bill/invoice number from supplier
            bill_date: Bill date
            due_date: Payment due date
            amount: Bill amount
            source_type: Source document type (BILL, PURCHASE, etc.)
            source_id: Source document UUID
            supplier_id: Supplier UUID (optional)
            description: Description (optional)
            currency: Currency code (default IDR)

        Returns:
            {success: bool, ap_id: str, journal_id: str, error: str}
        """
        await self._ensure_initialized()

        try:
            from ..constants import SourceType

            # Map string to enum
            st_enum = SourceType.BILL
            if source_type == "PURCHASE":
                st_enum = SourceType.PURCHASE

            # Create AP record via APService
            ap = await self.ap.create_payable(
                tenant_id=tenant_id,
                supplier_id=supplier_id,
                supplier_name=supplier_name,
                bill_number=bill_number,
                bill_date=bill_date,
                due_date=due_date,
                amount=amount,
                description=description or f"AP for {bill_number}",
                source_type=st_enum,
                source_id=source_id,
                currency=currency
            )

            # Create journal entry for the AP
            # Debit: Expense/Inventory account (we need an expense account)
            # Credit: Accounts Payable
            journal_result = await self.auto_posting.post_bill(
                tenant_id=tenant_id,
                bill_date=bill_date,
                bill_id=source_id or ap.id,
                total_amount=amount,
                supplier_name=supplier_name,
                description=f"AP Invoice {bill_number}"
            )

            # Check if journal creation succeeded
            if not journal_result.success:
                return {
                    "success": False,
                    "ap_id": str(ap.id),
                    "journal_id": None,
                    "error": f"Journal creation failed: {journal_result.error}"
                }

            return {
                "success": True,
                "ap_id": str(ap.id),
                "journal_id": str(journal_result.journal_id) if journal_result.journal_id else None,
                "error": None
            }

        except Exception as e:
            return {
                "success": False,
                "ap_id": None,
                "journal_id": None,
                "error": str(e)
            }

    async def void_payable(
        self,
        tenant_id: str,
        ap_id: UUID,
        void_reason: str = "Voided",
        voided_by: Optional[str] = None
    ) -> Dict:
        """
        Void an accounts payable record and reverse its journal entry.

        Args:
            tenant_id: Tenant UUID
            ap_id: AP record UUID
            reason: Reason for voiding

        Returns:
            {success: bool, error: str}
        """
        await self._ensure_initialized()

        try:
            from ..constants import ARAPStatus

            async with self.pool.acquire() as conn:
                # Set tenant context
                await conn.execute(
                    "SELECT set_config('app.tenant_id', $1, true)",
                    str(tenant_id)
                )

                # Get AP record
                ap = await conn.fetchrow(
                    """
                    SELECT id, status, source_id
                    FROM accounts_payable
                    WHERE id = $1 AND tenant_id = $2
                    """,
                    ap_id,
                    tenant_id
                )

                if not ap:
                    return {"success": False, "error": "AP record not found"}

                if ap['status'] == 'VOID':
                    return {"success": False, "error": "AP already voided"}

                # Update AP to void status
                await conn.execute(
                    """
                    UPDATE accounts_payable
                    SET status = 'VOID', updated_at = NOW()
                    WHERE id = $1
                    """,
                    ap_id
                )

                # Try to find and reverse journal by source_id
                if ap['source_id']:
                    journal = await conn.fetchrow(
                        """
                        SELECT id FROM journal_entries
                        WHERE source_id = $1 AND tenant_id = $2
                        LIMIT 1
                        """,
                        ap['source_id'],
                        tenant_id
                    )
                    if journal:
                        try:
                            await self.reverse_journal(
                                tenant_id=tenant_id,
                                journal_id=journal['id'],
                                reason=f"AP Void: {void_reason}"
                            )
                        except Exception as e:
                            # Log but don't fail - AP is voided
                            pass

            return {"success": True, "error": None}

        except Exception as e:
            return {"success": False, "error": str(e)}

    async def apply_ap_payment(
        self,
        tenant_id: str,
        ap_id: UUID,
        payment_amount: Decimal,
        payment_date: date,
        payment_method: str,
        account_id: Optional[UUID] = None,
        reference_number: Optional[str] = None,
        notes: Optional[str] = None
    ) -> Dict:
        """
        Apply a payment to an accounts payable record.

        This follows the Iron Laws of Accounting:
        - Law 3: Append-Only - creates journal entry, no mutations
        - Law 4: Double-Entry - debits AP, credits Cash/Bank
        - Law 6: Source Traceability - journal has source_type and source_id
        - Law 8: No Silent Mutation - always creates journal for payment

        Args:
            tenant_id: Tenant UUID
            ap_id: AP record UUID to apply payment to
            payment_amount: Amount being paid
            payment_date: Date of payment
            payment_method: Payment method (cash, bank, transfer, etc.)
            account_id: Optional account ID for tracking
            reference_number: Optional payment reference number
            notes: Optional notes for the payment

        Returns:
            {success: bool, journal_id: str | None, error: str | None}
        """
        await self._ensure_initialized()

        try:
            # Apply payment via APService - this creates journal entry internally
            # following Iron Laws (append-only, double-entry, source traceability)
            payment_application = await self.ap.apply_payment(
                tenant_id=tenant_id,
                ap_id=ap_id,
                payment_date=payment_date,
                amount=payment_amount,
                payment_method=payment_method,
                reference_number=reference_number,
                notes=notes,
                create_journal=True  # Ensures journal is created (Law 8)
            )

            return {
                "success": True,
                "journal_id": str(payment_application.journal_id) if payment_application.journal_id else None,
                "payment_application_id": str(payment_application.id),
                "error": None
            }

        except ValueError as ve:
            # Validation errors (e.g., payment exceeds balance)
            return {
                "success": False,
                "journal_id": None,
                "error": str(ve)
            }
        except Exception as e:
            return {
                "success": False,
                "journal_id": None,
                "error": str(e)
            }
