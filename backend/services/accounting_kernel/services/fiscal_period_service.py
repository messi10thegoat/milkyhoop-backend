"""
Fiscal Period Service
=====================

Manages fiscal periods with OPEN/CLOSED/LOCKED status lifecycle.

Status Lifecycle:
- OPEN:   Normal operation, all posting allowed
- CLOSED: Soft close, only system reversals allowed
- LOCKED: Immutable, audit-ready

Transitions:
- OPEN → CLOSED: close_period() - creates closing entries, snapshots balances
- CLOSED → LOCKED: lock_period() - makes period immutable
- LOCKED → CLOSED: unlock_period() - admin only with audit trail
"""
import json
import logging
from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional, Dict, Any
from uuid import UUID, uuid4

from ..config import settings
from ..constants import PeriodStatus, AccountType, EventType
from ..models.fiscal_period import (
    FiscalPeriod,
    CreatePeriodRequest,
    CreatePeriodResponse,
    ClosePeriodRequest,
    ClosePeriodResponse,
    LockPeriodRequest,
    LockPeriodResponse,
    UnlockPeriodRequest,
    UnlockPeriodResponse,
)

logger = logging.getLogger(__name__)


class FiscalPeriodService:
    """
    Fiscal Period Management Service

    Responsibilities:
    - Create fiscal periods
    - Close periods (generate closing entries, snapshot balances)
    - Lock periods (make immutable for audit)
    - Unlock periods (admin only)
    - Validate journal dates against period status
    """

    def __init__(self, db_pool):
        """
        Initialize with database pool.

        Args:
            db_pool: asyncpg connection pool
        """
        self.db = db_pool

    async def create_period(
        self,
        request: CreatePeriodRequest
    ) -> CreatePeriodResponse:
        """
        Create a new fiscal period.

        Args:
            request: CreatePeriodRequest with period details

        Returns:
            CreatePeriodResponse with period_id or errors
        """
        logger.info(f"Creating period {request.period_name} for tenant {request.tenant_id}")

        async with self.db.acquire() as conn:
            async with conn.transaction():
                # Set tenant context
                await conn.execute(
                    "SELECT set_config('app.tenant_id', $1, true)",
                    str(request.tenant_id)
                )

                # Check for existing period with same name
                existing = await conn.fetchrow(
                    """
                    SELECT id FROM fiscal_periods
                    WHERE tenant_id = $1 AND period_name = $2
                    """,
                    request.tenant_id,
                    request.period_name
                )

                if existing:
                    return CreatePeriodResponse(
                        success=False,
                        errors=[f"Period {request.period_name} already exists"]
                    )

                # Create period
                period_id = uuid4()
                try:
                    await conn.execute(
                        """
                        INSERT INTO fiscal_periods (
                            id, tenant_id, period_name, start_date, end_date,
                            status, created_at
                        ) VALUES ($1, $2, $3, $4, $5, $6, NOW())
                        """,
                        period_id,
                        request.tenant_id,
                        request.period_name,
                        request.start_date,
                        request.end_date,
                        PeriodStatus.OPEN.value
                    )
                except Exception as e:
                    # EXCLUDE constraint violation = overlapping periods
                    if "excl_no_overlap" in str(e):
                        return CreatePeriodResponse(
                            success=False,
                            errors=["Period dates overlap with existing period"]
                        )
                    raise

                logger.info(f"Created period {request.period_name} with id {period_id}")

                return CreatePeriodResponse(
                    success=True,
                    period_id=period_id,
                    period_name=request.period_name,
                    message=f"Period {request.period_name} created successfully"
                )

    async def get_period_by_date(
        self,
        tenant_id: str,
        target_date: date
    ) -> Optional[FiscalPeriod]:
        """
        Get the fiscal period containing a specific date.

        Args:
            tenant_id: Tenant identifier
            target_date: Date to look up

        Returns:
            FiscalPeriod if found, None otherwise
        """
        async with self.db.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM fiscal_periods
                WHERE tenant_id = $1
                  AND $2 >= start_date
                  AND $2 <= end_date
                """,
                tenant_id,
                target_date
            )

            if not row:
                return None

            return self._row_to_period(row)

    async def get_period_by_id(
        self,
        tenant_id: str,
        period_id: UUID
    ) -> Optional[FiscalPeriod]:
        """
        Get a fiscal period by ID.

        Args:
            tenant_id: Tenant identifier
            period_id: Period UUID

        Returns:
            FiscalPeriod if found, None otherwise
        """
        async with self.db.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM fiscal_periods
                WHERE tenant_id = $1 AND id = $2
                """,
                tenant_id,
                period_id
            )

            if not row:
                return None

            return self._row_to_period(row)

    async def list_periods(
        self,
        tenant_id: str,
        status: Optional[PeriodStatus] = None
    ) -> List[FiscalPeriod]:
        """
        List all fiscal periods for a tenant.

        Args:
            tenant_id: Tenant identifier
            status: Optional filter by status

        Returns:
            List of FiscalPeriod objects
        """
        async with self.db.acquire() as conn:
            if status:
                rows = await conn.fetch(
                    """
                    SELECT * FROM fiscal_periods
                    WHERE tenant_id = $1 AND status = $2
                    ORDER BY start_date DESC
                    """,
                    tenant_id,
                    status.value
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT * FROM fiscal_periods
                    WHERE tenant_id = $1
                    ORDER BY start_date DESC
                    """,
                    tenant_id
                )

            return [self._row_to_period(row) for row in rows]

    async def is_date_locked(
        self,
        tenant_id: str,
        target_date: date
    ) -> bool:
        """
        Check if a date falls within a LOCKED period.

        Args:
            tenant_id: Tenant identifier
            target_date: Date to check

        Returns:
            True if date is in a locked period
        """
        async with self.db.acquire() as conn:
            result = await conn.fetchval(
                "SELECT is_period_locked($1, $2)",
                tenant_id,
                target_date
            )
            return result or False

    async def is_date_closed(
        self,
        tenant_id: str,
        target_date: date
    ) -> bool:
        """
        Check if a date falls within a CLOSED or LOCKED period.

        Args:
            tenant_id: Tenant identifier
            target_date: Date to check

        Returns:
            True if date is in a closed/locked period
        """
        async with self.db.acquire() as conn:
            result = await conn.fetchval(
                "SELECT is_period_closed($1, $2)",
                tenant_id,
                target_date
            )
            return result or False

    async def can_post_to_date(
        self,
        tenant_id: str,
        target_date: date,
        is_system_generated: bool = False
    ) -> tuple[bool, Optional[str]]:
        """
        Check if posting is allowed for a specific date.

        Args:
            tenant_id: Tenant identifier
            target_date: Journal date
            is_system_generated: True if system-generated entry (e.g., reversal)

        Returns:
            Tuple of (can_post, error_message)
        """
        period = await self.get_period_by_date(tenant_id, target_date)

        if not period:
            # No period defined = allow (grace period for setup)
            return (True, None)

        if period.status == PeriodStatus.LOCKED:
            return (False, f"Period {period.period_name} is locked")

        if period.status == PeriodStatus.CLOSED:
            if is_system_generated:
                return (True, None)  # System can post to CLOSED
            return (False, f"Period {period.period_name} is closed for manual posting")

        # OPEN
        return (True, None)

    async def close_period(
        self,
        request: ClosePeriodRequest
    ) -> ClosePeriodResponse:
        """
        Close a fiscal period (OPEN → CLOSED).

        Actions:
        1. Validate period is OPEN
        2. Generate closing journal entries (revenue/expense → retained earnings)
        3. Snapshot account balances
        4. Update status to CLOSED

        Args:
            request: ClosePeriodRequest

        Returns:
            ClosePeriodResponse with closing details
        """
        logger.info(f"Closing period {request.period_name} for tenant {request.tenant_id}")

        async with self.db.acquire() as conn:
            async with conn.transaction():
                # Set tenant context
                await conn.execute(
                    "SELECT set_config('app.tenant_id', $1, true)",
                    str(request.tenant_id)
                )

                # Get the period
                period_row = await conn.fetchrow(
                    """
                    SELECT * FROM fiscal_periods
                    WHERE tenant_id = $1 AND period_name = $2
                    FOR UPDATE
                    """,
                    request.tenant_id,
                    request.period_name
                )

                if not period_row:
                    return ClosePeriodResponse(
                        success=False,
                        errors=[f"Period {request.period_name} not found"]
                    )

                if period_row['status'] != PeriodStatus.OPEN.value:
                    return ClosePeriodResponse(
                        success=False,
                        errors=[f"Period {request.period_name} is not open (status: {period_row['status']})"]
                    )

                period_id = period_row['id']

                # Generate closing snapshot (all account balances)
                closing_snapshot = await self._generate_closing_snapshot(
                    conn,
                    request.tenant_id,
                    period_row['end_date']
                )

                # Create closing journal entry if requested
                closing_journal_id = None
                if request.create_closing_entries:
                    closing_journal_id = await self._create_closing_entries(
                        conn,
                        request.tenant_id,
                        period_id,
                        period_row['end_date'],
                        request.closed_by,
                        closing_snapshot
                    )

                # Update period status
                await conn.execute(
                    """
                    UPDATE fiscal_periods
                    SET status = $1,
                        closed_at = NOW(),
                        closed_by = $2,
                        closing_journal_id = $3,
                        closing_snapshot = $4,
                        updated_at = NOW()
                    WHERE id = $5
                    """,
                    PeriodStatus.CLOSED.value,
                    request.closed_by,
                    closing_journal_id,
                    json.dumps(closing_snapshot),
                    period_id
                )

                # Publish event to outbox
                await self._publish_event(
                    conn,
                    request.tenant_id,
                    EventType.PERIOD_CLOSED,
                    {
                        "period_id": str(period_id),
                        "period_name": request.period_name,
                        "closed_by": str(request.closed_by),
                        "closing_journal_id": str(closing_journal_id) if closing_journal_id else None,
                    }
                )

                logger.info(f"Period {request.period_name} closed successfully")

                return ClosePeriodResponse(
                    success=True,
                    period_id=period_id,
                    period_name=request.period_name,
                    closing_journal_id=closing_journal_id,
                    closing_snapshot=closing_snapshot,
                    message=f"Period {request.period_name} closed successfully"
                )

    async def lock_period(
        self,
        request: LockPeriodRequest
    ) -> LockPeriodResponse:
        """
        Lock a fiscal period (CLOSED → LOCKED).

        A locked period is immutable - no entries can be added or modified.

        Args:
            request: LockPeriodRequest

        Returns:
            LockPeriodResponse
        """
        logger.info(f"Locking period {request.period_id} for tenant {request.tenant_id}")

        async with self.db.acquire() as conn:
            async with conn.transaction():
                # Set tenant context
                await conn.execute(
                    "SELECT set_config('app.tenant_id', $1, true)",
                    str(request.tenant_id)
                )

                # Get the period (with lock)
                period_row = await conn.fetchrow(
                    """
                    SELECT * FROM fiscal_periods
                    WHERE tenant_id = $1 AND id = $2
                    FOR UPDATE
                    """,
                    request.tenant_id,
                    request.period_id
                )

                if not period_row:
                    return LockPeriodResponse(
                        success=False,
                        errors=["Period not found"]
                    )

                if period_row['status'] == PeriodStatus.LOCKED.value:
                    return LockPeriodResponse(
                        success=False,
                        errors=["Period is already locked"]
                    )

                if period_row['status'] != PeriodStatus.CLOSED.value:
                    return LockPeriodResponse(
                        success=False,
                        errors=["Period must be closed before locking"]
                    )

                # Lock the period
                locked_at = datetime.utcnow()
                await conn.execute(
                    """
                    UPDATE fiscal_periods
                    SET status = $1,
                        locked_at = $2,
                        locked_by = $3,
                        lock_reason = $4,
                        updated_at = NOW()
                    WHERE id = $5
                    """,
                    PeriodStatus.LOCKED.value,
                    locked_at,
                    request.locked_by,
                    request.reason,
                    request.period_id
                )

                # Publish event
                await self._publish_event(
                    conn,
                    request.tenant_id,
                    EventType.PERIOD_LOCKED,
                    {
                        "period_id": str(request.period_id),
                        "period_name": period_row['period_name'],
                        "locked_by": str(request.locked_by),
                        "reason": request.reason,
                        "closing_snapshot": period_row['closing_snapshot'],
                    }
                )

                logger.info(f"Period {period_row['period_name']} locked successfully")

                return LockPeriodResponse(
                    success=True,
                    period_id=request.period_id,
                    period_name=period_row['period_name'],
                    locked_at=locked_at,
                    message=f"Period {period_row['period_name']} locked successfully"
                )

    async def unlock_period(
        self,
        request: UnlockPeriodRequest
    ) -> UnlockPeriodResponse:
        """
        Unlock a fiscal period (LOCKED → CLOSED).

        This is an admin-only operation that requires a reason.
        Creates an audit trail entry.

        Args:
            request: UnlockPeriodRequest (requires reason)

        Returns:
            UnlockPeriodResponse
        """
        if not request.reason:
            return UnlockPeriodResponse(
                success=False,
                errors=["Reason is required for unlocking a period"]
            )

        logger.warning(f"Unlocking period {request.period_id} for tenant {request.tenant_id} - Reason: {request.reason}")

        async with self.db.acquire() as conn:
            async with conn.transaction():
                # Set tenant context
                await conn.execute(
                    "SELECT set_config('app.tenant_id', $1, true)",
                    str(request.tenant_id)
                )

                # Get the period (with lock)
                period_row = await conn.fetchrow(
                    """
                    SELECT * FROM fiscal_periods
                    WHERE tenant_id = $1 AND id = $2
                    FOR UPDATE
                    """,
                    request.tenant_id,
                    request.period_id
                )

                if not period_row:
                    return UnlockPeriodResponse(
                        success=False,
                        errors=["Period not found"]
                    )

                if period_row['status'] != PeriodStatus.LOCKED.value:
                    return UnlockPeriodResponse(
                        success=False,
                        errors=[f"Period is not locked (status: {period_row['status']})"]
                    )

                # Unlock the period (back to CLOSED, not OPEN)
                await conn.execute(
                    """
                    UPDATE fiscal_periods
                    SET status = $1,
                        locked_at = NULL,
                        locked_by = NULL,
                        lock_reason = NULL,
                        updated_at = NOW()
                    WHERE id = $2
                    """,
                    PeriodStatus.CLOSED.value,
                    request.period_id
                )

                # Publish event with audit info
                await self._publish_event(
                    conn,
                    request.tenant_id,
                    EventType.PERIOD_UNLOCKED,
                    {
                        "period_id": str(request.period_id),
                        "period_name": period_row['period_name'],
                        "unlocked_by": str(request.unlocked_by),
                        "reason": request.reason,
                        "previous_lock_time": period_row['locked_at'].isoformat() if period_row['locked_at'] else None,
                        "previous_locked_by": str(period_row['locked_by']) if period_row['locked_by'] else None,
                    }
                )

                logger.warning(f"Period {period_row['period_name']} unlocked by {request.unlocked_by} - Reason: {request.reason}")

                return UnlockPeriodResponse(
                    success=True,
                    period_id=request.period_id,
                    period_name=period_row['period_name'],
                    message=f"Period {period_row['period_name']} unlocked (now CLOSED)"
                )

    async def _generate_closing_snapshot(
        self,
        conn,
        tenant_id: str,
        as_of_date: date
    ) -> Dict[str, Any]:
        """
        Generate a snapshot of all account balances at period end.

        Calculates balances from journal_lines for accuracy.

        Returns dict like:
        {
            "generated_at": "2026-01-31T23:59:59Z",
            "accounts": {
                "1-10100": {"name": "Kas", "type": "ASSET", "balance": 150000, ...}
            }
        }
        """
        # Calculate balances from journal lines up to as_of_date
        rows = await conn.fetch(
            """
            SELECT
                coa.account_code,
                coa.name as account_name,
                coa.account_type,
                coa.normal_balance,
                COALESCE(SUM(jl.debit), 0) as debit_total,
                COALESCE(SUM(jl.credit), 0) as credit_total
            FROM chart_of_accounts coa
            LEFT JOIN journal_lines jl ON jl.account_id = coa.id
            LEFT JOIN journal_entries je ON je.id = jl.journal_id
                AND je.tenant_id = $1
                AND je.journal_date <= $2
                AND je.status = 'POSTED'
            WHERE coa.tenant_id = $1 AND coa.is_active = true
            GROUP BY coa.id, coa.account_code, coa.name, coa.account_type, coa.normal_balance
            ORDER BY coa.account_code
            """,
            tenant_id,
            as_of_date
        )

        snapshot = {
            "generated_at": datetime.utcnow().isoformat(),
            "as_of_date": as_of_date.isoformat(),
            "accounts": {}
        }

        total_debit = Decimal("0")
        total_credit = Decimal("0")

        for row in rows:
            code = row['account_code']
            debit = Decimal(str(row['debit_total']))
            credit = Decimal(str(row['credit_total']))

            # Calculate balance based on normal balance
            if row['normal_balance'] == 'DEBIT':
                balance = debit - credit
            else:
                balance = credit - debit

            snapshot["accounts"][code] = {
                "name": row['account_name'],
                "type": row['account_type'],
                "normal_balance": row['normal_balance'],
                "balance": float(balance),
                "debit_total": float(debit),
                "credit_total": float(credit),
            }

            total_debit += debit
            total_credit += credit

        snapshot["total_debit"] = float(total_debit)
        snapshot["total_credit"] = float(total_credit)
        snapshot["is_balanced"] = abs(total_debit - total_credit) < Decimal("0.01")

        return snapshot

    async def _create_closing_entries(
        self,
        conn,
        tenant_id: str,
        period_id: UUID,
        closing_date: date,
        closed_by: UUID,
        snapshot: Dict
    ) -> Optional[UUID]:
        """
        Create closing journal entries.

        Closing entries transfer temporary accounts (Income/Expense)
        to Retained Earnings:
        - DR Income accounts (to zero them)
        - CR Expense accounts (to zero them)
        - Net to Retained Earnings
        """
        # Get revenue and expense account balances
        income_accounts = []
        expense_accounts = []

        for code, data in snapshot.get("accounts", {}).items():
            balance = Decimal(str(data['balance']))
            if balance == 0:
                continue

            if data['type'] == 'INCOME':
                income_accounts.append((code, balance))
            elif data['type'] == 'EXPENSE':
                expense_accounts.append((code, balance))

        # If no income/expense accounts have balances, no closing entry needed
        if not income_accounts and not expense_accounts:
            logger.info("No closing entries needed - no income/expense balances")
            return None

        # Calculate net income
        total_income = sum(b for _, b in income_accounts)
        total_expense = sum(b for _, b in expense_accounts)
        net_income = total_income - total_expense

        # Get Retained Earnings account
        retained_earnings = await conn.fetchrow(
            """
            SELECT id, account_code FROM chart_of_accounts
            WHERE tenant_id = $1 AND account_code = '3-20000'
            """,
            tenant_id
        )

        if not retained_earnings:
            logger.warning("Retained Earnings account (3-20000) not found, skipping closing entries")
            return None

        # Create closing journal
        journal_id = uuid4()
        journal_number = await self._generate_journal_number(conn, tenant_id, "CLO")

        await conn.execute(
            """
            INSERT INTO journal_entries (
                id, tenant_id, journal_number, journal_date,
                description, source_type, status,
                total_debit, total_credit,
                created_by, created_at, period_id
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, NOW(), $11)
            """,
            journal_id,
            tenant_id,
            journal_number,
            closing_date,
            f"Closing entries for period ending {closing_date}",
            "CLOSING",
            "POSTED",
            total_income + total_expense,  # All debits
            total_income + total_expense,  # All credits
            closed_by,
            period_id
        )

        line_num = 1

        # Close income accounts (DR to reduce credit balance)
        for code, balance in income_accounts:
            account = await conn.fetchrow(
                "SELECT id FROM chart_of_accounts WHERE tenant_id = $1 AND account_code = $2",
                tenant_id, code
            )
            if account:
                await conn.execute(
                    """
                    INSERT INTO journal_lines (
                        id, journal_id, account_id, line_number,
                        debit, credit, memo
                    ) VALUES ($1, $2, $3, $4, $5, 0, $6)
                    """,
                    uuid4(), journal_id, account['id'], line_num,
                    balance, f"Close {code} to Retained Earnings"
                )
                line_num += 1

        # Close expense accounts (CR to reduce debit balance)
        for code, balance in expense_accounts:
            account = await conn.fetchrow(
                "SELECT id FROM chart_of_accounts WHERE tenant_id = $1 AND account_code = $2",
                tenant_id, code
            )
            if account:
                await conn.execute(
                    """
                    INSERT INTO journal_lines (
                        id, journal_id, account_id, line_number,
                        debit, credit, memo
                    ) VALUES ($1, $2, $3, $4, 0, $5, $6)
                    """,
                    uuid4(), journal_id, account['id'], line_num,
                    balance, f"Close {code} to Retained Earnings"
                )
                line_num += 1

        # Retained Earnings entry (balancing entry)
        if net_income >= 0:
            # Net profit: CR Retained Earnings
            await conn.execute(
                """
                INSERT INTO journal_lines (
                    id, journal_id, account_id, line_number,
                    debit, credit, memo
                ) VALUES ($1, $2, $3, $4, 0, $5, $6)
                """,
                uuid4(), journal_id, retained_earnings['id'], line_num,
                net_income, "Net income to Retained Earnings"
            )
        else:
            # Net loss: DR Retained Earnings
            await conn.execute(
                """
                INSERT INTO journal_lines (
                    id, journal_id, account_id, line_number,
                    debit, credit, memo
                ) VALUES ($1, $2, $3, $4, $5, 0, $6)
                """,
                uuid4(), journal_id, retained_earnings['id'], line_num,
                abs(net_income), "Net loss to Retained Earnings"
            )

        logger.info(f"Created closing journal {journal_number} with {line_num} lines")

        return journal_id

    async def _generate_journal_number(
        self,
        conn,
        tenant_id: str,
        prefix: str = "JE"
    ) -> str:
        """Generate sequential journal number"""
        today = datetime.now()
        month_prefix = f"{prefix}-{today.strftime('%y%m')}"

        last = await conn.fetchval(
            """
            SELECT journal_number FROM journal_entries
            WHERE tenant_id = $1 AND journal_number LIKE $2
            ORDER BY journal_number DESC LIMIT 1
            """,
            tenant_id,
            f"{month_prefix}-%"
        )

        if last:
            seq = int(last.split("-")[-1]) + 1
        else:
            seq = 1

        return f"{month_prefix}-{seq:04d}"

    async def _publish_event(
        self,
        conn,
        tenant_id: str,
        event_type: EventType,
        payload: Dict
    ) -> None:
        """Publish event to accounting_outbox"""
        await conn.execute(
            """
            INSERT INTO accounting_outbox (
                id, tenant_id, event_type, aggregate_type,
                aggregate_id, payload, created_at
            ) VALUES ($1, $2, $3, $4, $5, $6, NOW())
            """,
            uuid4(),
            tenant_id,
            event_type.value,
            "fiscal_period",
            payload.get("period_id", ""),
            json.dumps(payload)
        )

    def _row_to_period(self, row) -> FiscalPeriod:
        """Convert database row to FiscalPeriod model"""
        return FiscalPeriod(
            id=row['id'],
            tenant_id=row['tenant_id'],
            period_name=row['period_name'],
            start_date=row['start_date'],
            end_date=row['end_date'],
            status=PeriodStatus(row['status']),
            closed_at=row.get('closed_at'),
            closed_by=row.get('closed_by'),
            closing_journal_id=row.get('closing_journal_id'),
            locked_at=row.get('locked_at'),
            locked_by=row.get('locked_by'),
            lock_reason=row.get('lock_reason'),
            opening_balances=json.loads(row['opening_balances']) if row.get('opening_balances') else None,
            closing_balances=json.loads(row['closing_balances']) if row.get('closing_balances') else None,
            closing_snapshot=json.loads(row['closing_snapshot']) if row.get('closing_snapshot') else None,
            created_at=row.get('created_at'),
            updated_at=row.get('updated_at'),
        )
