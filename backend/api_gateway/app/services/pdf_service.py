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
        "Jan",
        "Feb",
        "Mar",
        "Apr",
        "Mei",
        "Jun",
        "Jul",
        "Agu",
        "Sep",
        "Okt",
        "Nov",
        "Des",
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
        Generate PDF for a bill (purchase invoice).

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

    def generate_sales_invoice_pdf(self, invoice: Dict[str, Any]) -> bytes:
        """
        Generate PDF for a sales invoice.

        Args:
            invoice: Invoice data dict with items, customer info, and totals.
                     Expected fields: invoice_number, customer_name,
                     invoice_date, due_date, status, items, subtotal, total_amount, etc.

        Returns:
            PDF content as bytes
        """
        template = self.jinja_env.get_template("sales_invoice.html")

        # Get status label
        status = invoice.get("status", "draft")
        status_label = self.STATUS_LABELS.get(status, status.upper())

        # Render HTML
        html_content = template.render(
            invoice=invoice,
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
