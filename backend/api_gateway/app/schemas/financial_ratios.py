"""
Pydantic schemas for Financial Ratios module.

Provides financial ratio calculation and analysis:
- Liquidity ratios (Current, Quick, Cash)
- Profitability ratios (Gross Margin, Net Margin, ROE, ROA)
- Efficiency ratios (Inventory Turnover, DSO, DPO, CCC)
- Leverage ratios (Debt Ratio, Debt-to-Equity)

NO JOURNAL ENTRIES - This is a calculation/reporting system.
"""

from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any, Literal
from datetime import date, datetime


# =============================================================================
# REQUEST MODELS
# =============================================================================

class CalculateRatiosRequest(BaseModel):
    """Request to calculate financial ratios."""
    as_of_date: date = Field(..., description="Balance sheet date")
    period_start: Optional[date] = Field(None, description="Income statement period start")
    period_end: Optional[date] = Field(None, description="Income statement period end")


class SaveSnapshotRequest(BaseModel):
    """Request to save ratio snapshot."""
    snapshot_date: date = Field(..., description="Date for the snapshot")
    period_type: Literal["daily", "monthly", "quarterly", "yearly"] = Field("monthly")


class CreateAlertRequest(BaseModel):
    """Request to create/update ratio alert thresholds."""
    ratio_code: str = Field(..., min_length=1, max_length=50)
    warning_min: Optional[float] = None
    warning_max: Optional[float] = None
    critical_min: Optional[float] = None
    critical_max: Optional[float] = None
    notify_on_warning: bool = Field(True)
    notify_on_critical: bool = Field(True)
    is_active: bool = Field(True)


# =============================================================================
# RESPONSE MODELS - Ratio Values
# =============================================================================

class RatioValue(BaseModel):
    """Single ratio value with interpretation."""
    value: Optional[float] = None
    display: Optional[str] = None  # Formatted display value
    status: Optional[str] = None  # good, warning, critical, below_ideal, above_ideal
    ideal_range: Optional[str] = None


class LiquidityRatios(BaseModel):
    """Liquidity ratio category."""
    current_ratio: Optional[RatioValue] = None
    quick_ratio: Optional[RatioValue] = None
    cash_ratio: Optional[RatioValue] = None
    working_capital: Optional[RatioValue] = None


class ProfitabilityRatios(BaseModel):
    """Profitability ratio category."""
    gross_profit_margin: Optional[RatioValue] = None
    operating_margin: Optional[RatioValue] = None
    net_profit_margin: Optional[RatioValue] = None
    roe: Optional[RatioValue] = None
    roa: Optional[RatioValue] = None


class EfficiencyRatios(BaseModel):
    """Efficiency ratio category."""
    asset_turnover: Optional[RatioValue] = None
    inventory_turnover: Optional[RatioValue] = None
    days_inventory: Optional[RatioValue] = None
    receivables_turnover: Optional[RatioValue] = None
    days_receivable: Optional[RatioValue] = None
    payables_turnover: Optional[RatioValue] = None
    days_payable: Optional[RatioValue] = None
    cash_conversion_cycle: Optional[RatioValue] = None


class LeverageRatios(BaseModel):
    """Leverage ratio category."""
    debt_ratio: Optional[RatioValue] = None
    debt_to_equity: Optional[RatioValue] = None
    interest_coverage: Optional[RatioValue] = None
    equity_ratio: Optional[RatioValue] = None


class AllRatios(BaseModel):
    """All financial ratios by category."""
    liquidity: Optional[Dict[str, Any]] = None
    profitability: Optional[Dict[str, Any]] = None
    efficiency: Optional[Dict[str, Any]] = None
    leverage: Optional[Dict[str, Any]] = None


class SourceData(BaseModel):
    """Source data used for ratio calculations."""
    current_assets: int = 0
    current_liabilities: int = 0
    total_assets: int = 0
    total_liabilities: int = 0
    equity: int = 0
    inventory: int = 0
    cash: int = 0
    receivables: int = 0
    payables: int = 0
    revenue: int = 0
    cogs: int = 0
    operating_income: int = 0
    net_income: int = 0


class CalculatedRatios(BaseModel):
    """Complete calculated ratios with source data."""
    as_of_date: date
    period_start: Optional[date] = None
    period_end: Optional[date] = None
    ratios: AllRatios
    source_data: SourceData


# =============================================================================
# RESPONSE MODELS - Definitions
# =============================================================================

class RatioDefinition(BaseModel):
    """Financial ratio definition."""
    code: str
    name: str
    category: str
    formula: str
    description: Optional[str] = None
    ideal_min: Optional[float] = None
    ideal_max: Optional[float] = None
    higher_is_better: Optional[bool] = None
    display_format: str = "decimal"  # decimal, percentage, times, days
    decimal_places: int = 2


# =============================================================================
# RESPONSE MODELS - Trend & History
# =============================================================================

class RatioTrendPoint(BaseModel):
    """Single point in ratio trend."""
    period: str  # YYYY-MM format
    value: Optional[float] = None


class RatioTrend(BaseModel):
    """Ratio trend over time."""
    ratio_code: str
    ratio_name: str
    trend: List[RatioTrendPoint]
    analysis: Optional[Dict[str, Any]] = None  # direction, change_pct, average, min, max


class RatioSnapshot(BaseModel):
    """Historical ratio snapshot."""
    id: str
    snapshot_date: date
    period_type: str
    period_start: Optional[date] = None
    period_end: Optional[date] = None
    current_ratio: Optional[float] = None
    quick_ratio: Optional[float] = None
    gross_profit_margin: Optional[float] = None
    net_profit_margin: Optional[float] = None
    debt_to_equity: Optional[float] = None
    created_at: datetime


# =============================================================================
# RESPONSE MODELS - Alerts
# =============================================================================

class RatioAlert(BaseModel):
    """Ratio alert/threshold configuration."""
    id: str
    ratio_code: str
    ratio_name: Optional[str] = None
    warning_min: Optional[float] = None
    warning_max: Optional[float] = None
    critical_min: Optional[float] = None
    critical_max: Optional[float] = None
    notify_on_warning: bool
    notify_on_critical: bool
    is_active: bool


class RatioAlertStatus(BaseModel):
    """Current alert status for a ratio."""
    ratio_code: str
    ratio_name: str
    current_value: Optional[float] = None
    alert_level: str  # normal, warning, critical, below_ideal, above_ideal
    threshold_min: Optional[float] = None
    threshold_max: Optional[float] = None


# =============================================================================
# RESPONSE MODELS - Benchmark
# =============================================================================

class IndustryBenchmark(BaseModel):
    """Industry benchmark for a ratio."""
    industry: str
    ratio_code: str
    benchmark_min: Optional[float] = None
    benchmark_avg: Optional[float] = None
    benchmark_max: Optional[float] = None
    source: Optional[str] = None
    year: Optional[int] = None


class BenchmarkComparison(BaseModel):
    """Comparison of ratio to industry benchmark."""
    ratio_code: str
    ratio_name: str
    current_value: Optional[float] = None
    benchmark_avg: Optional[float] = None
    variance: Optional[float] = None  # percentage above/below benchmark
    performance: Optional[str] = None  # above_average, average, below_average


# =============================================================================
# RESPONSE MODELS - Dashboard
# =============================================================================

class RatioDashboard(BaseModel):
    """Dashboard summary of key ratios."""
    as_of_date: date
    key_ratios: Dict[str, RatioValue]
    alerts: List[RatioAlertStatus]
    trends: Dict[str, str]  # ratio_code -> direction (improving, stable, declining)


# =============================================================================
# GENERIC RESPONSE MODELS
# =============================================================================

class CalculateRatiosResponse(BaseModel):
    """Response for ratio calculation."""
    success: bool = True
    data: CalculatedRatios


class RatioDefinitionListResponse(BaseModel):
    """Response for listing ratio definitions."""
    items: List[RatioDefinition]
    total: int


class RatioTrendResponse(BaseModel):
    """Response for ratio trend."""
    success: bool = True
    data: RatioTrend


class RatioSnapshotListResponse(BaseModel):
    """Response for listing snapshots."""
    items: List[RatioSnapshot]
    total: int
    has_more: bool = False


class RatioAlertListResponse(BaseModel):
    """Response for listing alerts."""
    success: bool = True
    data: List[RatioAlertStatus]


class RatioDashboardResponse(BaseModel):
    """Response for dashboard."""
    success: bool = True
    data: RatioDashboard


class BenchmarkComparisonResponse(BaseModel):
    """Response for benchmark comparison."""
    success: bool = True
    industry: str
    data: List[BenchmarkComparison]


class FinancialRatioResponse(BaseModel):
    """Generic financial ratio operation response."""
    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None
