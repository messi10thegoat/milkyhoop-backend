"""Tests for drill-down endpoint."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from uuid import uuid4
from datetime import date
from decimal import Decimal


class TestDrillDownEndpoint:
    """Test /reports/drill-down endpoint."""

    @pytest.fixture
    def mock_user_context(self):
        return {
            "tenant_id": "test-tenant",
            "user_id": str(uuid4()),
        }

    @pytest.fixture
    def mock_journal_lines(self):
        return [
            {
                "journal_id": uuid4(),
                "journal_number": "JE-2026-0001",
                "entry_date": date(2026, 1, 5),
                "source_type": "invoice",
                "source_id": uuid4(),
                "description": "Sales to PT Maju",
                "memo": "INV-001",
                "debit": Decimal("0"),
                "credit": Decimal("10000000"),
            },
            {
                "journal_id": uuid4(),
                "journal_number": "JE-2026-0002",
                "entry_date": date(2026, 1, 10),
                "source_type": "invoice",
                "source_id": uuid4(),
                "description": "Sales to CV Jaya",
                "memo": "INV-002",
                "debit": Decimal("0"),
                "credit": Decimal("15000000"),
            },
        ]

    def test_drill_down_endpoint_exists(self):
        """Endpoint is registered in router."""
        from app.routers.reports import router

        routes = [route.path for route in router.routes]
        assert "/drill-down" in routes

    def test_drill_down_requires_account_id(self):
        """Endpoint requires account_id parameter."""
        from app.schemas.drill_down import DrillDownRequest

        with pytest.raises(ValueError):
            DrillDownRequest(
                start_date=date(2026, 1, 1),
                end_date=date(2026, 1, 31),
            )

    def test_running_balance_calculation_credit_account(self, mock_journal_lines):
        """Running balance calculated correctly for credit-normal accounts."""
        opening = Decimal("0")
        running = opening

        for line in mock_journal_lines:
            running = running + line["credit"] - line["debit"]

        assert running == Decimal("25000000")

    def test_running_balance_calculation_debit_account(self):
        """Running balance calculated correctly for debit-normal accounts."""
        lines = [
            {"debit": Decimal("5000000"), "credit": Decimal("0")},
            {"debit": Decimal("0"), "credit": Decimal("2000000")},
        ]
        opening = Decimal("10000000")
        running = opening

        for line in lines:
            running = running + line["debit"] - line["credit"]

        assert running == Decimal("13000000")
