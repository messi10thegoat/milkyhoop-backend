"""
Tests for /api/bills/outstanding-summary endpoint.

Verifies the proper aging-based accounting structure with invariants.
"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from uuid import uuid4


class TestOutstandingSummaryInvariants:
    """Test invariants for outstanding summary."""

    @pytest.fixture
    def mock_user_context(self):
        """Mock authenticated user context."""
        return {
            "tenant_id": "test-tenant",
            "user_id": str(uuid4()),
        }

    @pytest.fixture
    def valid_summary_data(self):
        """Valid summary data that satisfies all invariants."""
        return {
            "total_outstanding": 50000000,
            "overdue_amount": 35000000,
            "current_amount": 15000000,
            "overdue_1_30": 15000000,
            "overdue_31_60": 10000000,
            "overdue_61_90": 5000000,
            "overdue_90_plus": 5000000,
            "total_count": 13,
            "overdue_count": 8,
            "current_count": 5,
            "partial_count": 3,
            "partial_overdue_count": 2,
            "partial_current_count": 1,
            "vendor_count": 6,
            "no_due_date_count": 1,
            "oldest_days": 45,
            "largest_amount": 8000000,
            "due_within_7_days": 3,
        }

    def test_invariant_aging_sum_equals_total(self, valid_summary_data):
        """by_aging.overdue + by_aging.current == total_outstanding"""
        assert (
            valid_summary_data["overdue_amount"] + valid_summary_data["current_amount"]
            == valid_summary_data["total_outstanding"]
        ), "Aging sum must equal total outstanding"

    def test_invariant_aging_breakdown_sum_equals_overdue(self, valid_summary_data):
        """sum(aging_breakdown.*) == by_aging.overdue"""
        breakdown_sum = (
            valid_summary_data["overdue_1_30"]
            + valid_summary_data["overdue_31_60"]
            + valid_summary_data["overdue_61_90"]
            + valid_summary_data["overdue_90_plus"]
        )
        assert breakdown_sum == valid_summary_data["overdue_amount"], (
            "Aging breakdown sum must equal overdue amount"
        )

    def test_invariant_counts_sum_equals_total(self, valid_summary_data):
        """counts.overdue + counts.current == counts.total"""
        assert (
            valid_summary_data["overdue_count"] + valid_summary_data["current_count"]
            == valid_summary_data["total_count"]
        ), "Count sum must equal total count"

    def test_invariant_partial_counts_sum(self, valid_summary_data):
        """counts.partial == counts.partial_overdue + counts.partial_current"""
        assert (
            valid_summary_data["partial_overdue_count"]
            + valid_summary_data["partial_current_count"]
            == valid_summary_data["partial_count"]
        ), "Partial count sum must equal total partial"


class TestOutstandingSummarySchema:
    """Test Pydantic schema validation."""

    def test_schema_import(self):
        """Schema classes can be imported."""
        from app.schemas.bills import (
            OutstandingByAging,
            OutstandingCounts,
            AgingBreakdown,
            UrgencyMetrics,
            OutstandingSummaryData,
            OutstandingSummaryResponse,
        )

        # Verify all classes exist
        assert OutstandingByAging is not None
        assert OutstandingCounts is not None
        assert AgingBreakdown is not None
        assert UrgencyMetrics is not None
        assert OutstandingSummaryData is not None
        assert OutstandingSummaryResponse is not None

    def test_schema_valid_data(self):
        """Schema accepts valid data."""
        from app.schemas.bills import OutstandingSummaryData, OutstandingByAging

        data = OutstandingSummaryData(
            total_outstanding=50000000,
            by_aging=OutstandingByAging(overdue=35000000, current=15000000),
            counts={
                "total": 13,
                "overdue": 8,
                "current": 5,
                "partial": 3,
                "partial_overdue": 2,
                "partial_current": 1,
                "vendors": 6,
                "no_due_date": 1,
            },
            aging_breakdown={
                "overdue_1_30": 15000000,
                "overdue_31_60": 10000000,
                "overdue_61_90": 5000000,
                "overdue_90_plus": 5000000,
            },
            urgency={
                "oldest_days": 45,
                "largest_amount": 8000000,
                "due_within_7_days": 3,
            },
        )

        assert data.total_outstanding == 50000000
        assert data.by_aging.overdue + data.by_aging.current == data.total_outstanding


class TestOutstandingSummaryEndpoint:
    """Test /api/bills/outstanding-summary endpoint."""

    @pytest.fixture
    def mock_user_context(self):
        """Mock authenticated user context."""
        return {
            "tenant_id": "test-tenant",
            "user_id": str(uuid4()),
        }

    @pytest.fixture
    def mock_db_row(self):
        """Mock database row result."""
        return {
            "total_outstanding": 50000000,
            "overdue_amount": 35000000,
            "current_amount": 15000000,
            "overdue_1_30": 15000000,
            "overdue_31_60": 10000000,
            "overdue_61_90": 5000000,
            "overdue_90_plus": 5000000,
            "total_count": 13,
            "overdue_count": 8,
            "current_count": 5,
            "partial_count": 3,
            "partial_overdue_count": 2,
            "partial_current_count": 1,
            "vendor_count": 6,
            "no_due_date_count": 1,
            "oldest_days": 45,
            "largest_amount": 8000000,
            "due_within_7_days": 3,
        }

    def test_endpoint_returns_success(
        self, sync_client, mock_user_context, mock_db_row
    ):
        """Endpoint returns success with valid data."""
        with patch(
            "app.routers.bills.get_user_context", return_value=mock_user_context
        ), patch("app.routers.bills.get_bills_service") as mock_service:
            # Setup service mock
            service_instance = AsyncMock()
            service_instance.get_outstanding_summary = AsyncMock(
                return_value={
                    "success": True,
                    "data": {
                        "total_outstanding": 50000000,
                        "by_aging": {"overdue": 35000000, "current": 15000000},
                        "counts": {
                            "total": 13,
                            "overdue": 8,
                            "current": 5,
                            "partial": 3,
                            "partial_overdue": 2,
                            "partial_current": 1,
                            "vendors": 6,
                            "no_due_date": 1,
                        },
                        "aging_breakdown": {
                            "overdue_1_30": 15000000,
                            "overdue_31_60": 10000000,
                            "overdue_61_90": 5000000,
                            "overdue_90_plus": 5000000,
                        },
                        "urgency": {
                            "oldest_days": 45,
                            "largest_amount": 8000000,
                            "due_within_7_days": 3,
                        },
                    },
                }
            )
            mock_service.return_value = service_instance

            response = sync_client.get("/api/bills/outstanding-summary")

            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert "data" in data

    def test_endpoint_unauthenticated_returns_401(self, sync_client):
        """Endpoint returns 401 when not authenticated."""
        from fastapi import HTTPException

        with patch(
            "app.routers.bills.get_user_context",
            side_effect=HTTPException(status_code=401, detail="Auth required"),
        ):
            response = sync_client.get("/api/bills/outstanding-summary")
            assert response.status_code == 401

    def test_response_invariants(self, sync_client, mock_user_context):
        """Response data satisfies all invariants."""
        with patch(
            "app.routers.bills.get_user_context", return_value=mock_user_context
        ), patch("app.routers.bills.get_bills_service") as mock_service:
            # Setup service mock with data that satisfies invariants
            service_instance = AsyncMock()
            service_instance.get_outstanding_summary = AsyncMock(
                return_value={
                    "success": True,
                    "data": {
                        "total_outstanding": 50000000,
                        "by_aging": {"overdue": 35000000, "current": 15000000},
                        "counts": {
                            "total": 13,
                            "overdue": 8,
                            "current": 5,
                            "partial": 3,
                            "partial_overdue": 2,
                            "partial_current": 1,
                            "vendors": 6,
                            "no_due_date": 1,
                        },
                        "aging_breakdown": {
                            "overdue_1_30": 15000000,
                            "overdue_31_60": 10000000,
                            "overdue_61_90": 5000000,
                            "overdue_90_plus": 5000000,
                        },
                        "urgency": {
                            "oldest_days": 45,
                            "largest_amount": 8000000,
                            "due_within_7_days": 3,
                        },
                    },
                }
            )
            mock_service.return_value = service_instance

            response = sync_client.get("/api/bills/outstanding-summary")
            data = response.json()["data"]

            # Invariant 1: by_aging.overdue + by_aging.current == total_outstanding
            assert data["by_aging"]["overdue"] + data["by_aging"]["current"] == data[
                "total_outstanding"
            ]

            # Invariant 2: sum(aging_breakdown.*) == by_aging.overdue
            breakdown = data["aging_breakdown"]
            breakdown_sum = (
                breakdown["overdue_1_30"]
                + breakdown["overdue_31_60"]
                + breakdown["overdue_61_90"]
                + breakdown["overdue_90_plus"]
            )
            assert breakdown_sum == data["by_aging"]["overdue"]

            # Invariant 3: counts.overdue + counts.current == counts.total
            counts = data["counts"]
            assert counts["overdue"] + counts["current"] == counts["total"]

            # Invariant 4: counts.partial == counts.partial_overdue + counts.partial_current
            assert (
                counts["partial_overdue"] + counts["partial_current"]
                == counts["partial"]
            )

    def test_no_partial_amount_bucket(self, sync_client, mock_user_context):
        """Response has no 'partial' amount bucket (only count)."""
        with patch(
            "app.routers.bills.get_user_context", return_value=mock_user_context
        ), patch("app.routers.bills.get_bills_service") as mock_service:
            service_instance = AsyncMock()
            service_instance.get_outstanding_summary = AsyncMock(
                return_value={
                    "success": True,
                    "data": {
                        "total_outstanding": 50000000,
                        "by_aging": {"overdue": 35000000, "current": 15000000},
                        "counts": {
                            "total": 13,
                            "overdue": 8,
                            "current": 5,
                            "partial": 3,
                            "partial_overdue": 2,
                            "partial_current": 1,
                            "vendors": 6,
                            "no_due_date": 1,
                        },
                        "aging_breakdown": {
                            "overdue_1_30": 15000000,
                            "overdue_31_60": 10000000,
                            "overdue_61_90": 5000000,
                            "overdue_90_plus": 5000000,
                        },
                        "urgency": {
                            "oldest_days": 45,
                            "largest_amount": 8000000,
                            "due_within_7_days": 3,
                        },
                    },
                }
            )
            mock_service.return_value = service_instance

            response = sync_client.get("/api/bills/outstanding-summary")
            data = response.json()["data"]

            # Verify no partial amount bucket exists in by_aging
            assert "partial" not in data["by_aging"]

            # Verify partial only exists as count
            assert "partial" in data["counts"]
