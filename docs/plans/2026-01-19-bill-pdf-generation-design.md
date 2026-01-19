# Bill PDF Generation - Design Document

## Overview

Generate PDF faktur pembelian (purchase invoice) for bills. PDF can be previewed inline or downloaded via presigned URL.

## API Endpoint

### GET /api/bills/{bill_id}/pdf

**Query Parameters:**
- `format`: `url` (default) | `inline`

**Response (format=url):**
```json
{
  "success": true,
  "data": {
    "url": "https://storage.example.com/invoices/xxx.pdf?signature=...",
    "expires_at": "2026-01-19T11:00:00Z",
    "filename": "Faktur-PB-2601-0001.pdf"
  }
}
```

**Response (format=inline):**
- Content-Type: `application/pdf`
- Content-Disposition: `inline; filename="Faktur-PB-2601-0001.pdf"`
- Body: PDF bytes

## Architecture

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Router    │────▶│ PDFService  │────▶│   Storage   │
│  bills.py   │     │             │     │  (MinIO)    │
└─────────────┘     └──────┬──────┘     └─────────────┘
                          │
                    ┌─────▼─────┐
                    │  Jinja2   │
                    │ Templates │
                    └───────────┘
```

## Implementation

### 1. Dependencies

**requirements.txt additions:**
```
weasyprint==60.2
Jinja2>=3.0.0  # Already present
```

**Dockerfile additions:**
```dockerfile
# WeasyPrint system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libcairo2 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf2.0-0 \
    libffi-dev \
    shared-mime-info \
    && rm -rf /var/lib/apt/lists/*
```

### 2. PDF Service

**Location:** `app/services/pdf_service.py`

```python
"""
PDF Generation Service - WeasyPrint-based HTML to PDF conversion.
"""

import logging
from io import BytesIO
from pathlib import Path
from typing import Optional, Dict, Any
from datetime import datetime
from decimal import Decimal

from jinja2 import Environment, FileSystemLoader
from weasyprint import HTML, CSS

logger = logging.getLogger(__name__)

# Template directory
TEMPLATE_DIR = Path(__file__).parent.parent / "templates" / "pdf"


class PDFService:
    """Generate PDFs from HTML templates using WeasyPrint."""

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
        """Format amount as IDR currency (e.g., 1.500.000)."""
        try:
            value = float(amount) if amount else 0
            return f"{value:,.0f}".replace(",", ".")
        except (ValueError, TypeError):
            return "0"

    @staticmethod
    def format_date_indonesian(date_value: Any) -> str:
        """Format date to Indonesian locale (e.g., 19 Jan 2026)."""
        if not date_value:
            return "-"

        months = [
            "Jan", "Feb", "Mar", "Apr", "Mei", "Jun",
            "Jul", "Agu", "Sep", "Okt", "Nov", "Des"
        ]

        try:
            if isinstance(date_value, str):
                # Handle ISO format
                dt = datetime.fromisoformat(date_value.replace("Z", "+00:00"))
            elif isinstance(date_value, datetime):
                dt = date_value
            else:
                # Assume date object
                dt = datetime.combine(date_value, datetime.min.time())

            return f"{dt.day} {months[dt.month - 1]} {dt.year}"
        except Exception:
            return str(date_value)

    def generate_bill_pdf(self, bill: Dict[str, Any]) -> bytes:
        """
        Generate PDF for a bill.

        Args:
            bill: Bill data dict with items, vendor info, and totals

        Returns:
            PDF content as bytes
        """
        template = self.jinja_env.get_template("bill_invoice.html")

        # Translate status to Indonesian
        status_map = {
            "draft": "DRAFT",
            "posted": "TERBIT",
            "unpaid": "TERBIT",
            "partial": "SEBAGIAN",
            "paid": "LUNAS",
            "overdue": "JATUH TEMPO",
            "void": "BATAL",
        }

        html_content = template.render(
            bill=bill,
            status_label=status_map.get(bill.get("status"), bill.get("status", "-")),
        )

        # Load CSS
        css_path = TEMPLATE_DIR / "invoice.css"
        stylesheets = []
        if css_path.exists():
            stylesheets.append(CSS(filename=str(css_path)))

        # Generate PDF
        pdf_bytes = HTML(string=html_content).write_pdf(stylesheets=stylesheets)

        return pdf_bytes


# Singleton
_pdf_service: Optional[PDFService] = None


def get_pdf_service() -> PDFService:
    """Get or create PDF service singleton."""
    global _pdf_service
    if _pdf_service is None:
        _pdf_service = PDFService()
    return _pdf_service
```

### 3. Router Endpoint

**Add to:** `app/routers/bills.py`

```python
from fastapi.responses import StreamingResponse
from ..services.pdf_service import get_pdf_service
from ..services.storage_service import get_storage_service

@router.get("/{bill_id}/pdf")
async def get_bill_pdf(
    request: Request,
    bill_id: UUID,
    format: Literal["url", "inline"] = Query("url", description="Response format"),
):
    """
    Generate PDF for a bill.

    - format=url: Return presigned URL (for download/share)
    - format=inline: Return PDF bytes directly (for inline preview)
    """
    try:
        ctx = get_user_context(request)
        service = await get_bills_service()

        # Fetch bill with full details (use V2 for extended data)
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
                    "Cache-Control": "private, max-age=300",  # 5 min browser cache
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

### 4. HTML Template

**Location:** `app/templates/pdf/bill_invoice.html`

```html
<!DOCTYPE html>
<html lang="id">
<head>
    <meta charset="UTF-8">
    <title>Faktur {{ bill.invoice_number or '-' }}</title>
    <link rel="stylesheet" href="invoice.css">
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
                <div class="vendor-name">{{ bill.vendor.name if bill.vendor else '-' }}</div>
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
                        <span class="batch-info">Exp: {{ item.exp_date }}</span>
                        {% endif %}
                    </td>
                    <td class="col-qty">{{ item.qty }} {{ item.unit or '' }}</td>
                    <td class="col-price">{{ item.price | currency }}</td>
                    <td class="col-disc">{{ item.discount_percent | default(0) }}%</td>
                    <td class="col-total">{{ item.total | currency }}</td>
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
                    <td class="amount">Rp {{ bill.amount | currency }}</td>
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
                Dicetak: {{ now | date_id if now else '' }}
            </div>
        </div>
    </div>
</body>
</html>
```

### 5. CSS Stylesheet

**Location:** `app/templates/pdf/invoice.css`

```css
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
    font-family: 'Segoe UI', Arial, sans-serif;
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

## File Structure

```
backend/api_gateway/app/
├── routers/
│   └── bills.py              # Add PDF endpoint
├── services/
│   ├── pdf_service.py        # NEW: PDF generation
│   ├── storage_service.py    # Existing: presigned URLs
│   └── bills_service.py      # Existing: get_bill_v2
└── templates/
    └── pdf/                   # NEW: PDF templates
        ├── bill_invoice.html
        └── invoice.css
```

## P0 Scope (This Implementation)

- [x] Basic PDF generation with WeasyPrint
- [x] HTML/CSS template for invoice
- [x] Inline preview (`format=inline`)
- [x] Presigned URL (`format=url`)
- [x] Indonesian locale (dates, currency)
- [ ] Company header (skip - no tenant profile yet)
- [ ] Caching (skip - add in P1)

## P1 (Future)

- Add `tenant_profile` table with company info
- Redis caching for generated PDFs
- Custom templates per tenant
- Logo support in header

## Testing

```bash
# Inline preview
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/bills/{bill_id}/pdf?format=inline" \
  --output test.pdf

# Presigned URL
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/bills/{bill_id}/pdf?format=url"
```

## Frontend Integration

```typescript
// Preview in modal
const handlePreviewPdf = () => {
  window.open(`/api/bills/${billId}/pdf?format=inline`, '_blank');
};

// Download with presigned URL
const handleDownloadPdf = async () => {
  const res = await api.get(`/bills/${billId}/pdf?format=url`);
  const { url, filename } = res.data.data;

  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.click();
};
```
