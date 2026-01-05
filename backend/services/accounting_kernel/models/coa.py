"""
Chart of Accounts Models
"""
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Optional, List
from uuid import UUID

from ..constants import AccountType, NormalBalance


@dataclass
class Account:
    """Chart of Accounts entity"""
    id: UUID
    tenant_id: str
    code: str
    name: str
    type: AccountType
    normal_balance: NormalBalance
    parent_id: Optional[UUID] = None
    is_active: bool = True
    is_system: bool = False
    metadata: dict = field(default_factory=dict)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    # Computed fields (not persisted)
    children: List["Account"] = field(default_factory=list)
    balance: Decimal = Decimal("0")

    @property
    def is_debit_normal(self) -> bool:
        """Check if account has debit normal balance"""
        return self.normal_balance == NormalBalance.DEBIT

    @property
    def is_credit_normal(self) -> bool:
        """Check if account has credit normal balance"""
        return self.normal_balance == NormalBalance.CREDIT

    def calculate_balance(self, total_debit: Decimal, total_credit: Decimal) -> Decimal:
        """Calculate account balance based on normal balance"""
        if self.is_debit_normal:
            return total_debit - total_credit
        else:
            return total_credit - total_debit

    def to_dict(self) -> dict:
        """Convert to dictionary"""
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "code": self.code,
            "name": self.name,
            "type": self.type.value,
            "normal_balance": self.normal_balance.value,
            "parent_id": str(self.parent_id) if self.parent_id else None,
            "is_active": self.is_active,
            "is_system": self.is_system,
            "metadata": self.metadata,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


@dataclass
class AccountCreate:
    """Request model for creating an account"""
    tenant_id: str
    code: str
    name: str
    type: AccountType
    normal_balance: Optional[NormalBalance] = None  # Auto-determined from type if not provided
    parent_id: Optional[UUID] = None
    is_system: bool = False
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        """Set default normal balance based on account type"""
        if self.normal_balance is None:
            from ..constants import ACCOUNT_TYPE_NORMAL_BALANCE
            self.normal_balance = ACCOUNT_TYPE_NORMAL_BALANCE.get(
                self.type,
                NormalBalance.DEBIT
            )


@dataclass
class AccountUpdate:
    """Request model for updating an account"""
    name: Optional[str] = None
    parent_id: Optional[UUID] = None
    is_active: Optional[bool] = None
    metadata: Optional[dict] = None


@dataclass
class AccountBalance:
    """Account balance at a point in time"""
    account_id: UUID
    account_code: str
    account_name: str
    account_type: AccountType
    normal_balance: NormalBalance
    total_debit: Decimal
    total_credit: Decimal
    balance: Decimal
    as_of_date: datetime

    def to_dict(self) -> dict:
        return {
            "account_id": str(self.account_id),
            "account_code": self.account_code,
            "account_name": self.account_name,
            "account_type": self.account_type.value,
            "normal_balance": self.normal_balance.value,
            "total_debit": float(self.total_debit),
            "total_credit": float(self.total_credit),
            "balance": float(self.balance),
            "as_of_date": self.as_of_date.isoformat(),
        }
