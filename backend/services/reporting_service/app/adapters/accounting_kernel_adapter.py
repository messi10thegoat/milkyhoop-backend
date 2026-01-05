"""
Accounting Kernel Adapter for Reporting Service
================================================

Bridges the Reporting Service to the new Accounting Kernel.
Provides financial reports from the General Ledger instead of
source transactions (transaksiharian).

This enables "QuickBooks-like" behavior where all reports are
derived from the journal entries, ensuring:
- Double-entry integrity
- Accurate trial balance
- Proper P&L and Balance Sheet

Author: MilkyHoop Team
Version: 1.0.0
"""

import asyncio
import logging
import os
from datetime import date, datetime
from decimal import Decimal
from typing import Dict, Any, Optional

import asyncpg

# Add accounting kernel to path - handle both Docker and local dev
import sys
if '/app/backend/services' not in sys.path:
    sys.path.insert(0, '/app/backend/services')
if '/root/milkyhoop-dev/backend/services' not in sys.path:
    sys.path.insert(0, '/root/milkyhoop-dev/backend/services')

from accounting_kernel.integration.facade import AccountingFacade
from accounting_kernel.reports.profit_loss import ProfitLossGenerator
from accounting_kernel.reports.balance_sheet import BalanceSheetGenerator
from accounting_kernel.reports.cash_flow import CashFlowGenerator
from accounting_kernel.reports.general_ledger import GeneralLedgerGenerator

logger = logging.getLogger(__name__)


class AccountingKernelAdapter:
    """
    Adapter for reporting service to use Accounting Kernel.

    Maps gRPC request parameters to Accounting Kernel calls and
    transforms responses to match existing Proto message formats.
    """

    def __init__(self, database_url: Optional[str] = None):
        """
        Initialize adapter with database connection.

        Args:
            database_url: PostgreSQL connection URL
        """
        self.database_url = database_url or os.getenv('DATABASE_URL')
        self.pool: Optional[asyncpg.Pool] = None
        self.facade: Optional[AccountingFacade] = None
        self._initialized = False

    async def initialize(self):
        """Initialize database pool and facade."""
        if self._initialized:
            return

        try:
            self.pool = await asyncpg.create_pool(
                self.database_url,
                min_size=2,
                max_size=10
            )

            self.facade = AccountingFacade(self.pool)
            await self.facade._ensure_initialized()

            self._initialized = True
            logger.info("AccountingKernelAdapter initialized")

        except Exception as e:
            logger.error(f"Failed to initialize AccountingKernelAdapter: {e}")
            raise

    async def close(self):
        """Close database connections."""
        if self.pool:
            await self.pool.close()
            self._initialized = False
            logger.info("AccountingKernelAdapter closed")

    def _parse_periode(self, periode: str) -> tuple:
        """
        Parse periode string to (start_date, end_date).

        Supports:
        - "2025-10" → October 2025
        - "2025-Q3" → Q3 2025
        - "2025" → Full year 2025
        """
        from datetime import datetime, timedelta

        if '-Q' in periode:  # Quarterly
            year, quarter = periode.split('-Q')
            year = int(year)
            quarter = int(quarter)

            quarter_months = {
                1: (1, 3),
                2: (4, 6),
                3: (7, 9),
                4: (10, 12)
            }

            start_month, end_month = quarter_months[quarter]
            start = date(year, start_month, 1)

            if end_month == 12:
                end = date(year, 12, 31)
            else:
                end = date(year, end_month + 1, 1) - timedelta(days=1)

        elif '-' in periode:  # Monthly
            year, month = periode.split('-')
            year, month = int(year), int(month)
            start = date(year, month, 1)

            if month == 12:
                end = date(year, 12, 31)
            else:
                end = date(year, month + 1, 1) - timedelta(days=1)

        else:  # Yearly
            year = int(periode)
            start = date(year, 1, 1)
            end = date(year, 12, 31)

        return start, end

    async def get_laba_rugi(
        self,
        tenant_id: str,
        periode: str,
        company_name: str = ""
    ) -> Dict[str, Any]:
        """
        Get Profit & Loss (Laba Rugi) report from Accounting Kernel.

        Returns data compatible with Proto LaporanLabaRugi message.
        """
        await self.initialize()

        try:
            start, end = self._parse_periode(periode)

            report = await self.facade.get_profit_loss(
                tenant_id=tenant_id,
                period_start=start,
                period_end=end,
                company_name=company_name
            )

            # Extract nested values safely
            revenue = report.get("revenue", {})
            cogs = report.get("cogs", {})
            operating_expenses = report.get("operating_expenses", {})
            other_income = report.get("other_income", {})

            total_revenue = int(revenue.get("subtotal", 0))
            total_cogs = int(cogs.get("subtotal", 0))
            total_op_expenses = int(operating_expenses.get("subtotal", 0))
            total_other_income = int(other_income.get("subtotal", 0))
            gross_profit = int(report.get("gross_profit", 0))
            operating_income = int(report.get("operating_income", 0))
            net_income = int(report.get("net_income", 0))

            # Calculate margins
            margin_laba_kotor = (gross_profit / total_revenue * 100) if total_revenue > 0 else 0.0
            margin_laba_bersih = (net_income / total_revenue * 100) if total_revenue > 0 else 0.0

            # Map to existing Proto structure
            return {
                "tenant_id": tenant_id,
                "periode_pelaporan": periode,
                "generated_at": int(datetime.utcnow().timestamp() * 1000),

                # Revenue
                "pendapatan_penjualan": total_revenue,
                "pendapatan_lainnya": total_other_income,
                "total_pendapatan": total_revenue + total_other_income,

                # COGS
                "hpp_bahan_baku": 0,
                "hpp_tenaga_kerja": 0,
                "hpp_overhead": 0,
                "total_hpp": total_cogs,

                # Operating Expenses (breakdown from report sections)
                "beban_gaji": self._get_expense_by_code(report, "5-20"),  # Salaries
                "beban_sewa": self._get_expense_by_code(report, "5-30"),  # Rent
                "beban_utilitas": self._get_expense_by_code(report, "5-40"),  # Utilities
                "beban_transportasi": self._get_expense_by_code(report, "5-50"),  # Transport
                "beban_penyusutan": self._get_expense_by_code(report, "5-60"),  # Depreciation
                "beban_operasional_lainnya": self._get_other_expenses(report),
                "total_beban_operasional": total_op_expenses,

                # Profits
                "laba_kotor": gross_profit,
                "laba_operasional": operating_income,
                "beban_bunga": 0,  # From other_expenses section
                "laba_bersih": net_income,

                # Margins
                "margin_laba_kotor": margin_laba_kotor,
                "margin_laba_bersih": margin_laba_bersih,

                # Metadata
                "jumlah_transaksi": 0,  # TODO: Count journal entries
            }

        except Exception as e:
            logger.error(f"Error getting laba rugi: {e}", exc_info=True)
            raise

    def _get_expense_by_code(self, report: Dict, code_prefix: str) -> int:
        """Extract expense amount by account code prefix."""
        sections = report.get("operating_expenses", {}).get("sections", [])
        for section in sections:
            for line in section.get("lines", []):
                if line.get("account_code", "").startswith(code_prefix):
                    return int(line.get("amount", 0))
        return 0

    def _get_other_expenses(self, report: Dict) -> int:
        """Get other operating expenses (not in standard categories)."""
        total = int(report.get("total_operating_expenses", 0))
        known = sum([
            self._get_expense_by_code(report, prefix)
            for prefix in ["5-20", "5-30", "5-40", "5-50", "5-60"]
        ])
        return max(0, total - known)

    async def get_neraca(
        self,
        tenant_id: str,
        periode: str,
        company_name: str = ""
    ) -> Dict[str, Any]:
        """
        Get Balance Sheet (Neraca) report from Accounting Kernel.

        Returns data compatible with Proto LaporanNeraca message.
        """
        await self.initialize()

        try:
            _, end = self._parse_periode(periode)

            report = await self.facade.get_balance_sheet(
                tenant_id=tenant_id,
                as_of_date=end,
                company_name=company_name
            )

            assets = report.get("assets", {})
            liabilities = report.get("liabilities", {})
            equity = report.get("equity", {})

            return {
                "tenant_id": tenant_id,
                "periode_pelaporan": periode,
                "generated_at": int(datetime.utcnow().timestamp() * 1000),

                # Assets
                "kas_dan_setara_kas": self._get_cash_total(assets),
                "piutang_usaha": self._get_account_total(assets, "1-104"),
                "persediaan": self._get_account_total(assets, "1-106"),
                "total_aset_lancar": int(assets.get("current_assets", {}).get("subtotal", 0)),
                "aset_tetap": int(assets.get("fixed_assets", {}).get("subtotal", 0)),
                "akumulasi_penyusutan": 0,  # Contra asset
                "aset_tetap_neto": int(assets.get("fixed_assets", {}).get("subtotal", 0)),
                "total_aset": int(assets.get("total", 0)),

                # Liabilities
                "hutang_usaha": self._get_account_total(liabilities, "2-101"),
                "hutang_pajak": self._get_account_total(liabilities, "2-104"),
                "total_liabilitas_lancar": int(liabilities.get("current_liabilities", {}).get("subtotal", 0)),
                "hutang_jangka_panjang": int(liabilities.get("long_term_liabilities", {}).get("subtotal", 0)),
                "total_liabilitas": int(liabilities.get("total", 0)),

                # Equity
                "modal_pemilik": self._get_account_total(equity, "3-1"),
                "laba_ditahan": int(equity.get("retained_earnings", 0)),
                "laba_periode_berjalan": int(equity.get("current_period_income", 0)),
                "total_ekuitas": int(equity.get("total", 0)),

                # Check
                "total_liabilitas_ekuitas": int(report.get("total_liabilities_equity", 0)),
                "is_balanced": report.get("is_balanced", False),
            }

        except Exception as e:
            logger.error(f"Error getting neraca: {e}", exc_info=True)
            raise

    def _get_cash_total(self, assets: Dict) -> int:
        """Get total cash and cash equivalents."""
        current = assets.get("current_assets", {})
        total = 0
        for line in current.get("lines", []):
            code = line.get("account_code", "")
            if code.startswith("1-101") or code.startswith("1-102"):
                total += int(line.get("amount", 0))
        return total

    def _get_account_total(self, section: Dict, code_prefix: str) -> int:
        """Get total for accounts matching code prefix."""
        for key in ["current_assets", "fixed_assets", "other_assets",
                    "current_liabilities", "long_term_liabilities", "lines"]:
            subsection = section.get(key, {})
            if isinstance(subsection, dict):
                for line in subsection.get("lines", []):
                    if line.get("account_code", "").startswith(code_prefix):
                        return int(line.get("amount", 0))
        return 0

    async def get_arus_kas(
        self,
        tenant_id: str,
        periode: str,
        company_name: str = ""
    ) -> Dict[str, Any]:
        """
        Get Cash Flow Statement (Arus Kas) from Accounting Kernel.

        Returns data compatible with Proto LaporanArusKas message.
        """
        await self.initialize()

        try:
            start, end = self._parse_periode(periode)

            report = await self.facade.get_cash_flow(
                tenant_id=tenant_id,
                period_start=start,
                period_end=end,
                company_name=company_name
            )

            operating = report.get("operating_activities", {})
            investing = report.get("investing_activities", {})
            financing = report.get("financing_activities", {})

            return {
                "tenant_id": tenant_id,
                "periode_pelaporan": periode,
                "generated_at": int(datetime.utcnow().timestamp() * 1000),

                # Operating Activities
                "laba_bersih": int(report.get("net_income", 0)),
                "penyusutan": self._get_line_amount(operating, "Penyusutan"),
                "perubahan_piutang": self._get_line_amount(operating, "Piutang"),
                "perubahan_persediaan": self._get_line_amount(operating, "Persediaan"),
                "perubahan_hutang": self._get_line_amount(operating, "Hutang"),
                "arus_kas_operasi": int(report.get("cash_from_operating", 0)),

                # Investing Activities
                "pembelian_aset": self._get_line_amount(investing, "Pembelian"),
                "penjualan_aset": self._get_line_amount(investing, "Penjualan"),
                "arus_kas_investasi": int(report.get("cash_from_investing", 0)),

                # Financing Activities
                "setoran_modal": self._get_line_amount(financing, "Modal"),
                "penarikan_prive": self._get_line_amount(financing, "Prive"),
                "pinjaman_baru": self._get_line_amount(financing, "Pinjaman"),
                "pembayaran_pinjaman": 0,
                "arus_kas_pendanaan": int(report.get("cash_from_financing", 0)),

                # Summary
                "perubahan_kas_bersih": int(report.get("net_change_in_cash", 0)),
                "kas_awal_periode": int(report.get("beginning_cash", 0)),
                "kas_akhir_periode": int(report.get("ending_cash", 0)),

                "is_balanced": report.get("is_balanced", False),
            }

        except Exception as e:
            logger.error(f"Error getting arus kas: {e}", exc_info=True)
            raise

    def _get_line_amount(self, section: Dict, search_term: str) -> int:
        """Get amount from section lines matching search term."""
        for line in section.get("lines", []):
            if search_term.lower() in line.get("description", "").lower():
                return int(line.get("amount", 0))
        return 0

    async def get_dashboard_metrics(
        self,
        tenant_id: str,
        periode: str
    ) -> Dict[str, Any]:
        """Get dashboard metrics from Accounting Kernel."""
        await self.initialize()

        try:
            start, end = self._parse_periode(periode)

            return await self.facade.get_dashboard_metrics(
                tenant_id=tenant_id,
                period_start=start,
                period_end=end
            )

        except Exception as e:
            logger.error(f"Error getting dashboard metrics: {e}", exc_info=True)
            raise


# Singleton instance
_adapter_instance: Optional[AccountingKernelAdapter] = None


async def get_kernel_adapter() -> AccountingKernelAdapter:
    """Get or create singleton adapter instance."""
    global _adapter_instance

    if _adapter_instance is None:
        _adapter_instance = AccountingKernelAdapter()
        await _adapter_instance.initialize()

    return _adapter_instance


async def close_kernel_adapter():
    """Close the singleton adapter instance."""
    global _adapter_instance

    if _adapter_instance is not None:
        await _adapter_instance.close()
        _adapter_instance = None
