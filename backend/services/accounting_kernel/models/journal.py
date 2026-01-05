"""
Journal Entry Models
"""
from dataclasses import dataclass, field
from datetime import datetime, date
from decimal import Decimal
from typing import Optional, List, Any
from uuid import UUID

from ..constants import JournalStatus, SourceType


@dataclass
class JournalLineInput:
    """Input for creating a journal line"""
    account_code: str
    debit: Decimal = Decimal("0")
    credit: Decimal = Decimal("0")
    description: Optional[str] = None
    memo: Optional[str] = None
    department_id: Optional[UUID] = None
    project_id: Optional[UUID] = None

    def __post_init__(self):
        """Validate debit/credit"""
        if self.debit < 0 or self.credit < 0:
            raise ValueError("Debit and credit must be non-negative")
        if self.debit > 0 and self.credit > 0:
            raise ValueError("A line cannot have both debit and credit")


@dataclass
class JournalLine:
    """Journal line entity"""
    id: UUID
    journal_id: UUID
    journal_date: date
    account_id: UUID
    line_number: int
    debit: Decimal = Decimal("0")
    credit: Decimal = Decimal("0")
    description: Optional[str] = None
    department_id: Optional[UUID] = None
    project_id: Optional[UUID] = None
    currency: str = "IDR"
    exchange_rate: Decimal = Decimal("1")
    amount_local: Optional[Decimal] = None
    created_at: Optional[datetime] = None

    # Computed/joined fields
    account_code: Optional[str] = None
    account_name: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "journal_id": str(self.journal_id),
            "account_id": str(self.account_id),
            "account_code": self.account_code,
            "account_name": self.account_name,
            "line_number": self.line_number,
            "debit": float(self.debit),
            "credit": float(self.credit),
            "description": self.description,
            "department_id": str(self.department_id) if self.department_id else None,
            "project_id": str(self.project_id) if self.project_id else None,
        }


@dataclass
class JournalEntry:
    """Journal entry entity (header)"""
    id: UUID
    tenant_id: str  # TEXT in DB, matches Tenant.id
    journal_number: str
    journal_date: date
    description: Optional[str] = None

    # Source tracking
    source_type: SourceType = SourceType.MANUAL
    source_id: Optional[UUID] = None
    trace_id: Optional[UUID] = None
    source_snapshot: Optional[dict] = None

    # Status
    status: JournalStatus = JournalStatus.POSTED
    voided_by: Optional[UUID] = None
    void_reason: Optional[str] = None

    # Reversal tracking (first-class citizen)
    reversal_of_id: Optional[UUID] = None      # If this is a reversal, points to original
    reversed_by_id: Optional[UUID] = None      # If reversed, points to reversal journal
    reversal_reason: Optional[str] = None      # Reason for reversal
    reversed_at: Optional[datetime] = None     # When this journal was reversed

    # Audit
    posted_at: Optional[datetime] = None
    posted_by: Optional[UUID] = None
    created_at: Optional[datetime] = None
    version: int = 1

    # Period tracking
    period_id: Optional[UUID] = None

    # Lines (loaded separately)
    lines: List[JournalLine] = field(default_factory=list)

    @property
    def total_debit(self) -> Decimal:
        return sum(line.debit for line in self.lines)

    @property
    def total_credit(self) -> Decimal:
        return sum(line.credit for line in self.lines)

    @property
    def is_balanced(self) -> bool:
        return abs(self.total_debit - self.total_credit) < Decimal("0.01")

    @property
    def is_reversal(self) -> bool:
        """True if this journal is a reversal of another journal."""
        return self.reversal_of_id is not None

    @property
    def is_reversed(self) -> bool:
        """True if this journal has been reversed."""
        return self.reversed_by_id is not None

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "journal_number": self.journal_number,
            "journal_date": self.journal_date.isoformat(),
            "description": self.description,
            "source_type": self.source_type.value,
            "source_id": str(self.source_id) if self.source_id else None,
            "trace_id": str(self.trace_id) if self.trace_id else None,
            "status": self.status.value,
            "posted_at": self.posted_at.isoformat() if self.posted_at else None,
            "posted_by": str(self.posted_by) if self.posted_by else None,
            # Reversal info
            "is_reversal": self.is_reversal,
            "is_reversed": self.is_reversed,
            "reversal_of_id": str(self.reversal_of_id) if self.reversal_of_id else None,
            "reversed_by_id": str(self.reversed_by_id) if self.reversed_by_id else None,
            "reversal_reason": self.reversal_reason,
            "reversed_at": self.reversed_at.isoformat() if self.reversed_at else None,
            # Totals
            "total_debit": float(self.total_debit),
            "total_credit": float(self.total_credit),
            "is_balanced": self.is_balanced,
            "lines": [line.to_dict() for line in self.lines],
        }


@dataclass
class CreateJournalRequest:
    """Request for creating a journal entry"""
    tenant_id: str  # TEXT in DB, matches Tenant.id
    journal_date: date
    description: Optional[str] = None
    source_type: SourceType = SourceType.MANUAL
    source_id: Optional[UUID] = None
    trace_id: Optional[str] = None  # TEXT in DB for idempotency
    posted_by: Optional[UUID] = None
    source_snapshot: Optional[dict] = None
    lines: List[JournalLineInput] = field(default_factory=list)

    def validate(self) -> List[str]:
        """Validate the request"""
        errors = []

        if not self.lines:
            errors.append("Journal must have at least one line")

        if len(self.lines) < 2:
            errors.append("Journal must have at least two lines for double-entry")

        total_debit = sum(line.debit for line in self.lines)
        total_credit = sum(line.credit for line in self.lines)

        if abs(total_debit - total_credit) >= Decimal("0.01"):
            errors.append(
                f"Journal is not balanced: debit={total_debit}, credit={total_credit}"
            )

        return errors


@dataclass
class JournalResponse:
    """Response after creating/modifying a journal"""
    success: bool
    journal_id: Optional[UUID] = None
    journal_number: Optional[str] = None
    status: Optional[JournalStatus] = None
    message: Optional[str] = None
    is_duplicate: bool = False
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "journal_id": str(self.journal_id) if self.journal_id else None,
            "journal_number": self.journal_number,
            "status": self.status.value if self.status else None,
            "message": self.message,
            "is_duplicate": self.is_duplicate,
            "errors": self.errors,
        }
