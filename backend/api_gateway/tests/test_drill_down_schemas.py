"""Tests for drill-down report schemas."""
import pytest
from datetime import date
from decimal import Decimal
from uuid import uuid4


class TestDrillDownSchemas:
    """Test drill-down Pydantic schemas."""

    def test_schema_import(self):
        """Schema classes can be imported."""
        from app.schemas.drill_down import (
            DrillDownTransaction,
            DrillDownResponse,
            DrillDownRequest,
        )
        assert DrillDownTransaction is not None
        assert DrillDownResponse is not None
        assert DrillDownRequest is not None

    def test_drill_down_transaction_model(self):
        """DrillDownTransaction model validates correctly."""
        from app.schemas.drill_down import DrillDownTransaction

        tx = DrillDownTransaction(
            journal_id=uuid4(),
            journal_number="JE-2026-0001",
            entry_date=date(2026, 1, 15),
            source_type="invoice",
            source_id=uuid4(),
            description="Sales to PT Maju",
            memo="INV-001",
            debit=Decimal("0"),
            credit=Decimal("10000000"),
            running_balance=Decimal("10000000"),
        )
        assert tx.journal_number == "JE-2026-0001"
        assert tx.credit == Decimal("10000000")

    def test_drill_down_response_model(self):
        """DrillDownResponse model validates correctly."""
        from app.schemas.drill_down import DrillDownResponse, DrillDownTransaction

        response = DrillDownResponse(
            account_id=uuid4(),
            account_code="4-1001",
            account_name="Penjualan Barang",
            account_type="REVENUE",
            normal_balance="CREDIT",
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 31),
            opening_balance=Decimal("0"),
            total_debit=Decimal("0"),
            total_credit=Decimal("50000000"),
            closing_balance=Decimal("50000000"),
            transactions=[],
            pagination={"page": 1, "limit": 50, "total": 0, "has_more": False},
        )
        assert response.account_code == "4-1001"
        assert response.closing_balance == Decimal("50000000")
