# backend/api_gateway/tests/test_bills_pdf.py
"""
Tests for bills PDF endpoint.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4


class TestBillsPDFEndpoint:
    """Test GET /api/bills/{bill_id}/pdf endpoint."""

    @pytest.fixture
    def mock_bill_data(self):
        """Sample bill data returned by service."""
        return {
            "id": str(uuid4()),
            "invoice_number": "PB-2601-0001",
            "vendor": {"name": "PT Supplier"},
            "issue_date": "2026-01-19",
            "due_date": "2026-02-19",
            "status": "posted",
            "items": [
                {"product_name": "Item 1", "qty": 10, "price": 1000, "total": 10000}
            ],
            "subtotal": 10000,
            "amount": 10000,
        }

    @pytest.fixture
    def mock_user_context(self):
        """Mock authenticated user context."""
        return {
            "tenant_id": "test-tenant",
            "user_id": str(uuid4()),
        }

    def test_pdf_inline_returns_pdf_content_type(
        self, sync_client, mock_bill_data, mock_user_context
    ):
        """Inline format returns application/pdf content type."""
        bill_id = uuid4()

        with patch(
            "app.routers.bills.get_user_context", return_value=mock_user_context
        ), patch("app.routers.bills.get_bills_service") as mock_service:
            # Setup mock
            service_instance = AsyncMock()
            service_instance.get_bill_v2 = AsyncMock(return_value=mock_bill_data)
            mock_service.return_value = service_instance

            response = sync_client.get(f"/api/bills/{bill_id}/pdf?format=inline")

            assert response.status_code == 200
            assert response.headers["content-type"] == "application/pdf"
            assert "inline" in response.headers.get("content-disposition", "")

    def test_pdf_url_returns_json_with_presigned_url(
        self, sync_client, mock_bill_data, mock_user_context
    ):
        """URL format returns JSON with presigned URL."""
        bill_id = uuid4()

        with patch(
            "app.routers.bills.get_user_context", return_value=mock_user_context
        ), patch("app.routers.bills.get_bills_service") as mock_service, patch(
            "app.routers.bills.get_storage_service"
        ) as mock_storage:
            # Setup service mock
            service_instance = AsyncMock()
            service_instance.get_bill_v2 = AsyncMock(return_value=mock_bill_data)
            mock_service.return_value = service_instance

            # Setup storage mock
            storage_instance = MagicMock()
            storage_instance.upload_bytes = AsyncMock(
                return_value="https://storage.example.com/test.pdf?sig=abc"
            )
            storage_instance.config.url_expiry = 3600
            mock_storage.return_value = storage_instance

            response = sync_client.get(f"/api/bills/{bill_id}/pdf?format=url")

            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert "url" in data["data"]
            assert "expires_at" in data["data"]
            assert "filename" in data["data"]

    def test_pdf_bill_not_found_returns_404(self, sync_client, mock_user_context):
        """Return 404 when bill not found."""
        bill_id = uuid4()

        with patch(
            "app.routers.bills.get_user_context", return_value=mock_user_context
        ), patch("app.routers.bills.get_bills_service") as mock_service:
            service_instance = AsyncMock()
            service_instance.get_bill_v2 = AsyncMock(return_value=None)
            mock_service.return_value = service_instance

            response = sync_client.get(f"/api/bills/{bill_id}/pdf")

            assert response.status_code == 404

    def test_pdf_unauthenticated_returns_401(self, sync_client):
        """Return 401 when not authenticated."""
        from fastapi import HTTPException

        bill_id = uuid4()

        with patch(
            "app.routers.bills.get_user_context",
            side_effect=HTTPException(status_code=401, detail="Auth required"),
        ):
            response = sync_client.get(f"/api/bills/{bill_id}/pdf")

            assert response.status_code == 401
