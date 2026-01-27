"""
Pydantic schemas for Fiscal Year module.

Request and response models for /api/fiscal-years endpoints.
"""

from pydantic import BaseModel, Field, field_validator
from typing import Optional, List
from datetime import date, datetime


# =============================================================================
# REQUEST MODELS
# =============================================================================


class CreateFiscalYearRequest(BaseModel):
    """Request body for creating a fiscal year."""

    name: str = Field(
        ..., min_length=1, max_length=100, description="e.g., 'Tahun Buku 2026'"
    )
    year: int = Field(..., ge=2000, le=2100, description="The calendar year")
    start_month: int = Field(default=1, ge=1, le=12, description="1=Jan, 4=Apr, 7=Jul")

    @field_validator("name")
    @classmethod
    def validate_name(cls, v):
        if not v or not v.strip():
            raise ValueError("Fiscal year name is required")
        return v.strip()


# =============================================================================
# RESPONSE MODELS
# =============================================================================


class PeriodSummary(BaseModel):
    """Brief period info for fiscal year response."""

    id: str
    period_number: int
    period_name: str
    start_date: date
    end_date: date
    status: str


class FiscalYearResponse(BaseModel):
    """Response for single fiscal year."""

    id: str
    name: str
    start_month: int
    start_date: date
    end_date: date
    status: str
    periods: List[PeriodSummary]
    closed_at: Optional[datetime] = None
    closed_by: Optional[str] = None
    created_at: datetime


class FiscalYearListItem(BaseModel):
    """Fiscal year item for list view."""

    id: str
    name: str
    start_date: date
    end_date: date
    status: str
    period_count: int = 12
    open_period_count: int
    closed_period_count: int
    created_at: datetime


class FiscalYearListResponse(BaseModel):
    """Response for list fiscal years."""

    success: bool = True
    data: List[FiscalYearListItem]
    total: int
