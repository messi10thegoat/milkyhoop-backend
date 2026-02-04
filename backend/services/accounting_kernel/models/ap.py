"""
Accounts Payable Models
"""
from dataclasses import dataclass, field
from datetime import datetime, date
from decimal import Decimal
from typing import Optional, List
from uuid import UUID

from ..constants import ARAPStatus, SourceType


@dataclass
class AccountPayable:
    """Accounts Payable entity (subledger)"""
    id: UUID
    tenant_id: str
    supplier_id: UUID
    supplier_name: Optional[str] = None

    # Source
    source_type: SourceType = SourceType.BILL
    source_id: Optional[UUID] = None
    source_number: Optional[str] = None

    # Amount
    amount: Decimal = Decimal("0")
    balance: Decimal = Decimal("0")  # Remaining unpaid
    currency: str = "IDR"

    # Dates
    issue_date: Optional[date] = None
    due_date: Optional[date] = None

    # Status
    status: ARAPStatus = ARAPStatus.OPEN

    # Link to journal
    journal_id: Optional[UUID] = None
    journal_date: Optional[date] = None

    # Audit
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    # Payment applications
    payments: List["APPaymentApplication"] = field(default_factory=list)

    @property
    def amount_paid(self) -> Decimal:
        """Total amount paid"""
        return self.amount - self.balance

    @property
    def is_overdue(self) -> bool:
        """Check if AP is overdue"""
        if self.due_date and self.status in (ARAPStatus.OPEN, ARAPStatus.PARTIAL):
            return date.today() > self.due_date
        return False

    @property
    def days_overdue(self) -> int:
        """Number of days overdue"""
        if self.is_overdue and self.due_date:
            return (date.today() - self.due_date).days
        return 0

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "supplier_id": str(self.supplier_id),
            "supplier_name": self.supplier_name,
            "source_type": self.source_type.value,
            "source_id": str(self.source_id) if self.source_id else None,
            "source_number": self.source_number,
            "amount": float(self.amount),
            "balance": float(self.balance),
            "amount_paid": float(self.amount_paid),
            "currency": self.currency,
            "issue_date": self.issue_date.isoformat() if self.issue_date else None,
            "due_date": self.due_date.isoformat() if self.due_date else None,
            "status": self.status.value,
            "is_overdue": self.is_overdue,
            "days_overdue": self.days_overdue,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


@dataclass
class APPaymentApplication:
    """Payment application to AP"""
    id: UUID
    tenant_id: str
    ap_id: UUID
    payment_date: date
    amount_applied: Decimal
    payment_method: str = "transfer"
    reference_number: Optional[str] = None
    notes: Optional[str] = None

    # Link to journal
    journal_id: Optional[UUID] = None

    created_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "ap_id": str(self.ap_id),
            "payment_date": self.payment_date.isoformat(),
            "amount_applied": float(self.amount_applied),
            "payment_method": self.payment_method,
            "reference_number": self.reference_number,
            "notes": self.notes,
            "journal_id": str(self.journal_id) if self.journal_id else None,
        }


@dataclass
class CreateAPRequest:
    """Request for creating AP"""
    tenant_id: str
    supplier_id: UUID
    supplier_name: Optional[str] = None
    source_type: SourceType = SourceType.BILL
    source_id: Optional[UUID] = None
    source_number: Optional[str] = None
    amount: Decimal = Decimal("0")
    issue_date: Optional[date] = None
    due_date: Optional[date] = None
    journal_id: Optional[UUID] = None
    journal_date: Optional[date] = None


@dataclass
class ApplyAPPaymentRequest:
    """Request for applying payment to AP"""
    tenant_id: str
    ap_id: UUID
    payment_id: UUID
    payment_date: date
    amount_applied: Decimal
    journal_id: Optional[UUID] = None
    journal_date: Optional[date] = None
