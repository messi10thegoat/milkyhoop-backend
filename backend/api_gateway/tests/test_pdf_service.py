# backend/api_gateway/tests/test_pdf_service.py
"""
Tests for PDF generation service.
"""
import pytest
from datetime import date


class TestPDFServiceFormatters:
    """Test formatting helper functions."""

    def test_format_currency_integer(self):
        """Format integer amount to IDR format."""
        from app.services.pdf_service import PDFService

        service = PDFService()
        result = service.format_currency(1500000)
        assert result == "1.500.000"

    def test_format_currency_zero(self):
        """Format zero amount."""
        from app.services.pdf_service import PDFService

        service = PDFService()
        result = service.format_currency(0)
        assert result == "0"

    def test_format_currency_none(self):
        """Handle None gracefully."""
        from app.services.pdf_service import PDFService

        service = PDFService()
        result = service.format_currency(None)
        assert result == "0"

    def test_format_date_indonesian_date_object(self):
        """Format date object to Indonesian locale."""
        from app.services.pdf_service import PDFService

        service = PDFService()
        result = service.format_date_indonesian(date(2026, 1, 19))
        assert result == "19 Jan 2026"

    def test_format_date_indonesian_iso_string(self):
        """Format ISO date string."""
        from app.services.pdf_service import PDFService

        service = PDFService()
        result = service.format_date_indonesian("2026-05-15")
        assert result == "15 Mei 2026"

    def test_format_date_indonesian_none(self):
        """Handle None date gracefully."""
        from app.services.pdf_service import PDFService

        service = PDFService()
        result = service.format_date_indonesian(None)
        assert result == "-"


class TestPDFServiceGeneration:
    """Test PDF generation."""

    @pytest.fixture
    def sample_bill(self):
        """Sample bill data for testing."""
        return {
            "id": "123e4567-e89b-12d3-a456-426614174000",
            "invoice_number": "PB-2601-0001",
            "vendor": {"name": "PT Supplier Test", "address": "Jl. Test No. 1"},
            "issue_date": "2026-01-19",
            "due_date": "2026-02-19",
            "status": "posted",
            "items": [
                {
                    "product_name": "Paracetamol 500mg",
                    "qty": 100,
                    "unit": "Tab",
                    "price": 500,
                    "discount_percent": 5,
                    "total": 47500,
                    "batch_no": "BTH001",
                    "exp_date": "2027-12",
                }
            ],
            "subtotal": 50000,
            "item_discount_total": 2500,
            "invoice_discount_total": 0,
            "cash_discount_total": 0,
            "tax_rate": 11,
            "dpp": 47500,
            "tax_amount": 5225,
            "amount": 52725,
            "amount_paid": 0,
            "amount_due": 52725,
            "notes": "Test notes",
        }

    def test_generate_bill_pdf_returns_bytes(self, sample_bill):
        """PDF generation returns bytes."""
        from app.services.pdf_service import PDFService

        service = PDFService()
        result = service.generate_bill_pdf(sample_bill)

        assert isinstance(result, bytes)
        assert len(result) > 0

    def test_generate_bill_pdf_valid_pdf_header(self, sample_bill):
        """Generated PDF has valid PDF header."""
        from app.services.pdf_service import PDFService

        service = PDFService()
        result = service.generate_bill_pdf(sample_bill)

        # PDF files start with %PDF-
        assert result[:5] == b"%PDF-"

    def test_generate_bill_pdf_minimal_data(self):
        """PDF generation works with minimal required data."""
        from app.services.pdf_service import PDFService

        minimal_bill = {
            "invoice_number": "TEST-001",
            "vendor_name": "Test Vendor",
            "issue_date": "2026-01-19",
            "due_date": "2026-02-19",
            "status": "draft",
            "items": [
                {"product_name": "Item 1", "qty": 1, "price": 1000, "total": 1000}
            ],
            "subtotal": 1000,
            "amount": 1000,
        }

        service = PDFService()
        result = service.generate_bill_pdf(minimal_bill)

        assert isinstance(result, bytes)
        assert result[:5] == b"%PDF-"
