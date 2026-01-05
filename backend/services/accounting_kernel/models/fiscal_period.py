"""
Fiscal Period Models

Supports three status states:
- OPEN:   Normal operation, all posting allowed
- CLOSED: Soft close, only system reversals allowed
- LOCKED: Immutable, audit-ready
"""
from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Optional, Dict
from uuid import UUID

from ..constants import PeriodStatus


@dataclass
class FiscalPeriod:
    """Fiscal period entity for period closing and locking"""
    id: UUID
    tenant_id: str
    period_name: str  # "2026-01"
    start_date: date
    end_date: date

    # Status (replaces is_closed bool)
    status: PeriodStatus = PeriodStatus.OPEN

    # Closing info
    closed_at: Optional[datetime] = None
    closed_by: Optional[UUID] = None
    closing_journal_id: Optional[UUID] = None

    # Locking info
    locked_at: Optional[datetime] = None
    locked_by: Optional[UUID] = None
    lock_reason: Optional[str] = None

    # Snapshot balances (JSONB)
    opening_balances: Optional[Dict] = None
    closing_balances: Optional[Dict] = None
    closing_snapshot: Optional[Dict] = None

    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @property
    def is_open(self) -> bool:
        """Check if period is open for all operations"""
        return self.status == PeriodStatus.OPEN

    @property
    def is_closed(self) -> bool:
        """Check if period is closed (soft close)"""
        return self.status in (PeriodStatus.CLOSED, PeriodStatus.LOCKED)

    @property
    def is_locked(self) -> bool:
        """Check if period is locked (immutable)"""
        return self.status == PeriodStatus.LOCKED

    @property
    def can_manual_post(self) -> bool:
        """Check if manual posting is allowed"""
        return self.status == PeriodStatus.OPEN

    @property
    def can_system_post(self) -> bool:
        """Check if system-generated entries are allowed"""
        return self.status in (PeriodStatus.OPEN, PeriodStatus.CLOSED)

    @property
    def is_current(self) -> bool:
        """Check if this is the current period"""
        today = date.today()
        return self.start_date <= today <= self.end_date

    @property
    def is_future(self) -> bool:
        """Check if this is a future period"""
        return self.start_date > date.today()

    @property
    def is_past(self) -> bool:
        """Check if this is a past period"""
        return self.end_date < date.today()

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "period_name": self.period_name,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "status": self.status.value,
            "is_open": self.is_open,
            "is_closed": self.is_closed,
            "is_locked": self.is_locked,
            "can_manual_post": self.can_manual_post,
            "can_system_post": self.can_system_post,
            "closed_at": self.closed_at.isoformat() if self.closed_at else None,
            "closed_by": str(self.closed_by) if self.closed_by else None,
            "locked_at": self.locked_at.isoformat() if self.locked_at else None,
            "locked_by": str(self.locked_by) if self.locked_by else None,
            "lock_reason": self.lock_reason,
            "closing_journal_id": str(self.closing_journal_id) if self.closing_journal_id else None,
            "closing_snapshot": self.closing_snapshot,
            "is_current": self.is_current,
            "is_future": self.is_future,
            "is_past": self.is_past,
        }


@dataclass
class ClosePeriodRequest:
    """Request for closing a fiscal period"""
    tenant_id: str
    period_name: str
    closed_by: UUID
    create_closing_entries: bool = True  # Create closing journal entries


@dataclass
class ClosePeriodResponse:
    """Response after closing a period"""
    success: bool
    period_id: Optional[UUID] = None
    period_name: Optional[str] = None
    closing_journal_id: Optional[UUID] = None
    closing_snapshot: Optional[Dict] = None
    message: Optional[str] = None
    errors: list = field(default_factory=list)


@dataclass
class LockPeriodRequest:
    """Request for locking a fiscal period"""
    tenant_id: str
    period_id: UUID
    locked_by: UUID
    reason: str = ""


@dataclass
class LockPeriodResponse:
    """Response after locking a period"""
    success: bool
    period_id: Optional[UUID] = None
    period_name: Optional[str] = None
    locked_at: Optional[datetime] = None
    message: Optional[str] = None
    errors: list = field(default_factory=list)


@dataclass
class UnlockPeriodRequest:
    """Request for unlocking a fiscal period (admin only)"""
    tenant_id: str
    period_id: UUID
    unlocked_by: UUID
    reason: str  # Required - must explain why unlocking


@dataclass
class UnlockPeriodResponse:
    """Response after unlocking a period"""
    success: bool
    period_id: Optional[UUID] = None
    period_name: Optional[str] = None
    message: Optional[str] = None
    errors: list = field(default_factory=list)


@dataclass
class CreatePeriodRequest:
    """Request for creating a new fiscal period"""
    tenant_id: str
    period_name: str
    start_date: date
    end_date: date
    created_by: Optional[UUID] = None


@dataclass
class CreatePeriodResponse:
    """Response after creating a period"""
    success: bool
    period_id: Optional[UUID] = None
    period_name: Optional[str] = None
    message: Optional[str] = None
    errors: list = field(default_factory=list)
