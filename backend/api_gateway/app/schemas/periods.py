"""
Pydantic schemas for Accounting Period module.

Request and response models for /api/periods endpoints.
"""

from pydantic import BaseModel, Field, field_validator
from typing import Optional, List
from datetime import date, datetime


# =============================================================================
# REQUEST MODELS
# =============================================================================


class UpdatePeriodRequest(BaseModel):
    """Request body for updating period info."""

    name: Optional[str] = Field(None, max_length=50)


class ClosePeriodRequest(BaseModel):
    """Request body for closing a period."""

    closing_notes: Optional[str] = Field(None, max_length=1000)
    force: bool = Field(
        default=False, description="Force close even with draft journals"
    )


class ReopenPeriodRequest(BaseModel):
    """Request body for reopening a period."""

    reason: str = Field(
        ..., min_length=1, max_length=500, description="Reason for reopening"
    )

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, v):
        if not v or not v.strip():
            raise ValueError("Reason is required for reopening a period")
        return v.strip()


# =============================================================================
# RESPONSE MODELS
# =============================================================================


class PeriodResponse(BaseModel):
    """Response for single accounting period."""

    id: str
    period_name: str
    period_number: Optional[int] = None
    fiscal_year_id: Optional[str] = None
    fiscal_year_name: Optional[str] = None
    start_date: date
    end_date: date
    status: str
    closed_at: Optional[datetime] = None
    closed_by: Optional[str] = None
    closing_notes: Optional[str] = None


class PeriodListItem(BaseModel):
    """Period item for list view."""

    id: str
    period_name: str
    period_number: Optional[int] = None
    fiscal_year_name: Optional[str] = None
    start_date: date
    end_date: date
    status: str
    journal_count: int = 0
    draft_journal_count: int = 0


class PeriodListResponse(BaseModel):
    """Response for list periods."""

    success: bool = True
    data: List[PeriodListItem]
    total: int


class DraftJournalInfo(BaseModel):
    """Brief info about draft journal."""

    id: str
    journal_number: str
    description: str
    entry_date: date


class ClosePeriodWarning(BaseModel):
    """Warning during period close."""

    code: str
    message: str
    draft_journals: List[DraftJournalInfo] = []


class ClosePeriodError(BaseModel):
    """Error during period close."""

    code: str
    message: str


class TrialBalanceSnapshotResponse(BaseModel):
    """Trial balance snapshot created during period close."""

    id: str
    as_of_date: date
    total_debit: float
    total_credit: float
    is_balanced: bool
    generated_at: datetime


class ClosePeriodResponse(BaseModel):
    """Response for close period endpoint."""

    success: bool
    data: Optional[dict] = None
    warnings: List[ClosePeriodWarning] = []
    errors: List[ClosePeriodError] = []
