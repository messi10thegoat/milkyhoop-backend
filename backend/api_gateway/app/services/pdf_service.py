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
        self.jinja_env.filters["date_short"] = self.format_date_short

    @classmethod
    def format_date_short(cls, date_value: Any) -> str:
        """Format date to short form (e.g., Mar 2026 or 19 Mar 2026)."""
        if not date_value:
            return "-"
        try:
            from datetime import date, datetime

            if isinstance(date_value, str):
                # Handle YYYY-MM format
                if len(date_value) == 7 and "-" in date_value:
                    parts = date_value.split("-")
                    months = [
                        "Jan",
                        "Feb",
                        "Mar",
                        "Apr",
                        "Mei",
                        "Jun",
                        "Jul",
                        "Agt",
                        "Sep",
                        "Okt",
                        "Nov",
                        "Des",
                    ]
                    return f"{months[int(parts[1])-1]} {parts[0]}"
                date_value = datetime.fromisoformat(date_value.replace("Z", "+00:00"))
            if isinstance(date_value, (date, datetime)):
                months = [
                    "Jan",
                    "Feb",
                    "Mar",
                    "Apr",
                    "Mei",
                    "Jun",
                    "Jul",
                    "Agt",
                    "Sep",
                    "Okt",
                    "Nov",
                    "Des",
                ]
                return (
                    f"{date_value.day} {months[date_value.month-1]} {date_value.year}"
                )
            return str(date_value)
        except Exception:
            return str(date_value) if date_value else "-"

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

    def generate_quote_pdf(self, quote_data, tenant_info):
        """
        Generate PDF for a quote (penawaran harga).

        Args:
            quote_data: Quote data dict with items and totals.
            tenant_info: Tenant info dict with name, address, phone, email, logo_data.

        Returns:
            PDF content as bytes
        """
        template = self.jinja_env.get_template("quote.html")

        # Get status label
        status = quote_data.get("status", "draft")
        status_label = self.STATUS_LABELS.get(status, status.upper())

        # Build company context matching template variable name
        company = {
            "name": tenant_info.get("name"),
            "address": tenant_info.get("address"),
            "phone": tenant_info.get("phone"),
            "email": tenant_info.get("email"),
            "logo_base64": tenant_info.get("logo_data"),
        }

        # Render HTML
        html_content = template.render(
            quote=quote_data,
            company=company,
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

    def generate_income_statement_pdf(
        self, data: dict, company_name: str, basis: str = "Akrual"
    ) -> bytes:
        """Generate PDF for Income Statement (Laba Rugi) from PSAK engine output."""
        template = self.jinja_env.get_template("laba_rugi.html")

        def _section(section: dict, label: str) -> dict:
            return {
                "label": label,
                "items": [
                    {
                        "accountCode": a["account_code"],
                        "accountName": a["account_name"],
                        "amount": a["balance"],
                    }
                    for a in section.get("akun", [])
                ],
                "total": section.get("total", 0),
            }

        context = {
            "company_name": company_name,
            "period_start": data["period"]["start"],
            "period_end": data["period"]["end"],
            "basis": basis,
            "revenue": _section(data["pendapatan"], "Pendapatan"),
            "cost_of_goods_sold": _section(data["hpp"], "Harga Pokok Penjualan"),
            "gross_profit": data["laba_kotor"],
            "operating_expenses": _section(data["beban_usaha"], "Beban Usaha"),
            "operating_income": data["laba_usaha"],
            "other_income": _section(data["pendapatan_lain"], "Pendapatan Lain"),
            "other_expenses": _section(data["beban_lain"], "Beban Lain"),
            "income_before_tax": data["laba_sebelum_pajak"],
            "tax_expense": data.get("beban_pajak", {}).get("total", 0),
            "net_income": data["laba_bersih"],
            "generated_at": datetime.now(),
        }

        html_content = template.render(**context)
        css_path = TEMPLATE_DIR / "report.css"
        stylesheets = [CSS(filename=str(css_path))] if css_path.exists() else []
        return HTML(string=html_content, base_url=str(TEMPLATE_DIR)).write_pdf(
            stylesheets=stylesheets
        )

    def generate_balance_sheet_pdf(
        self, data: dict, company_name: str, basis: str = "Akrual"
    ) -> bytes:
        """Generate PDF for Balance Sheet (Neraca) from PSAK engine output."""
        template = self.jinja_env.get_template("neraca.html")

        def _accounts(akun_list: list) -> list:
            return [
                {
                    "code": a["account_code"],
                    "name": a["account_name"],
                    "balance": a["balance"],
                }
                for a in akun_list
            ]

        # Build asset categories
        al = data.get("aset_lancar", {})
        current_accounts = []
        for sub_key in ["kas_setara_kas", "piutang_usaha", "persediaan", "lainnya"]:
            sub = al.get(sub_key, {})
            current_accounts.extend(_accounts(sub.get("akun", [])))

        asset_categories = [
            {
                "name": "Aset Lancar",
                "accounts": current_accounts,
                "total": al.get("total", 0),
            }
        ]

        atl = data.get("aset_tidak_lancar", {})
        fa_accounts = _accounts(atl.get("aset_tetap", {}).get("akun", []))
        if fa_accounts or atl.get("total", 0) != 0:
            asset_categories.append(
                {
                    "name": "Aset Tidak Lancar",
                    "accounts": fa_accounts,
                    "total": atl.get("total", 0),
                }
            )

        # Build liability categories (PSAK: Jangka Pendek + Jangka Panjang)
        liab = data.get("liabilitas", {})
        liability_categories = []
        jp = liab.get("jangka_pendek", {})
        jp_accounts = []
        ut = jp.get("utang_usaha", {})
        if ut.get("akun"):
            jp_accounts.extend(_accounts(ut["akun"]))
        jp_lain = jp.get("lainnya", {})
        if jp_lain.get("akun"):
            jp_accounts.extend(_accounts(jp_lain["akun"]))
        if jp_accounts or jp.get("total", 0) != 0:
            liability_categories.append(
                {
                    "name": "Liabilitas Jangka Pendek",
                    "accounts": jp_accounts,
                    "total": jp.get("total", 0),
                }
            )
        jpp = liab.get("jangka_panjang", {})
        jpp_accounts = _accounts(jpp.get("akun", []))
        if jpp_accounts or jpp.get("total", 0) != 0:
            liability_categories.append(
                {
                    "name": "Liabilitas Jangka Panjang",
                    "accounts": jpp_accounts,
                    "total": jpp.get("total", 0),
                }
            )
        if not liability_categories:
            liability_categories.append(
                {"name": "Liabilitas", "accounts": [], "total": 0}
            )

        # Build equity categories
        ek = data.get("ekuitas", {})
        equity_accounts = []
        for sub_key in ["modal_disetor", "saldo_laba", "lainnya"]:
            sub = ek.get(sub_key, {})
            equity_accounts.extend(_accounts(sub.get("akun", [])))
        laba_periode = ek.get("laba_periode", 0)
        if laba_periode:
            equity_accounts.append(
                {"code": "", "name": "Laba Periode Berjalan", "balance": laba_periode}
            )
        equity_categories = [
            {
                "name": "Modal & Saldo Laba",
                "accounts": equity_accounts,
                "total": ek.get("total", 0),
            }
        ]

        context = {
            "company_name": company_name,
            "as_of_date": data.get("as_of", ""),
            "basis": basis,
            "assets": {"categories": asset_categories},
            "total_assets": data.get("total_aset", 0),
            "liabilities": {
                "categories": liability_categories,
                "total": liab.get("total", 0),
            },
            "equity": {"categories": equity_categories, "total": ek.get("total", 0)},
            "total_liabilities_and_equity": data.get("total_liabilitas_ekuitas", 0),
            "generated_at": datetime.now(),
        }

        html_content = template.render(**context)
        css_path = TEMPLATE_DIR / "report.css"
        stylesheets = [CSS(filename=str(css_path))] if css_path.exists() else []
        return HTML(string=html_content, base_url=str(TEMPLATE_DIR)).write_pdf(
            stylesheets=stylesheets
        )

    def generate_cash_flow_pdf(
        self, data: dict, company_name: str, basis: str = "Akrual"
    ) -> bytes:
        """Generate PDF for Cash Flow Statement (Arus Kas) from PSAK engine output."""
        template = self.jinja_env.get_template("arus_kas.html")

        context = {
            "company_name": company_name,
            "period_start": data["period"]["start"],
            "period_end": data["period"]["end"],
            "basis": basis,
            "opening_balance": data["kas_awal"],
            "operating": {
                "label": "Aktivitas Operasi",
                "items": data["operasi"]["items"],
                "total": data["operasi"]["total"],
            },
            "investing": {
                "label": "Aktivitas Investasi",
                "items": data["investasi"]["items"],
                "total": data["investasi"]["total"],
            },
            "financing": {
                "label": "Aktivitas Pendanaan",
                "items": data["pendanaan"]["items"],
                "total": data["pendanaan"]["total"],
            },
            "net_cash_change": data["kenaikan_kas_bersih"],
            "closing_balance": data["kas_akhir"],
            "generated_at": datetime.now(),
        }

        html_content = template.render(**context)
        css_path = TEMPLATE_DIR / "report.css"
        stylesheets = [CSS(filename=str(css_path))] if css_path.exists() else []
        return HTML(string=html_content, base_url=str(TEMPLATE_DIR)).write_pdf(
            stylesheets=stylesheets
        )

    def generate_delivery_note_pdf(self, delivery: dict) -> bytes:
        """Generate PDF for a Surat Jalan (Delivery Note)."""
        template = self.jinja_env.get_template("delivery_note.html")

        html_content = template.render(
            delivery=delivery,
            delivery_items=delivery.get("items", []),
            generated_at=datetime.now(),
        )

        css_path = TEMPLATE_DIR / "invoice.css"
        stylesheets = []
        if css_path.exists():
            stylesheets.append(CSS(filename=str(css_path)))

        return HTML(string=html_content).write_pdf(stylesheets=stylesheets)

    def generate_receipt_pdf(self, receipt_data, tenant_info):
        """
        Generate PDF for a receipt (Bukti Penerimaan / Kwitansi).

        Args:
            receipt_data: Receipt data dict (receipt_number, receipt_date,
                payer_name, amount, amount_words, method, bank_name,
                purpose_label, purpose_ref, remaining, notes).
            tenant_info: Tenant info dict with name, address, phone, email, logo_data.

        Returns:
            PDF content as bytes
        """
        template = self.jinja_env.get_template("kwitansi.html")

        # Build company context matching template variable name
        company = {
            "name": tenant_info.get("name"),
            "address": tenant_info.get("address"),
            "phone": tenant_info.get("phone"),
            "email": tenant_info.get("email"),
            "logo_base64": tenant_info.get("logo_data"),
        }

        # Render HTML
        html_content = template.render(
            receipt=receipt_data,
            company=company,
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
