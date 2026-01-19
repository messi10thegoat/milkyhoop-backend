# Bill PDF Generation - Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add PDF generation endpoint for bills (faktur pembelian) with inline preview and presigned URL download.

**Architecture:** PDFService uses WeasyPrint to render Jinja2 HTML templates into PDFs. Router endpoint fetches bill data, generates PDF, and either returns bytes directly (inline) or uploads to MinIO and returns presigned URL.

**Tech Stack:** WeasyPrint, Jinja2, FastAPI, MinIO (via existing StorageService)

---

## Task 1: Add WeasyPrint Dependencies

**Files:**
- Modify: `backend/api_gateway/requirements.txt`
- Modify: `backend/api_gateway/Dockerfile`

**Step 1: Add WeasyPrint to requirements.txt**

Add after line 69 (after Pillow):

```python
# PDF Generation
weasyprint==60.2
```

**Step 2: Add system dependencies to Dockerfile**

In the `prod` stage (line 40-43), update the apt-get install to include WeasyPrint dependencies:

```dockerfile
# Install runtime dependencies (including WeasyPrint)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    tini \
    libcairo2 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf2.0-0 \
    libffi-dev \
    shared-mime-info \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
```

**Step 3: Verify syntax**

Run: `python3 -c "import weasyprint; print('OK')"` (will fail until container rebuild - that's expected)

**Step 4: Commit**

```bash
git add backend/api_gateway/requirements.txt backend/api_gateway/Dockerfile
git commit -m "build: add WeasyPrint dependencies for PDF generation"
```

---

## Task 2: Create PDF Templates Directory and CSS

**Files:**
- Create: `backend/api_gateway/app/templates/pdf/invoice.css`

**Step 1: Create directory and CSS file**

```css
/* backend/api_gateway/app/templates/pdf/invoice.css */
@page {
    size: A4;
    margin: 1.5cm 2cm;
}

* {
    margin: 0;
    padding: 0;
    box-sizing: border-box;
}

body {
    font-family: 'DejaVu Sans', Arial, sans-serif;
    font-size: 9pt;
    color: #333;
    line-height: 1.4;
}

.invoice {
    max-width: 100%;
}

/* Header */
.header {
    display: flex;
    justify-content: space-between;
    align-items: flex-end;
    padding-bottom: 15px;
    margin-bottom: 20px;
    border-bottom: 2px solid #333;
}

.title {
    font-size: 18pt;
    font-weight: bold;
    letter-spacing: 1px;
}

.invoice-number {
    font-size: 11pt;
    color: #666;
}

/* Meta Section */
.meta-section {
    display: flex;
    justify-content: space-between;
    margin-bottom: 25px;
}

.vendor-info {
    flex: 1;
}

.vendor-name {
    font-size: 11pt;
    font-weight: bold;
    margin-top: 5px;
}

.vendor-address {
    font-size: 9pt;
    color: #666;
    margin-top: 3px;
}

.invoice-meta {
    text-align: right;
}

.invoice-meta table {
    margin-left: auto;
}

.invoice-meta td {
    padding: 2px 8px;
}

.label {
    color: #666;
    font-size: 8pt;
    text-transform: uppercase;
}

/* Status colors */
.status-draft { color: #666; }
.status-posted, .status-unpaid { color: #2563eb; }
.status-partial { color: #d97706; }
.status-paid { color: #16a34a; }
.status-overdue { color: #dc2626; }
.status-void { color: #6b7280; text-decoration: line-through; }

/* Items Table */
.items-table {
    width: 100%;
    border-collapse: collapse;
    margin-bottom: 20px;
}

.items-table th {
    background: #1f2937;
    color: white;
    padding: 8px 10px;
    text-align: left;
    font-size: 8pt;
    font-weight: 600;
    text-transform: uppercase;
}

.items-table td {
    padding: 8px 10px;
    border-bottom: 1px solid #e5e7eb;
    vertical-align: top;
}

.items-table tbody tr:nth-child(even) {
    background: #f9fafb;
}

.col-no { width: 5%; text-align: center; }
.col-item { width: 40%; }
.col-qty { width: 12%; text-align: right; }
.col-price { width: 15%; text-align: right; }
.col-disc { width: 8%; text-align: right; }
.col-total { width: 20%; text-align: right; }

.text-right { text-align: right; }

.batch-info {
    font-size: 8pt;
    color: #6b7280;
}

/* Totals */
.totals-section {
    display: flex;
    justify-content: flex-end;
    margin-bottom: 20px;
}

.totals-table {
    width: 250px;
}

.totals-table td {
    padding: 5px 10px;
}

.totals-table .label {
    text-align: right;
    font-size: 9pt;
}

.totals-table .amount {
    text-align: right;
    font-weight: 500;
}

.grand-total {
    background: #fef3c7;
    font-size: 11pt;
}

.grand-total td {
    padding: 8px 10px;
    font-weight: bold;
}

.balance-due {
    background: #fee2e2;
}

/* Notes */
.notes-section {
    margin-top: 20px;
    padding: 12px;
    background: #f3f4f6;
    border-radius: 4px;
}

.notes-content {
    margin-top: 5px;
    font-size: 9pt;
}

/* Footer */
.footer {
    margin-top: 30px;
    padding-top: 10px;
    border-top: 1px solid #e5e7eb;
    font-size: 8pt;
    color: #9ca3af;
}
```

**Step 2: Commit**

```bash
git add backend/api_gateway/app/templates/pdf/invoice.css
git commit -m "feat(pdf): add invoice CSS stylesheet"
```

---

## Task 3: Create HTML Template

**Files:**
- Create: `backend/api_gateway/app/templates/pdf/bill_invoice.html`

**Step 1: Create HTML template**

```html
<!DOCTYPE html>
<html lang="id">
<head>
    <meta charset="UTF-8">
    <title>Faktur {{ bill.invoice_number or '-' }}</title>
</head>
<body>
    <div class="invoice">
        <!-- Header -->
        <div class="header">
            <div class="title">FAKTUR PEMBELIAN</div>
            <div class="invoice-number">{{ bill.invoice_number or '-' }}</div>
        </div>

        <!-- Meta Info -->
        <div class="meta-section">
            <div class="vendor-info">
                <div class="label">Dari</div>
                <div class="vendor-name">{{ bill.vendor.name if bill.vendor else (bill.vendor_name or '-') }}</div>
                {% if bill.vendor and bill.vendor.address %}
                <div class="vendor-address">{{ bill.vendor.address }}</div>
                {% endif %}
            </div>

            <div class="invoice-meta">
                <table>
                    <tr>
                        <td class="label">Tanggal:</td>
                        <td>{{ bill.issue_date | date_id }}</td>
                    </tr>
                    <tr>
                        <td class="label">Jatuh Tempo:</td>
                        <td>{{ bill.due_date | date_id }}</td>
                    </tr>
                    {% if bill.ref_no %}
                    <tr>
                        <td class="label">Ref:</td>
                        <td>{{ bill.ref_no }}</td>
                    </tr>
                    {% endif %}
                    <tr>
                        <td class="label">Status:</td>
                        <td class="status-{{ bill.status }}">{{ status_label }}</td>
                    </tr>
                </table>
            </div>
        </div>

        <!-- Items Table -->
        <table class="items-table">
            <thead>
                <tr>
                    <th class="col-no">#</th>
                    <th class="col-item">Item</th>
                    <th class="col-qty">Qty</th>
                    <th class="col-price">Harga</th>
                    <th class="col-disc">Disk</th>
                    <th class="col-total">Jumlah</th>
                </tr>
            </thead>
            <tbody>
                {% for item in bill.items %}
                <tr>
                    <td class="col-no">{{ loop.index }}</td>
                    <td class="col-item">
                        {{ item.product_name or item.description or '-' }}
                        {% if item.batch_no %}
                        <br><span class="batch-info">Batch: {{ item.batch_no }}</span>
                        {% endif %}
                        {% if item.exp_date %}
                        <span class="batch-info"> Exp: {{ item.exp_date }}</span>
                        {% endif %}
                    </td>
                    <td class="col-qty text-right">{{ item.qty or item.quantity }} {{ item.unit or '' }}</td>
                    <td class="col-price text-right">{{ (item.price or item.unit_price) | currency }}</td>
                    <td class="col-disc text-right">{{ item.discount_percent | default(0) }}%</td>
                    <td class="col-total text-right">{{ (item.total or item.subtotal) | currency }}</td>
                </tr>
                {% endfor %}
            </tbody>
        </table>

        <!-- Totals -->
        <div class="totals-section">
            <table class="totals-table">
                <tr>
                    <td class="label">Subtotal</td>
                    <td class="amount">Rp {{ bill.subtotal | currency }}</td>
                </tr>
                {% if bill.item_discount_total and bill.item_discount_total > 0 %}
                <tr>
                    <td class="label">Diskon Item</td>
                    <td class="amount">-Rp {{ bill.item_discount_total | currency }}</td>
                </tr>
                {% endif %}
                {% if bill.invoice_discount_total and bill.invoice_discount_total > 0 %}
                <tr>
                    <td class="label">Diskon Faktur</td>
                    <td class="amount">-Rp {{ bill.invoice_discount_total | currency }}</td>
                </tr>
                {% endif %}
                {% if bill.cash_discount_total and bill.cash_discount_total > 0 %}
                <tr>
                    <td class="label">Diskon Tunai</td>
                    <td class="amount">-Rp {{ bill.cash_discount_total | currency }}</td>
                </tr>
                {% endif %}
                {% if bill.tax_rate and bill.tax_rate > 0 %}
                <tr>
                    <td class="label">DPP</td>
                    <td class="amount">Rp {{ bill.dpp | currency }}</td>
                </tr>
                <tr>
                    <td class="label">PPN ({{ bill.tax_rate }}%)</td>
                    <td class="amount">Rp {{ bill.tax_amount | currency }}</td>
                </tr>
                {% endif %}
                <tr class="grand-total">
                    <td class="label">Total</td>
                    <td class="amount">Rp {{ (bill.amount or bill.grand_total) | currency }}</td>
                </tr>
                {% if bill.amount_paid and bill.amount_paid > 0 %}
                <tr>
                    <td class="label">Dibayar</td>
                    <td class="amount">Rp {{ bill.amount_paid | currency }}</td>
                </tr>
                <tr class="balance-due">
                    <td class="label">Sisa</td>
                    <td class="amount">Rp {{ bill.amount_due | currency }}</td>
                </tr>
                {% endif %}
            </table>
        </div>

        {% if bill.notes %}
        <div class="notes-section">
            <div class="label">Catatan</div>
            <div class="notes-content">{{ bill.notes }}</div>
        </div>
        {% endif %}

        <!-- Footer -->
        <div class="footer">
            <div class="generated-at">
                Dicetak: {{ generated_at | date_id }}
            </div>
        </div>
    </div>
</body>
</html>
```

**Step 2: Commit**

```bash
git add backend/api_gateway/app/templates/pdf/bill_invoice.html
git commit -m "feat(pdf): add bill invoice HTML template"
```

---

## Task 4: Create PDF Service with Tests

**Files:**
- Create: `backend/api_gateway/app/services/pdf_service.py`
- Create: `backend/api_gateway/tests/test_pdf_service.py`

**Step 1: Write the failing test**

```python
# backend/api_gateway/tests/test_pdf_service.py
"""
Tests for PDF generation service.
"""
import pytest
from datetime import date, datetime


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
```

**Step 2: Run test to verify it fails**

Run: `cd /root/milkyhoop-dev/backend/api_gateway && python -m pytest tests/test_pdf_service.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.pdf_service'`

**Step 3: Write the PDF service implementation**

```python
# backend/api_gateway/app/services/pdf_service.py
"""
PDF Generation Service - WeasyPrint-based HTML to PDF conversion.
"""

import logging
from pathlib import Path
from typing import Optional, Dict, Any
from datetime import datetime, date

from jinja2 import Environment, FileSystemLoader
from weasyprint import HTML, CSS

logger = logging.getLogger(__name__)

# Template directory (relative to this file)
TEMPLATE_DIR = Path(__file__).parent.parent / "templates" / "pdf"


class PDFService:
    """Generate PDFs from HTML templates using WeasyPrint."""

    # Indonesian month names
    MONTHS_ID = [
        "Jan", "Feb", "Mar", "Apr", "Mei", "Jun",
        "Jul", "Agu", "Sep", "Okt", "Nov", "Des"
    ]

    # Status translations
    STATUS_LABELS = {
        "draft": "DRAFT",
        "posted": "TERBIT",
        "unpaid": "TERBIT",
        "partial": "SEBAGIAN",
        "paid": "LUNAS",
        "overdue": "JATUH TEMPO",
        "void": "BATAL",
    }

    def __init__(self):
        self.jinja_env = Environment(
            loader=FileSystemLoader(str(TEMPLATE_DIR)),
            autoescape=True,
        )
        # Register custom filters
        self.jinja_env.filters["currency"] = self.format_currency
        self.jinja_env.filters["date_id"] = self.format_date_indonesian

    @staticmethod
    def format_currency(amount: Any) -> str:
        """
        Format amount as IDR currency (e.g., 1.500.000).

        Args:
            amount: Number to format (int, float, Decimal, or None)

        Returns:
            Formatted string with Indonesian thousand separator
        """
        try:
            value = float(amount) if amount else 0
            return f"{value:,.0f}".replace(",", ".")
        except (ValueError, TypeError):
            return "0"

    @classmethod
    def format_date_indonesian(cls, date_value: Any) -> str:
        """
        Format date to Indonesian locale (e.g., 19 Jan 2026).

        Args:
            date_value: Date as string, date, or datetime object

        Returns:
            Formatted date string or "-" if None
        """
        if not date_value:
            return "-"

        try:
            if isinstance(date_value, str):
                # Handle ISO format (YYYY-MM-DD or with time)
                date_str = date_value.split("T")[0]
                dt = datetime.strptime(date_str, "%Y-%m-%d")
            elif isinstance(date_value, datetime):
                dt = date_value
            elif isinstance(date_value, date):
                dt = datetime.combine(date_value, datetime.min.time())
            else:
                return str(date_value)

            return f"{dt.day} {cls.MONTHS_ID[dt.month - 1]} {dt.year}"
        except Exception as e:
            logger.warning(f"Failed to format date {date_value}: {e}")
            return str(date_value)

    def generate_bill_pdf(self, bill: Dict[str, Any]) -> bytes:
        """
        Generate PDF for a bill.

        Args:
            bill: Bill data dict with items, vendor info, and totals.
                  Expected fields: invoice_number, vendor/vendor_name,
                  issue_date, due_date, status, items, subtotal, amount, etc.

        Returns:
            PDF content as bytes
        """
        template = self.jinja_env.get_template("bill_invoice.html")

        # Get status label
        status = bill.get("status", "draft")
        status_label = self.STATUS_LABELS.get(status, status.upper())

        # Render HTML
        html_content = template.render(
            bill=bill,
            status_label=status_label,
            generated_at=datetime.now(),
        )

        # Load CSS
        css_path = TEMPLATE_DIR / "invoice.css"
        stylesheets = []
        if css_path.exists():
            stylesheets.append(CSS(filename=str(css_path)))

        # Generate PDF
        pdf_bytes = HTML(string=html_content).write_pdf(stylesheets=stylesheets)

        return pdf_bytes


# Singleton instance
_pdf_service: Optional[PDFService] = None


def get_pdf_service() -> PDFService:
    """Get or create PDF service singleton."""
    global _pdf_service
    if _pdf_service is None:
        _pdf_service = PDFService()
    return _pdf_service
```

**Step 4: Run tests to verify they pass**

Run: `cd /root/milkyhoop-dev/backend/api_gateway && python -m pytest tests/test_pdf_service.py -v`

Expected: All tests PASS

**Step 5: Commit**

```bash
git add backend/api_gateway/app/services/pdf_service.py backend/api_gateway/tests/test_pdf_service.py
git commit -m "feat(pdf): add PDF generation service with tests"
```

---

## Task 5: Add PDF Endpoint to Bills Router

**Files:**
- Modify: `backend/api_gateway/app/routers/bills.py`
- Create: `backend/api_gateway/tests/test_bills_pdf.py`

**Step 1: Write the failing test**

```python
# backend/api_gateway/tests/test_bills_pdf.py
"""
Tests for bills PDF endpoint.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient
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

            response = sync_client.get(
                f"/api/bills/{bill_id}/pdf?format=inline"
            )

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
```

**Step 2: Run test to verify it fails**

Run: `cd /root/milkyhoop-dev/backend/api_gateway && python -m pytest tests/test_bills_pdf.py -v`

Expected: FAIL (endpoint doesn't exist yet)

**Step 3: Add PDF endpoint to bills router**

Add these imports at the top of `backend/api_gateway/app/routers/bills.py` (after existing imports):

```python
from io import BytesIO
from fastapi.responses import StreamingResponse
from ..services.pdf_service import get_pdf_service
from ..services.storage_service import get_storage_service
```

Add this endpoint after the `get_bill_history` endpoint (around line 483), before the CREATE BILL section:

```python
# =============================================================================
# GET BILL PDF
# =============================================================================
@router.get("/{bill_id}/pdf")
async def get_bill_pdf(
    request: Request,
    bill_id: UUID,
    format: Literal["url", "inline"] = Query(
        "url",
        description="Response format: 'url' returns presigned URL, 'inline' returns PDF bytes"
    ),
):
    """
    Generate PDF for a bill (faktur pembelian).

    **Format options:**
    - `url` (default): Returns presigned URL for download/share (expires in 1 hour)
    - `inline`: Returns PDF bytes directly for browser preview

    **Usage:**
    - For download button: use `?format=url` and redirect to returned URL
    - For inline preview: use `?format=inline` and embed in iframe/viewer
    """
    try:
        ctx = get_user_context(request)
        service = await get_bills_service()

        # Fetch bill with full details
        bill = await service.get_bill_v2(
            tenant_id=ctx["tenant_id"],
            bill_id=bill_id
        )

        if not bill:
            raise HTTPException(status_code=404, detail="Bill not found")

        # Generate PDF
        pdf_service = get_pdf_service()
        pdf_bytes = pdf_service.generate_bill_pdf(bill)

        # Generate filename
        invoice_num = bill.get("invoice_number") or str(bill_id)[:8]
        filename = f"Faktur-{invoice_num}.pdf"

        if format == "inline":
            return StreamingResponse(
                BytesIO(pdf_bytes),
                media_type="application/pdf",
                headers={
                    "Content-Disposition": f'inline; filename="{filename}"',
                    "Cache-Control": "private, max-age=300",
                }
            )

        # Upload to storage and return presigned URL
        storage = get_storage_service()
        file_path = f"{ctx['tenant_id']}/invoices/{bill_id}.pdf"

        url = await storage.upload_bytes(
            content=pdf_bytes,
            file_path=file_path,
            content_type="application/pdf",
            metadata={"bill_id": str(bill_id), "invoice_number": invoice_num},
        )

        # Calculate expiry
        from datetime import timedelta
        expires_at = datetime.utcnow() + timedelta(seconds=storage.config.url_expiry)

        return {
            "success": True,
            "data": {
                "url": url,
                "expires_at": expires_at.isoformat() + "Z",
                "filename": filename,
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating PDF for bill {bill_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate PDF")
```

Also add `datetime` import at the top if not present:

```python
from datetime import date, datetime, timedelta
```

**Step 4: Run tests to verify they pass**

Run: `cd /root/milkyhoop-dev/backend/api_gateway && python -m pytest tests/test_bills_pdf.py -v`

Expected: All tests PASS

**Step 5: Commit**

```bash
git add backend/api_gateway/app/routers/bills.py backend/api_gateway/tests/test_bills_pdf.py
git commit -m "feat(bills): add PDF generation endpoint GET /{bill_id}/pdf"
```

---

## Task 6: Integration Test and Manual Verification

**Files:**
- No new files

**Step 1: Run all bills-related tests**

Run: `cd /root/milkyhoop-dev/backend/api_gateway && python -m pytest tests/test_pdf_service.py tests/test_bills_pdf.py -v`

Expected: All tests PASS

**Step 2: Rebuild Docker image (if testing locally)**

Run: `docker-compose build api_gateway`

Expected: Build succeeds with WeasyPrint dependencies

**Step 3: Manual test with curl (after container is running)**

```bash
# Get a valid bill ID first
BILL_ID="your-bill-id-here"
TOKEN="your-auth-token"

# Test inline format
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/bills/$BILL_ID/pdf?format=inline" \
  --output test-invoice.pdf

# Test URL format
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/bills/$BILL_ID/pdf?format=url"
```

**Step 4: Final commit with any fixes**

```bash
git add -A
git commit -m "test(bills): verify PDF generation integration"
```

---

## Summary

| Task | Description | Files |
|------|-------------|-------|
| 1 | Add WeasyPrint dependencies | requirements.txt, Dockerfile |
| 2 | Create CSS stylesheet | templates/pdf/invoice.css |
| 3 | Create HTML template | templates/pdf/bill_invoice.html |
| 4 | Create PDF service + tests | services/pdf_service.py, test_pdf_service.py |
| 5 | Add router endpoint + tests | routers/bills.py, test_bills_pdf.py |
| 6 | Integration test | Manual verification |

**Total: 6 tasks, ~5-6 commits**
