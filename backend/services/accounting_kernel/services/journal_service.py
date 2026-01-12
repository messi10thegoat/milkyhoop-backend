"""
Journal Service
===============

Core service for creating and managing journal entries.
Implements double-entry bookkeeping with idempotency.

Key Features:
- Atomic journal creation (header + lines in one transaction)
- Idempotent via trace_id (exactly-once semantics)
- Double-entry validation (debit = credit)
- Source tracking for audit trail
- Void support (creates reversing entry)
"""
import json
import logging
from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional, Tuple
from uuid import UUID, uuid4

from ..config import settings
from ..constants import JournalStatus, SourceType, PeriodStatus
from ..models.journal import (
    JournalEntry,
    JournalLine,
    JournalLineInput,
    CreateJournalRequest,
    JournalResponse,
)
from .coa_service import CoAService

logger = logging.getLogger(__name__)


class JournalService:
    """
    Journal Entry Service

    Responsibilities:
    - Create journal entries (atomic, idempotent)
    - Void journal entries (create reversing entry)
    - Query journal entries
    - Validate double-entry
    - Update account balances (async)
    """

    def __init__(self, db_pool, coa_service: CoAService):
        """
        Initialize with database pool and CoA service.

        Args:
            db_pool: asyncpg connection pool
            coa_service: Chart of Accounts service for account resolution
        """
        self.db = db_pool
        self.coa = coa_service

    async def create_journal(
        self,
        request: CreateJournalRequest
    ) -> JournalResponse:
        """
        Create a journal entry with lines.

        This is the CORE operation of the accounting kernel.
        - Atomic: Header + lines created in single transaction
        - Idempotent: Same trace_id returns existing journal
        - Validated: Double-entry must balance

        Args:
            request: CreateJournalRequest with lines

        Returns:
            JournalResponse with journal_id and status
        """
        # Validate request
        errors = request.validate()
        if errors:
            return JournalResponse(
                success=False,
                errors=errors,
                message="Validation failed"
            )

        # Generate trace_id if not provided (TEXT in DB)
        trace_id = request.trace_id or str(uuid4())

        async with self.db.acquire() as conn:
            async with conn.transaction():
                # Set tenant context for RLS
                await conn.execute(
                    "SELECT set_config('app.tenant_id', $1, true)",
                    str(request.tenant_id)
                )

                # Check period lock status
                is_system_generated = request.source_type in (
                    SourceType.ADJUSTMENT, SourceType.CLOSING, SourceType.OPENING
                )
                can_post, period_error = await self._check_period_status(
                    conn,
                    request.tenant_id,
                    request.journal_date,
                    is_system_generated
                )
                if not can_post:
                    return JournalResponse(
                        success=False,
                        errors=[period_error],
                        message="Period validation failed"
                    )

                # Check for duplicate (idempotency)
                existing = await conn.fetchrow(
                    """
                    SELECT id, journal_number, status
                    FROM journal_entries
                    WHERE tenant_id = $1 AND trace_id = $2
                    """,
                    request.tenant_id, trace_id
                )

                if existing:
                    logger.info(
                        f"Duplicate journal detected: trace_id={trace_id}, "
                        f"existing_id={existing['id']}"
                    )
                    return JournalResponse(
                        success=True,
                        journal_id=existing["id"],
                        journal_number=existing["journal_number"],
                        status=JournalStatus(existing["status"]),
                        message="Journal already exists (idempotent)",
                        is_duplicate=True
                    )

                # Get next journal number
                journal_number = await self._get_next_journal_number(
                    conn,
                    request.tenant_id,
                    request.source_type
                )

                # Resolve account codes to IDs
                resolved_lines, resolve_errors = await self._resolve_account_codes(
                    request.tenant_id,
                    request.lines
                )

                if resolve_errors:
                    return JournalResponse(
                        success=False,
                        errors=resolve_errors,
                        message="Account resolution failed"
                    )

                # Validate: Block direct posting to AP account (2-10100)
                # AP account can only be modified via Bill, Payment, or Credit Note
                AP_ACCOUNT_CODE = settings.accounting.AP_ACCOUNT
                ALLOWED_AP_SOURCES = (
                    SourceType.BILL,
                    SourceType.PAYMENT_BILL,
                    SourceType.ADJUSTMENT,  # For void/reversal
                    SourceType.CLOSING,     # For period closing
                    SourceType.OPENING,     # For opening balance
                )

                for line_input, account_id in resolved_lines:
                    if line_input.account_code == AP_ACCOUNT_CODE:
                        if request.source_type not in ALLOWED_AP_SOURCES:
                            return JournalResponse(
                                success=False,
                                errors=[
                                    f"Direct posting to AP account ({AP_ACCOUNT_CODE}) not allowed. "
                                    f"AP can only be modified via: Bill, Payment, Void, or Credit Note. "
                                    f"Current source_type: {request.source_type.value}"
                                ],
                                message="AP account protection"
                            )

                # Create journal header
                journal_id = uuid4()
                source_snapshot_json = (
                    json.dumps(request.source_snapshot, default=str)
                    if request.source_snapshot else None
                )

                # Calculate totals from lines
                total_debit = sum(float(line.debit) for line, _ in resolved_lines)
                total_credit = sum(float(line.credit) for line, _ in resolved_lines)

                await conn.execute(
                    """
                    INSERT INTO journal_entries (
                        id, tenant_id, journal_number, journal_date,
                        description, source_type, source_id, trace_id,
                        status, total_debit, total_credit, created_by
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                    """,
                    journal_id,
                    request.tenant_id,
                    journal_number,
                    request.journal_date,
                    request.description or "",
                    request.source_type.value,
                    request.source_id,
                    trace_id,
                    JournalStatus.POSTED.value,
                    total_debit,
                    total_credit,
                    request.posted_by
                )

                # Create journal lines
                for idx, (line_input, account_id) in enumerate(resolved_lines, 1):
                    line_id = uuid4()
                    await conn.execute(
                        """
                        INSERT INTO journal_lines (
                            id, journal_id, line_number, account_id,
                            debit, credit, memo
                        )
                        VALUES ($1, $2, $3, $4, $5, $6, $7)
                        """,
                        line_id,
                        journal_id,
                        idx,
                        account_id,
                        float(line_input.debit),
                        float(line_input.credit),
                        line_input.memo or line_input.description or ""
                    )

                # Validate double-entry (DB has CHECK constraint je_balanced)
                if abs(total_debit - total_credit) >= 0.01:
                    raise ValueError(
                        f"Journal {journal_number} is not balanced: "
                        f"debit={total_debit}, credit={total_credit}"
                    )

                # Create outbox event for downstream processing
                await self._create_outbox_event(
                    conn,
                    request.tenant_id,
                    "accounting.journal.posted",
                    str(journal_id),
                    {
                        "journal_id": str(journal_id),
                        "journal_number": journal_number,
                        "journal_date": request.journal_date.isoformat(),
                        "source_type": request.source_type.value,
                        "source_id": str(request.source_id) if request.source_id else None,
                        "total_debit": float(sum(l.debit for l, _ in resolved_lines)),
                        "total_credit": float(sum(l.credit for l, _ in resolved_lines)),
                    }
                )

                logger.info(
                    f"Created journal {journal_number} (id={journal_id}) "
                    f"for tenant {request.tenant_id}"
                )

                return JournalResponse(
                    success=True,
                    journal_id=journal_id,
                    journal_number=journal_number,
                    status=JournalStatus.POSTED,
                    message="Journal created successfully"
                )

    async def void_journal(
        self,
        tenant_id: str,
        journal_id: UUID,
        voided_by: UUID,
        reason: str
    ) -> JournalResponse:
        """
        Void a journal entry by creating a reversing entry.

        Note: The original journal is marked as VOID, and a new
        reversing journal is created with opposite debits/credits.

        Args:
            tenant_id: Tenant UUID
            journal_id: Journal to void
            voided_by: User voiding the journal
            reason: Reason for voiding

        Returns:
            JournalResponse with reversing journal details
        """
        async with self.db.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "SELECT set_config('app.tenant_id', $1, true)",
                    str(tenant_id)
                )

                # Get original journal
                original = await conn.fetchrow(
                    """
                    SELECT id, journal_number, journal_date, description,
                           source_type, source_id, trace_id, status
                    FROM journal_entries
                    WHERE tenant_id = $1 AND id = $2
                    """,
                    tenant_id, journal_id
                )

                if not original:
                    return JournalResponse(
                        success=False,
                        message="Journal not found",
                        errors=["Journal not found"]
                    )

                if original["status"] == JournalStatus.VOID.value:
                    return JournalResponse(
                        success=False,
                        message="Journal already voided",
                        errors=["Cannot void an already voided journal"]
                    )

                # Check if original journal's period is locked
                can_void, period_error = await self._check_period_status(
                    conn,
                    tenant_id,
                    original["journal_date"],
                    is_system_generated=False  # Void is manual action
                )
                if not can_void:
                    return JournalResponse(
                        success=False,
                        errors=[f"Cannot void journal: {period_error}"],
                        message="Period validation failed"
                    )

                # Check if today's date (reversal entry date) is in open period
                can_reverse, reverse_error = await self._check_period_status(
                    conn,
                    tenant_id,
                    date.today(),
                    is_system_generated=True  # Reversal is system-generated
                )
                if not can_reverse:
                    return JournalResponse(
                        success=False,
                        errors=[f"Cannot create reversal: {reverse_error}"],
                        message="Period validation failed"
                    )

                # Get original lines
                original_lines = await conn.fetch(
                    """
                    SELECT account_id, memo, debit, credit
                    FROM journal_lines
                    WHERE journal_id = $1
                    ORDER BY line_number
                    """,
                    journal_id
                )

                # Mark original as voided
                await conn.execute(
                    """
                    UPDATE journal_entries
                    SET status = $1, voided_by = $2, void_reason = $3
                    WHERE id = $4
                    """,
                    JournalStatus.VOID.value,
                    voided_by,
                    reason,
                    journal_id
                )

                # Create reversing journal
                reversing_id = uuid4()
                reversing_number = await self._get_next_journal_number(
                    conn, tenant_id, SourceType.ADJUSTMENT
                )

                await conn.execute(
                    """
                    INSERT INTO journal_entries (
                        id, tenant_id, journal_number, journal_date,
                        description, source_type, source_id, trace_id,
                        status, created_by
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                    """,
                    reversing_id,
                    tenant_id,
                    reversing_number,
                    date.today(),  # Reversing entry dated today
                    f"Void: {original['journal_number']} - {reason}",
                    SourceType.ADJUSTMENT.value,
                    journal_id,  # Link to original
                    str(uuid4()),  # trace_id is TEXT
                    JournalStatus.POSTED.value,
                    voided_by
                )

                # Create reversed lines (swap debit/credit)
                for idx, line in enumerate(original_lines, 1):
                    await conn.execute(
                        """
                        INSERT INTO journal_lines (
                            id, journal_id, account_id, line_number,
                            memo, debit, credit
                        )
                        VALUES ($1, $2, $3, $4, $5, $6, $7)
                        """,
                        uuid4(),
                        reversing_id,
                        line["account_id"],
                        idx,
                        f"Reverse: {line['memo'] or ''}",
                        line["credit"],  # Swap: original credit becomes debit
                        line["debit"]    # Swap: original debit becomes credit
                    )

                # Update balances
                await conn.execute(
                    "SELECT update_account_balances_for_journal($1, $2, $3)",
                    reversing_id, date.today(), tenant_id
                )

                # Create outbox event
                await self._create_outbox_event(
                    conn,
                    tenant_id,
                    "accounting.journal.voided",
                    str(journal_id),
                    {
                        "voided_journal_id": str(journal_id),
                        "voided_journal_number": original["journal_number"],
                        "reversing_journal_id": str(reversing_id),
                        "reversing_journal_number": reversing_number,
                        "reason": reason,
                        "voided_by": str(voided_by),
                    }
                )

                logger.info(
                    f"Voided journal {original['journal_number']} "
                    f"with reversing entry {reversing_number}"
                )

                return JournalResponse(
                    success=True,
                    journal_id=reversing_id,
                    journal_number=reversing_number,
                    status=JournalStatus.POSTED,
                    message=f"Journal voided. Reversing entry: {reversing_number}"
                )

    async def get_journal(
        self,
        tenant_id: str,
        journal_id: UUID
    ) -> Optional[JournalEntry]:
        """
        Get a journal entry with its lines.

        Args:
            tenant_id: Tenant UUID
            journal_id: Journal UUID

        Returns:
            JournalEntry with lines, or None if not found
        """
        async with self.db.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true)",
                str(tenant_id)
            )

            # Get header (matching actual DB schema)
            header = await conn.fetchrow(
                """
                SELECT id, tenant_id, journal_number, journal_date,
                       description, source_type, source_id, trace_id,
                       status, voided_by, void_reason,
                       created_by, created_at, updated_at,
                       period_id, reversal_of_id, reversed_by_id,
                       reversal_reason, reversed_at
                FROM journal_entries
                WHERE tenant_id = $1 AND id = $2
                """,
                tenant_id, journal_id
            )

            if not header:
                return None

            # Get lines with account info (matching actual DB schema)
            lines = await conn.fetch(
                """
                SELECT jl.id, jl.journal_id, jl.account_id,
                       jl.line_number, jl.memo, jl.debit, jl.credit,
                       c.account_code, c.name as account_name
                FROM journal_lines jl
                JOIN chart_of_accounts c ON c.id = jl.account_id
                WHERE jl.journal_id = $1
                ORDER BY jl.line_number
                """,
                journal_id
            )

            journal = JournalEntry(
                id=header["id"],
                tenant_id=header["tenant_id"],
                journal_number=header["journal_number"],
                journal_date=header["journal_date"],
                description=header["description"],
                source_type=SourceType(header["source_type"]) if header["source_type"] else SourceType.MANUAL,
                source_id=header["source_id"],
                trace_id=header["trace_id"],
                status=JournalStatus(header["status"]),
                voided_by=header["voided_by"],
                void_reason=header["void_reason"],
                posted_by=header["created_by"],
                created_at=header["created_at"],
                period_id=header["period_id"],
                reversal_of_id=header["reversal_of_id"],
                reversed_by_id=header["reversed_by_id"],
                reversal_reason=header["reversal_reason"],
                reversed_at=header["reversed_at"],
                lines=[
                    JournalLine(
                        id=line["id"],
                        journal_id=line["journal_id"],
                        journal_date=header["journal_date"],  # From header
                        account_id=line["account_id"],
                        line_number=line["line_number"],
                        debit=Decimal(str(line["debit"])),
                        credit=Decimal(str(line["credit"])),
                        description=line["memo"],  # memo in DB
                        account_code=line["account_code"],
                        account_name=line["account_name"],
                    )
                    for line in lines
                ]
            )

            return journal

    async def list_journals(
        self,
        tenant_id: str,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        source_type: Optional[SourceType] = None,
        status: Optional[JournalStatus] = None,
        search: Optional[str] = None,
        limit: int = 50,
        offset: int = 0
    ) -> List[JournalEntry]:
        """
        List journal entries with filtering.

        Args:
            tenant_id: Tenant UUID
            start_date: Filter from date
            end_date: Filter to date
            source_type: Filter by source
            status: Filter by status
            search: Search in description/number
            limit: Max results
            offset: Skip results

        Returns:
            List of JournalEntry (without lines - use get_journal for full)
        """
        async with self.db.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true)",
                str(tenant_id)
            )

            conditions = ["tenant_id = $1"]
            params = [tenant_id]
            param_idx = 2

            if start_date:
                conditions.append(f"journal_date >= ${param_idx}")
                params.append(start_date)
                param_idx += 1

            if end_date:
                conditions.append(f"journal_date <= ${param_idx}")
                params.append(end_date)
                param_idx += 1

            if source_type:
                conditions.append(f"source_type = ${param_idx}")
                params.append(source_type.value)
                param_idx += 1

            if status:
                conditions.append(f"status = ${param_idx}")
                params.append(status.value)
                param_idx += 1

            if search:
                conditions.append(
                    f"(journal_number ILIKE ${param_idx} OR description ILIKE ${param_idx})"
                )
                params.append(f"%{search}%")
                param_idx += 1

            where_clause = " AND ".join(conditions)

            query = f"""
                SELECT id, tenant_id, journal_number, journal_date,
                       description, source_type, source_id, trace_id,
                       status, posted_at, posted_by, created_at
                FROM journal_entries
                WHERE {where_clause}
                ORDER BY journal_date DESC, created_at DESC
                LIMIT ${param_idx} OFFSET ${param_idx + 1}
            """
            params.extend([limit, offset])

            rows = await conn.fetch(query, *params)

            return [
                JournalEntry(
                    id=row["id"],
                    tenant_id=row["tenant_id"],
                    journal_number=row["journal_number"],
                    journal_date=row["journal_date"],
                    description=row["description"],
                    source_type=SourceType(row["source_type"]),
                    source_id=row["source_id"],
                    trace_id=row["trace_id"],
                    status=JournalStatus(row["status"]),
                    posted_at=row["posted_at"],
                    posted_by=row["posted_by"],
                    created_at=row["created_at"],
                )
                for row in rows
            ]

    async def _get_next_journal_number(
        self,
        conn,
        tenant_id: str,
        source_type: SourceType
    ) -> str:
        """Get next sequential journal number."""
        # Map source type to prefix
        prefix_map = {
            SourceType.INVOICE: settings.accounting.JOURNAL_PREFIX_SALES,
            SourceType.BILL: settings.accounting.JOURNAL_PREFIX_PURCHASE,
            SourceType.PAYMENT_RECEIVED: settings.accounting.JOURNAL_PREFIX_CASH,
            SourceType.PAYMENT_BILL: settings.accounting.JOURNAL_PREFIX_CASH,
            SourceType.POS: settings.accounting.JOURNAL_PREFIX_SALES,
            SourceType.ADJUSTMENT: settings.accounting.JOURNAL_PREFIX_ADJUSTMENT,
            SourceType.MANUAL: settings.accounting.JOURNAL_PREFIX_GENERAL,
            SourceType.CLOSING: "CL",
            SourceType.OPENING: "OP",
        }
        prefix = prefix_map.get(source_type, settings.accounting.JOURNAL_PREFIX_GENERAL)

        result = await conn.fetchval(
            "SELECT get_next_journal_number($1, $2)",
            tenant_id, prefix
        )
        return result

    async def _resolve_account_codes(
        self,
        tenant_id: str,
        lines: List[JournalLineInput]
    ) -> Tuple[List[Tuple[JournalLineInput, UUID]], List[str]]:
        """
        Resolve account codes to UUIDs.

        Returns:
            Tuple of (resolved_lines, errors)
        """
        resolved = []
        errors = []

        for line in lines:
            account_id = await self.coa.resolve_account_id(tenant_id, line.account_code)
            if not account_id:
                errors.append(f"Account code not found: {line.account_code}")
            else:
                resolved.append((line, account_id))

        return resolved, errors

    async def _create_outbox_event(
        self,
        conn,
        tenant_id: str,
        event_type: str,
        aggregate_id: str,
        payload: dict
    ):
        """Create outbox event for async publishing."""
        await conn.execute(
            """
            INSERT INTO accounting_outbox (tenant_id, event_type, aggregate_type, aggregate_id, payload)
            VALUES ($1, $2, $3, $4, $5)
            """,
            tenant_id,
            event_type,
            "journal",
            UUID(aggregate_id),
            json.dumps(payload, default=str)
        )

    async def _check_period_status(
        self,
        conn,
        tenant_id: str,
        journal_date: date,
        is_system_generated: bool = False
    ) -> Tuple[bool, Optional[str]]:
        """
        Check if posting is allowed for a specific date based on period status.

        Args:
            conn: Database connection
            tenant_id: Tenant identifier
            journal_date: Date to check
            is_system_generated: True if system-generated entry (e.g., reversal, closing)

        Returns:
            Tuple of (can_post, error_message)
        """
        # Get period for this date using the database function
        period = await conn.fetchrow(
            "SELECT * FROM get_fiscal_period_for_date($1, $2)",
            tenant_id,
            journal_date
        )

        if not period:
            # No period defined = allow (grace period for setup)
            return (True, None)

        status = period['status']

        if status == PeriodStatus.LOCKED.value:
            return (False, f"Period {period['period_name']} is locked - no modifications allowed")

        if status == PeriodStatus.CLOSED.value:
            if is_system_generated:
                return (True, None)  # System can post to CLOSED
            return (False, f"Period {period['period_name']} is closed - manual posting not allowed")

        # OPEN - all operations allowed
        return (True, None)

    async def reverse_journal(
        self,
        tenant_id: str,
        journal_id: UUID,
        reversal_date: date,
        reversed_by: UUID,
        reason: str
    ) -> JournalResponse:
        """
        Reverse a journal entry (first-class reversal, not void).

        Creates a new journal entry with debit/credit swapped,
        linked to the original via reversal_of_id.

        Rules:
        - Original journal cannot already be reversed
        - Original period cannot be LOCKED
        - Reversal must be posted to OPEN period
        - Reason is mandatory

        Args:
            tenant_id: Tenant identifier
            journal_id: Original journal to reverse
            reversal_date: Date for the reversal entry
            reversed_by: User performing the reversal
            reason: Mandatory reason for reversal

        Returns:
            JournalResponse with reversal journal details
        """
        if not reason or not reason.strip():
            return JournalResponse(
                success=False,
                errors=["Reversal reason is required"],
                message="Validation failed"
            )

        async with self.db.acquire() as conn:
            async with conn.transaction():
                # Set tenant context for RLS
                await conn.execute(
                    "SELECT set_config('app.tenant_id', $1, true)",
                    tenant_id
                )

                # 1. Load original journal
                original = await conn.fetchrow(
                    """
                    SELECT id, journal_number, journal_date, description,
                           source_type, source_id, status, reversed_by_id
                    FROM journal_entries
                    WHERE tenant_id = $1 AND id = $2
                    """,
                    tenant_id, journal_id
                )

                if not original:
                    return JournalResponse(
                        success=False,
                        errors=["Journal not found"],
                        message="Reversal failed"
                    )

                # 2. Check if already reversed
                if original["reversed_by_id"]:
                    return JournalResponse(
                        success=False,
                        errors=["Journal has already been reversed"],
                        message="Reversal failed"
                    )

                # 3. Check original period - cannot reverse from LOCKED
                can_reverse_from, period_error = await self._check_period_status(
                    conn,
                    tenant_id,
                    original["journal_date"],
                    is_system_generated=False
                )
                if not can_reverse_from:
                    return JournalResponse(
                        success=False,
                        errors=[f"Original journal period: {period_error}"],
                        message="Reversal failed"
                    )

                # 4. Check reversal date period - must be OPEN
                can_post_reversal, reversal_period_error = await self._check_period_status(
                    conn,
                    tenant_id,
                    reversal_date,
                    is_system_generated=True  # Reversal is system-generated
                )
                if not can_post_reversal:
                    return JournalResponse(
                        success=False,
                        errors=[f"Reversal date period: {reversal_period_error}"],
                        message="Reversal failed"
                    )

                # 5. Get original lines
                original_lines = await conn.fetch(
                    """
                    SELECT account_id, memo, debit, credit
                    FROM journal_lines
                    WHERE journal_id = $1
                    ORDER BY line_number
                    """,
                    journal_id
                )

                if not original_lines:
                    return JournalResponse(
                        success=False,
                        errors=["Original journal has no lines"],
                        message="Reversal failed"
                    )

                # 6. Generate reversal journal number
                reversal_number = await self._get_next_journal_number(
                    conn,
                    tenant_id,
                    SourceType.ADJUSTMENT  # Reversals use ADJUSTMENT prefix
                )

                # 7. Create reversal journal header
                reversal_id = uuid4()
                now = datetime.now()

                await conn.execute(
                    """
                    INSERT INTO journal_entries (
                        id, tenant_id, journal_number, journal_date, description,
                        source_type, source_id, trace_id, status,
                        created_by, reversal_of_id, reversal_reason
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                    """,
                    reversal_id,
                    tenant_id,
                    reversal_number,
                    reversal_date,
                    f"Reversal of {original['journal_number']}: {reason}",
                    SourceType.ADJUSTMENT.value,
                    original["source_id"],
                    str(uuid4()),  # New trace_id for reversal (TEXT type)
                    JournalStatus.POSTED.value,
                    reversed_by,
                    journal_id,  # reversal_of_id
                    reason
                )

                # 8. Create reversal lines (swap debit/credit)
                for i, line in enumerate(original_lines, start=1):
                    await conn.execute(
                        """
                        INSERT INTO journal_lines (
                            id, journal_id, account_id, line_number,
                            memo, debit, credit
                        )
                        VALUES ($1, $2, $3, $4, $5, $6, $7)
                        """,
                        uuid4(),
                        reversal_id,
                        line["account_id"],
                        i,
                        f"Reversal: {line['memo'] or ''}",
                        line["credit"],  # Swap: original credit → reversal debit
                        line["debit"]    # Swap: original debit → reversal credit
                    )

                # 9. Mark original as reversed
                await conn.execute(
                    """
                    UPDATE journal_entries
                    SET reversed_by_id = $1, reversed_at = $2
                    WHERE id = $3
                    """,
                    reversal_id,
                    now,
                    journal_id
                )

                # 10. Publish event
                await self._create_outbox_event(
                    conn,
                    tenant_id,
                    "accounting.journal.reversed",
                    str(reversal_id),
                    {
                        "original_journal_id": str(journal_id),
                        "original_journal_number": original["journal_number"],
                        "reversal_journal_id": str(reversal_id),
                        "reversal_journal_number": reversal_number,
                        "reversal_date": reversal_date.isoformat(),
                        "reason": reason,
                        "reversed_by": str(reversed_by)
                    }
                )

                logger.info(
                    f"Journal reversed: {original['journal_number']} → {reversal_number}"
                )

                return JournalResponse(
                    success=True,
                    journal_id=reversal_id,
                    journal_number=reversal_number,
                    status=JournalStatus.POSTED,
                    message=f"Reversal created: {reversal_number}"
                )
