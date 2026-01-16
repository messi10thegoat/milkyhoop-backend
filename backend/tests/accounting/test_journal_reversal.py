"""
Journal Reversal and Immutability Tests
========================================

Test journal reversal functionality and immutability enforcement.
These tests verify Task 4 (journal immutability triggers).
"""

import pytest
from decimal import Decimal
from datetime import date
from uuid import uuid4

from accounting_kernel.constants import JournalStatus

from .conftest import TEST_TENANT_ID, create_test_journal


class TestJournalReversal:
    """Test journal reversal functionality."""

    @pytest.mark.asyncio
    async def test_reversal_creates_opposite_entry(
        self, journal_service, tenant_id, db_conn, setup_coa
    ):
        """Reversal should create a journal with swapped debit/credit."""
        cash_id = setup_coa['1-10100']
        revenue_id = setup_coa['4-10100']

        # Create original journal
        original_id, original_number = await create_test_journal(
            db_conn, tenant_id, cash_id, revenue_id,
            amount=Decimal("100000")
        )

        # Reverse it
        result = await journal_service.reverse_journal(
            tenant_id=tenant_id,
            journal_id=original_id,
            reversal_date=date.today(),
            reversed_by=uuid4(),
            reason="Test reversal"
        )

        assert result.success, f"Reversal failed: {result.errors}"

        # Verify reversal journal exists
        reversal = await db_conn.fetchrow(
            "SELECT * FROM journal_entries WHERE id = $1",
            result.journal_id
        )
        assert reversal is not None
        assert reversal['reversal_of_id'] == original_id

        # Verify lines are swapped
        original_lines = await db_conn.fetch(
            "SELECT account_id, debit, credit FROM journal_lines WHERE journal_id = $1",
            original_id
        )
        reversal_lines = await db_conn.fetch(
            "SELECT account_id, debit, credit FROM journal_lines WHERE journal_id = $1",
            result.journal_id
        )

        # For each original line, find corresponding reversal line with swapped amounts
        for orig_line in original_lines:
            matching = [r for r in reversal_lines if r['account_id'] == orig_line['account_id']]
            assert len(matching) == 1
            rev_line = matching[0]
            # Debit and credit should be swapped
            assert rev_line['debit'] == orig_line['credit']
            assert rev_line['credit'] == orig_line['debit']

    @pytest.mark.asyncio
    async def test_reversal_net_effect_is_zero(
        self, journal_service, tenant_id, db_conn, setup_coa
    ):
        """After reversal, net effect on accounts should be zero."""
        cash_id = setup_coa['1-10100']
        revenue_id = setup_coa['4-10100']

        # Create and reverse journal
        original_id, _ = await create_test_journal(
            db_conn, tenant_id, cash_id, revenue_id,
            amount=Decimal("100000")
        )

        await journal_service.reverse_journal(
            tenant_id=tenant_id,
            journal_id=original_id,
            reversal_date=date.today(),
            reversed_by=uuid4(),
            reason="Test"
        )

        # Check net effect on cash account
        result = await db_conn.fetchrow(
            """
            SELECT
                SUM(jl.debit) as total_dr,
                SUM(jl.credit) as total_cr
            FROM journal_lines jl
            JOIN journal_entries je ON je.id = jl.journal_id
            WHERE jl.account_id = $1
              AND je.tenant_id = $2
              AND je.status = 'POSTED'
            """,
            cash_id, tenant_id
        )

        # Net should be zero (original debit cancelled by reversal credit)
        net = (result['total_dr'] or 0) - (result['total_cr'] or 0)
        assert abs(net) < 0.01, f"Net effect should be zero, got {net}"

    @pytest.mark.asyncio
    async def test_cannot_reverse_already_reversed(
        self, journal_service, tenant_id, db_conn, setup_coa
    ):
        """Cannot reverse a journal that has already been reversed."""
        cash_id = setup_coa['1-10100']
        revenue_id = setup_coa['4-10100']

        original_id, _ = await create_test_journal(
            db_conn, tenant_id, cash_id, revenue_id,
            amount=Decimal("100000")
        )

        # First reversal should succeed
        result1 = await journal_service.reverse_journal(
            tenant_id=tenant_id,
            journal_id=original_id,
            reversal_date=date.today(),
            reversed_by=uuid4(),
            reason="First reversal"
        )
        assert result1.success

        # Second reversal should fail
        result2 = await journal_service.reverse_journal(
            tenant_id=tenant_id,
            journal_id=original_id,
            reversal_date=date.today(),
            reversed_by=uuid4(),
            reason="Second reversal attempt"
        )
        assert not result2.success
        assert "already" in str(result2.errors).lower()


class TestJournalImmutability:
    """
    Test that POSTED journals cannot be modified.
    These tests verify the database triggers from V029.
    """

    @pytest.mark.asyncio
    async def test_cannot_update_posted_journal_amount(
        self, db_conn, tenant_id, cash_account_id, revenue_account_id
    ):
        """POSTED journal total_debit/total_credit cannot be changed."""
        journal_id, _ = await create_test_journal(
            db_conn, tenant_id, cash_account_id, revenue_account_id,
            amount=Decimal("100000")
        )

        # Attempt to modify amounts should fail
        with pytest.raises(Exception) as exc_info:
            await db_conn.execute(
                "UPDATE journal_entries SET total_debit = 999999 WHERE id = $1",
                journal_id
            )

        # Should contain error about POSTED
        assert "POSTED" in str(exc_info.value) or "cannot" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_cannot_update_posted_journal_date(
        self, db_conn, tenant_id, cash_account_id, revenue_account_id
    ):
        """POSTED journal date cannot be changed."""
        journal_id, _ = await create_test_journal(
            db_conn, tenant_id, cash_account_id, revenue_account_id,
            amount=Decimal("100000")
        )

        from datetime import timedelta
        new_date = date.today() - timedelta(days=30)

        with pytest.raises(Exception) as exc_info:
            await db_conn.execute(
                "UPDATE journal_entries SET journal_date = $1 WHERE id = $2",
                new_date, journal_id
            )

        assert "POSTED" in str(exc_info.value) or "cannot" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_cannot_delete_posted_journal(
        self, db_conn, tenant_id, cash_account_id, revenue_account_id
    ):
        """POSTED journal cannot be deleted."""
        journal_id, _ = await create_test_journal(
            db_conn, tenant_id, cash_account_id, revenue_account_id,
            amount=Decimal("100000")
        )

        with pytest.raises(Exception) as exc_info:
            await db_conn.execute(
                "DELETE FROM journal_entries WHERE id = $1",
                journal_id
            )

        assert "delete" in str(exc_info.value).lower() or "POSTED" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_cannot_modify_posted_journal_lines(
        self, db_conn, tenant_id, cash_account_id, revenue_account_id
    ):
        """Lines of POSTED journal cannot be modified."""
        journal_id, _ = await create_test_journal(
            db_conn, tenant_id, cash_account_id, revenue_account_id,
            amount=Decimal("100000")
        )

        # Get a line ID
        line_id = await db_conn.fetchval(
            "SELECT id FROM journal_lines WHERE journal_id = $1 LIMIT 1",
            journal_id
        )

        # Attempt to modify line
        with pytest.raises(Exception) as exc_info:
            await db_conn.execute(
                "UPDATE journal_lines SET debit = 999999 WHERE id = $1",
                line_id
            )

        assert "POSTED" in str(exc_info.value) or "cannot" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_cannot_delete_posted_journal_lines(
        self, db_conn, tenant_id, cash_account_id, revenue_account_id
    ):
        """Lines of POSTED journal cannot be deleted."""
        journal_id, _ = await create_test_journal(
            db_conn, tenant_id, cash_account_id, revenue_account_id,
            amount=Decimal("100000")
        )

        line_id = await db_conn.fetchval(
            "SELECT id FROM journal_lines WHERE journal_id = $1 LIMIT 1",
            journal_id
        )

        with pytest.raises(Exception) as exc_info:
            await db_conn.execute(
                "DELETE FROM journal_lines WHERE id = $1",
                line_id
            )

        assert "POSTED" in str(exc_info.value) or "cannot" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_void_flow_is_allowed(
        self, journal_service, tenant_id, db_conn, setup_coa
    ):
        """Voiding a POSTED journal should still work (legitimate flow)."""
        cash_id = setup_coa['1-10100']
        revenue_id = setup_coa['4-10100']

        journal_id, journal_number = await create_test_journal(
            db_conn, tenant_id, cash_id, revenue_id,
            amount=Decimal("100000")
        )

        # Void should work (this uses the void_journal service method)
        result = await journal_service.void_journal(
            tenant_id=tenant_id,
            journal_id=journal_id,
            voided_by=uuid4(),
            reason="Test void"
        )

        assert result.success, f"Void should be allowed: {result.errors}"

        # Verify status changed to VOID
        row = await db_conn.fetchrow(
            "SELECT status FROM journal_entries WHERE id = $1",
            journal_id
        )
        assert row['status'] == 'VOID'


class TestJournalVoid:
    """Test journal void functionality."""

    @pytest.mark.asyncio
    async def test_void_creates_reversing_entry(
        self, journal_service, tenant_id, db_conn, setup_coa
    ):
        """Voiding a journal should create a reversing entry."""
        cash_id = setup_coa['1-10100']
        revenue_id = setup_coa['4-10100']

        journal_id, _ = await create_test_journal(
            db_conn, tenant_id, cash_id, revenue_id,
            amount=Decimal("100000")
        )

        result = await journal_service.void_journal(
            tenant_id=tenant_id,
            journal_id=journal_id,
            voided_by=uuid4(),
            reason="Void test"
        )

        assert result.success

        # Should have created a reversing entry
        reversing = await db_conn.fetchrow(
            "SELECT * FROM journal_entries WHERE source_id = $1 AND source_type = 'ADJUSTMENT'",
            journal_id
        )
        assert reversing is not None

    @pytest.mark.asyncio
    async def test_cannot_void_already_voided(
        self, journal_service, tenant_id, db_conn, setup_coa
    ):
        """Cannot void a journal that is already voided."""
        cash_id = setup_coa['1-10100']
        revenue_id = setup_coa['4-10100']

        journal_id, _ = await create_test_journal(
            db_conn, tenant_id, cash_id, revenue_id,
            amount=Decimal("100000")
        )

        # First void
        result1 = await journal_service.void_journal(
            tenant_id=tenant_id,
            journal_id=journal_id,
            voided_by=uuid4(),
            reason="First void"
        )
        assert result1.success

        # Second void should fail
        result2 = await journal_service.void_journal(
            tenant_id=tenant_id,
            journal_id=journal_id,
            voided_by=uuid4(),
            reason="Second void"
        )
        assert not result2.success
        assert "voided" in str(result2.errors).lower() or "already" in str(result2.errors).lower()
