"""
Pydantic schemas for Journal Entry module.

Request and response models for /api/journals endpoints.
"""

from pydantic import BaseModel, Field, field_validator
from typing import Optional, List
from datetime import date, datetime
from decimal import Decimal


# =============================================================================
# CONSTANTS
# =============================================================================

JOURNAL_STATUSES = ["draft", "posted", "reversed"]
SOURCE_TYPES = [
    "manual",
    "sales_invoice",
    "purchase_invoice",
    "payment_received",
    "payment_made",
    "expense",
    "adjustment",
    "opening",
    "closing",
]


# =============================================================================
# REQUEST MODELS
# =============================================================================


class JournalLineInput(BaseModel):
    """Single line item for journal entry."""

    account_id: str = Field(..., description="Account UUID")
    description: Optional[str] = Field(None, max_length=500)
    debit: Decimal = Field(default=Decimal("0"), ge=0)
    credit: Decimal = Field(default=Decimal("0"), ge=0)

    @field_validator("debit", "credit", mode="before")
    @classmethod
    def coerce_decimal(cls, v):
        if v is None:
            return Decimal("0")
        return Decimal(str(v))


class CreateJournalRequest(BaseModel):
    """Request body for creating a manual journal entry."""

    entry_date: date = Field(..., description="Journal date")
    description: str = Field(..., min_length=1, max_length=500)
    lines: List[JournalLineInput] = Field(..., min_length=2)
    save_as_draft: bool = Field(
        default=False, description="If true, save as draft instead of posting"
    )

    @field_validator("lines")
    @classmethod
    def validate_lines(cls, v):
        if len(v) < 2:
            raise ValueError("Journal must have at least 2 lines")

        total_debit = sum(line.debit for line in v)
        total_credit = sum(line.credit for line in v)

        if total_debit != total_credit:
            raise ValueError(
                f"Journal not balanced: debit={total_debit}, credit={total_credit}"
            )

        if total_debit == 0:
            raise ValueError("Journal cannot have zero total")

        # Each line must have either debit or credit (not both, not neither)
        for i, line in enumerate(v):
            if line.debit > 0 and line.credit > 0:
                raise ValueError(f"Line {i+1}: cannot have both debit and credit")
            if line.debit == 0 and line.credit == 0:
                raise ValueError(f"Line {i+1}: must have either debit or credit")

        return v


class ReverseJournalRequest(BaseModel):
    """Request body for reversing a journal entry."""

    reversal_date: date = Field(..., description="Date for the reversal entry")
    reason: str = Field(
        ..., min_length=1, max_length=500, description="Reason for reversal"
    )


# =============================================================================
# RESPONSE MODELS
# =============================================================================


class JournalLineResponse(BaseModel):
    """Single line in journal response."""

    id: str
    line_number: int
    account_id: str
    account_code: str
    account_name: str
    description: Optional[str] = None
    debit: Decimal
    credit: Decimal


class JournalResponse(BaseModel):
    """Response for single journal entry."""

    id: str
    journal_number: str
    entry_date: date
    period_id: Optional[str] = None
    period_name: Optional[str] = None

    source_type: str
    source_id: Optional[str] = None
    source_number: Optional[str] = None

    description: str
    lines: List[JournalLineResponse]

    total_debit: Decimal
    total_credit: Decimal
    is_balanced: bool

    status: str
    reversal_of_id: Optional[str] = None
    reversed_by_id: Optional[str] = None

    created_by: Optional[str] = None
    created_at: datetime
    posted_at: Optional[datetime] = None
    posted_by: Optional[str] = None


class JournalListItem(BaseModel):
    """Simplified journal for list view."""

    id: str
    journal_number: str
    entry_date: date
    description: str
    source_type: str
    source_number: Optional[str] = None
    total_debit: Decimal
    total_credit: Decimal
    status: str
    created_at: datetime


class JournalSummary(BaseModel):
    """Summary statistics for journal list."""

    total_count: int
    draft_count: int
    posted_count: int
    reversed_count: int


class JournalListResponse(BaseModel):
    """Response for list journals endpoint."""

    success: bool = True
    data: List[JournalListItem]
    summary: JournalSummary
    pagination: dict
