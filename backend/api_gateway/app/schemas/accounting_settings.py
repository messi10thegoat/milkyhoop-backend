"""
Accounting Settings Schemas
Tenant-specific accounting preferences and report basis configuration.
"""
from pydantic import BaseModel, Field, field_validator
from typing import Optional, Literal, List, Dict, Any
from enum import Enum


class ReportBasis(str, Enum):
    """Accounting basis for financial reports."""
    CASH = "cash"
    ACCRUAL = "accrual"


# ============================================================================
# ACCOUNTING SETTINGS SCHEMAS
# ============================================================================

class AccountingSettingsResponse(BaseModel):
    """Response schema for accounting settings."""
    id: str
    tenant_id: str
    default_report_basis: str = "accrual"
    fiscal_year_start_month: int = 1
    base_currency_code: str = "IDR"
    decimal_places: int = 0
    thousand_separator: str = "."
    decimal_separator: str = ","
    date_format: str = "DD/MM/YYYY"
    created_at: str
    updated_at: str


class UpdateAccountingSettingsRequest(BaseModel):
    """Schema for updating accounting settings."""
    default_report_basis: Optional[Literal["cash", "accrual"]] = None
    fiscal_year_start_month: Optional[int] = Field(None, ge=1, le=12)
    base_currency_code: Optional[str] = Field(None, min_length=3, max_length=3)
    decimal_places: Optional[int] = Field(None, ge=0, le=4)
    thousand_separator: Optional[str] = Field(None, max_length=1)
    decimal_separator: Optional[str] = Field(None, max_length=1)
    date_format: Optional[str] = Field(None, max_length=20)


class AccountingSettingsDetailResponse(BaseModel):
    """Response for accounting settings detail endpoint."""
    success: bool
    data: AccountingSettingsResponse


# ============================================================================
# REPORT WITH BASIS SCHEMAS
# ============================================================================

class ReportAccountLine(BaseModel):
    """Individual account line in a report."""
    account_id: str
    account_code: str
    account_name: str
    amount: int


class RevenueExpenseSection(BaseModel):
    """Section of revenue or expenses."""
    accounts: List[ReportAccountLine] = []
    total: int = 0


class ProfitLossReport(BaseModel):
    """Profit & Loss report data."""
    period: str
    basis: str  # 'cash' or 'accrual'
    revenue: RevenueExpenseSection
    cost_of_goods_sold: RevenueExpenseSection
    gross_profit: int
    operating_expenses: RevenueExpenseSection
    operating_income: int
    other_income: RevenueExpenseSection
    other_expenses: RevenueExpenseSection
    net_income_before_tax: int
    tax_expense: int = 0
    net_income: int


class ProfitLossReportResponse(BaseModel):
    """Response for P&L report endpoint."""
    success: bool
    data: ProfitLossReport


class BalanceSheetSection(BaseModel):
    """Section of balance sheet (assets, liabilities, equity)."""
    accounts: List[ReportAccountLine] = []
    total: int = 0


class BalanceSheetReport(BaseModel):
    """Balance Sheet report data."""
    as_of_date: str
    basis: str
    assets: Dict[str, BalanceSheetSection]  # current_assets, fixed_assets, other_assets
    total_assets: int
    liabilities: Dict[str, BalanceSheetSection]  # current_liabilities, long_term_liabilities
    total_liabilities: int
    equity: BalanceSheetSection
    total_equity: int
    total_liabilities_and_equity: int


class BalanceSheetReportResponse(BaseModel):
    """Response for balance sheet report endpoint."""
    success: bool
    data: BalanceSheetReport


# ============================================================================
# COMPARISON REPORT SCHEMAS
# ============================================================================

class ComparisonLine(BaseModel):
    """Single line comparing cash vs accrual amounts."""
    account_id: str
    account_code: str
    account_name: str
    cash_amount: int
    accrual_amount: int
    difference: int


class ComparisonSection(BaseModel):
    """Section in comparison report."""
    accounts: List[ComparisonLine] = []
    cash_total: int = 0
    accrual_total: int = 0
    difference: int = 0


class CashAccrualComparisonReport(BaseModel):
    """Side-by-side comparison of cash vs accrual basis."""
    period: str
    revenue: ComparisonSection
    cost_of_goods_sold: ComparisonSection
    gross_profit_cash: int
    gross_profit_accrual: int
    gross_profit_difference: int
    operating_expenses: ComparisonSection
    operating_income_cash: int
    operating_income_accrual: int
    operating_income_difference: int
    other_income: ComparisonSection
    other_expenses: ComparisonSection
    net_income_cash: int
    net_income_accrual: int
    net_income_difference: int


class ComparisonReportResponse(BaseModel):
    """Response for comparison report endpoint."""
    success: bool
    data: CashAccrualComparisonReport


# ============================================================================
# UNPAID ITEMS REPORT (TIMING DIFFERENCES)
# ============================================================================

class UnpaidInvoiceItem(BaseModel):
    """Unpaid invoice affecting cash vs accrual difference."""
    id: str
    invoice_number: str
    customer_name: str
    invoice_date: str
    due_date: Optional[str] = None
    total_amount: int
    paid_amount: int
    balance_due: int


class UnpaidBillItem(BaseModel):
    """Unpaid bill affecting cash vs accrual difference."""
    id: str
    bill_number: str
    vendor_name: str
    bill_date: str
    due_date: Optional[str] = None
    total_amount: int
    paid_amount: int
    balance_due: int


class TimingDifferencesReport(BaseModel):
    """Report showing timing differences between cash and accrual."""
    as_of_date: str
    unpaid_invoices: List[UnpaidInvoiceItem] = []
    total_unpaid_revenue: int = 0
    unpaid_bills: List[UnpaidBillItem] = []
    total_unpaid_expenses: int = 0
    net_timing_difference: int = 0  # unpaid_revenue - unpaid_expenses


class TimingDifferencesResponse(BaseModel):
    """Response for timing differences report."""
    success: bool
    data: TimingDifferencesReport


# ============================================================================
# FISCAL YEAR SCHEMAS
# ============================================================================

class FiscalYearPeriod(BaseModel):
    """Fiscal year period information."""
    fiscal_year: str  # e.g., "FY2024"
    start_date: str
    end_date: str
    is_current: bool = False


class FiscalYearPeriodsResponse(BaseModel):
    """Response for fiscal year periods."""
    success: bool
    data: List[FiscalYearPeriod]
