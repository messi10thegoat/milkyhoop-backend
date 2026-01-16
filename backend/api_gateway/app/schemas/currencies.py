"""
Currencies and Exchange Rates Schemas
Multi-currency support with forex gain/loss tracking.
"""
from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Literal, Dict, Any
from datetime import date
from decimal import Decimal


# ============================================================================
# CURRENCY SCHEMAS
# ============================================================================

class CreateCurrencyRequest(BaseModel):
    """Schema for creating a new currency."""
    code: str = Field(..., min_length=3, max_length=3, description="ISO 4217 currency code")
    name: str = Field(..., min_length=1, max_length=100, description="Currency name")
    symbol: Optional[str] = Field(None, max_length=10, description="Currency symbol")
    decimal_places: int = Field(2, ge=0, le=4, description="Decimal places")
    is_base_currency: bool = Field(False, description="Set as base currency")

    @field_validator('code')
    @classmethod
    def validate_code(cls, v):
        return v.upper().strip()


class UpdateCurrencyRequest(BaseModel):
    """Schema for updating a currency."""
    name: Optional[str] = Field(None, max_length=100)
    symbol: Optional[str] = Field(None, max_length=10)
    decimal_places: Optional[int] = Field(None, ge=0, le=4)
    is_active: Optional[bool] = None


class CurrencyDetail(BaseModel):
    """Full currency detail."""
    id: str
    code: str
    name: str
    symbol: Optional[str] = None
    decimal_places: int
    is_base_currency: bool
    is_active: bool
    created_at: str
    updated_at: str


class CurrencyListItem(BaseModel):
    """Currency list item."""
    id: str
    code: str
    name: str
    symbol: Optional[str] = None
    decimal_places: int
    is_base_currency: bool
    is_active: bool


class CurrencyListResponse(BaseModel):
    """Response for currency list."""
    items: List[CurrencyListItem]
    total: int


class CurrencyResponse(BaseModel):
    """Generic currency operation response."""
    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None


# ============================================================================
# EXCHANGE RATE SCHEMAS
# ============================================================================

class CreateExchangeRateRequest(BaseModel):
    """Schema for creating an exchange rate."""
    from_currency_id: str = Field(..., description="Source currency UUID")
    to_currency_id: str = Field(..., description="Target currency UUID")
    rate_date: date = Field(..., description="Rate date")
    rate: float = Field(..., gt=0, description="Exchange rate (1 from = rate * to)")
    source: Literal['manual', 'api', 'bank'] = Field('manual', description="Rate source")

    @field_validator('rate')
    @classmethod
    def validate_rate(cls, v):
        if v <= 0:
            raise ValueError('Rate must be positive')
        return v


class ExchangeRateDetail(BaseModel):
    """Exchange rate detail."""
    id: str
    from_currency_id: str
    from_currency_code: str
    to_currency_id: str
    to_currency_code: str
    rate_date: str
    rate: float
    source: str
    created_at: str


class ExchangeRateListResponse(BaseModel):
    """Response for exchange rate list."""
    items: List[ExchangeRateDetail]
    total: int
    has_more: bool = False


class LatestRateItem(BaseModel):
    """Latest rate for a currency pair."""
    from_currency_code: str
    to_currency_code: str
    rate: float
    rate_date: str


class LatestRatesResponse(BaseModel):
    """Response for latest rates endpoint."""
    success: bool
    base_currency: str
    rates: List[LatestRateItem]
    as_of: str


class ExchangeRateResponse(BaseModel):
    """Generic exchange rate operation response."""
    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None


# ============================================================================
# CONVERSION SCHEMAS
# ============================================================================

class ConvertAmountRequest(BaseModel):
    """Schema for currency conversion."""
    amount: int = Field(..., description="Amount to convert (smallest unit)")
    from_currency_id: str = Field(..., description="Source currency")
    to_currency_id: str = Field(..., description="Target currency")
    as_of_date: Optional[date] = Field(None, description="Rate date (defaults to today)")


class ConvertAmountResponse(BaseModel):
    """Response for currency conversion."""
    success: bool
    original_amount: int
    converted_amount: int
    from_currency_code: str
    to_currency_code: str
    rate: float
    rate_date: str


# ============================================================================
# FOREX GAIN/LOSS SCHEMAS
# ============================================================================

class ForexGainLossItem(BaseModel):
    """Forex gain/loss record."""
    id: str
    source_type: str
    source_id: Optional[str] = None
    transaction_date: str
    original_currency_code: str
    original_amount: int
    original_rate: float
    settlement_rate: float
    gain_loss_amount: int
    is_gain: bool
    is_realized: bool
    journal_id: Optional[str] = None
    created_at: str


class ForexGainLossReport(BaseModel):
    """Forex gain/loss report."""
    period_start: str
    period_end: str
    realized_gain: int
    realized_loss: int
    net_realized: int
    unrealized_gain: int
    unrealized_loss: int
    net_unrealized: int
    items: List[ForexGainLossItem]


class ForexReportResponse(BaseModel):
    """Response for forex report endpoint."""
    success: bool
    data: ForexGainLossReport


class RevaluationRequest(BaseModel):
    """Schema for month-end revaluation."""
    as_of_date: date = Field(..., description="Revaluation date")
    currency_ids: Optional[List[str]] = Field(None, description="Specific currencies (all if empty)")
