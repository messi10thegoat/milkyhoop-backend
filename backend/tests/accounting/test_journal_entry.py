"""
Journal Entry Tests
====================

Test journal entry creation, validation, and constraints.
"""

import pytest
from decimal import Decimal
from datetime import date
from uuid import uuid4

from accounting_kernel.constants import SourceType, JournalStatus
from accounting_kernel.models.journal import CreateJournalRequest, JournalLineInput

from .conftest import TEST_TENANT_ID, create_test_journal


class TestJournalEntryCreation:
    """Test journal entry creation."""

    @pytest.mark.asyncio
    async def test_create_balanced_journal_entry(
        self, db_conn, tenant_id, cash_account_id, revenue_account_id
    ):
        """Balanced journal entry (debit = credit) should succeed."""
        journal_id, journal_number = await create_test_journal(
            db_conn, tenant_id, cash_account_id, revenue_account_id,
            amount=Decimal("100000")
        )

        # Verify journal was created
        row = await db_conn.fetchrow(
            "SELECT total_debit, total_credit, status FROM journal_entries WHERE id = $1",
            journal_id
        )

        assert row is not None
        assert row['total_debit'] == row['total_credit']
        assert row['status'] == 'POSTED'

    @pytest.mark.asyncio
    async def test_create_journal_with_multiple_lines(
        self, db_conn, tenant_id, cash_account_id, bank_account_id, revenue_account_id
    ):
        """Journal entry with multiple debit lines should work."""
        journal_id = uuid4()
        journal_number = f"JV-MULTI-{uuid4().hex[:8].upper()}"
        amount = Decimal("100000")

        # Create header
        await db_conn.execute(
            """
            INSERT INTO journal_entries (
                id, tenant_id, journal_number, journal_date, description,
                source_type, trace_id, status, total_debit, total_credit
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            """,
            journal_id, tenant_id, journal_number, date.today(),
            "Multi-line test", SourceType.MANUAL.value, str(uuid4()),
            "POSTED", float(amount), float(amount)
        )

        # Create lines: Cash 60k, Bank 40k = Revenue 100k
        await db_conn.execute(
            """
            INSERT INTO journal_lines (id, journal_id, account_id, line_number, debit, credit, memo)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            uuid4(), journal_id, cash_account_id, 1, 60000, 0, "Cash portion"
        )

        await db_conn.execute(
            """
            INSERT INTO journal_lines (id, journal_id, account_id, line_number, debit, credit, memo)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            uuid4(), journal_id, bank_account_id, 2, 40000, 0, "Bank portion"
        )

        await db_conn.execute(
            """
            INSERT INTO journal_lines (id, journal_id, account_id, line_number, debit, credit, memo)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            uuid4(), journal_id, revenue_account_id, 3, 0, 100000, "Sales"
        )

        # Verify totals
        lines = await db_conn.fetch(
            "SELECT SUM(debit) as total_dr, SUM(credit) as total_cr FROM journal_lines WHERE journal_id = $1",
            journal_id
        )

        assert lines[0]['total_dr'] == lines[0]['total_cr']

    @pytest.mark.asyncio
    async def test_journal_source_types(
        self, db_conn, tenant_id, cash_account_id, revenue_account_id
    ):
        """Different source types should be properly recorded."""
        source_types = [
            SourceType.INVOICE,
            SourceType.BILL,
            SourceType.POS,
            SourceType.MANUAL,
        ]

        for src_type in source_types:
            journal_id = uuid4()

            await db_conn.execute(
                """
                INSERT INTO journal_entries (
                    id, tenant_id, journal_number, journal_date, description,
                    source_type, trace_id, status, total_debit, total_credit
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                """,
                journal_id, tenant_id, f"JV-{src_type.value}-001", date.today(),
                f"Test {src_type.value}", src_type.value, str(uuid4()),
                "POSTED", 100000, 100000
            )

            # Verify source type
            row = await db_conn.fetchrow(
                "SELECT source_type FROM journal_entries WHERE id = $1",
                journal_id
            )
            assert row['source_type'] == src_type.value


class TestJournalIdempotency:
    """Test journal idempotency via trace_id."""

    @pytest.mark.asyncio
    async def test_duplicate_trace_id_detected(self, journal_service, tenant_id, setup_coa):
        """Creating journal with same trace_id should return existing journal."""
        trace_id = str(uuid4())

        # First request
        request1 = CreateJournalRequest(
            tenant_id=tenant_id,
            journal_date=date.today(),
            description="First entry",
            source_type=SourceType.MANUAL,
            trace_id=trace_id,
            lines=[
                JournalLineInput(account_code="1-10100", debit=Decimal("50000"), credit=Decimal("0")),
                JournalLineInput(account_code="4-10100", debit=Decimal("0"), credit=Decimal("50000")),
            ]
        )

        result1 = await journal_service.create_journal(request1)
        assert result1.success

        # Second request with same trace_id
        request2 = CreateJournalRequest(
            tenant_id=tenant_id,
            journal_date=date.today(),
            description="Second entry (should be duplicate)",
            source_type=SourceType.MANUAL,
            trace_id=trace_id,
            lines=[
                JournalLineInput(account_code="1-10100", debit=Decimal("50000"), credit=Decimal("0")),
                JournalLineInput(account_code="4-10100", debit=Decimal("0"), credit=Decimal("50000")),
            ]
        )

        result2 = await journal_service.create_journal(request2)
        assert result2.success
        assert result2.is_duplicate
        assert result2.journal_id == result1.journal_id


class TestJournalValidation:
    """Test journal validation rules."""

    @pytest.mark.asyncio
    async def test_reject_unbalanced_request(self, journal_service, tenant_id, setup_coa):
        """Journal request with debit != credit should be rejected."""
        request = CreateJournalRequest(
            tenant_id=tenant_id,
            journal_date=date.today(),
            description="Unbalanced entry",
            source_type=SourceType.MANUAL,
            lines=[
                JournalLineInput(account_code="1-10100", debit=Decimal("100000"), credit=Decimal("0")),
                JournalLineInput(account_code="4-10100", debit=Decimal("0"), credit=Decimal("50000")),
            ]
        )

        result = await journal_service.create_journal(request)
        assert not result.success
        assert len(result.errors) > 0

    @pytest.mark.asyncio
    async def test_reject_empty_lines(self, journal_service, tenant_id):
        """Journal request with no lines should be rejected."""
        request = CreateJournalRequest(
            tenant_id=tenant_id,
            journal_date=date.today(),
            description="No lines",
            source_type=SourceType.MANUAL,
            lines=[]
        )

        result = await journal_service.create_journal(request)
        assert not result.success

    @pytest.mark.asyncio
    async def test_reject_zero_amount_line(self, journal_service, tenant_id, setup_coa):
        """Journal line with both debit=0 and credit=0 should be rejected."""
        request = CreateJournalRequest(
            tenant_id=tenant_id,
            journal_date=date.today(),
            description="Zero line",
            source_type=SourceType.MANUAL,
            lines=[
                JournalLineInput(account_code="1-10100", debit=Decimal("0"), credit=Decimal("0")),
                JournalLineInput(account_code="4-10100", debit=Decimal("0"), credit=Decimal("0")),
            ]
        )

        result = await journal_service.create_journal(request)
        assert not result.success

    @pytest.mark.asyncio
    async def test_reject_invalid_account_code(self, journal_service, tenant_id, setup_coa):
        """Journal with non-existent account code should be rejected."""
        request = CreateJournalRequest(
            tenant_id=tenant_id,
            journal_date=date.today(),
            description="Invalid account",
            source_type=SourceType.MANUAL,
            lines=[
                JournalLineInput(account_code="9-99999", debit=Decimal("100000"), credit=Decimal("0")),
                JournalLineInput(account_code="4-10100", debit=Decimal("0"), credit=Decimal("100000")),
            ]
        )

        result = await journal_service.create_journal(request)
        assert not result.success
        assert "not found" in str(result.errors).lower()
