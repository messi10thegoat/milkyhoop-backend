"""
Drill-Down Report Schemas
For drilling into account transaction details from P&L or Balance Sheet.
"""
from datetime import date
from decimal import Decimal
from typing import Optional, List, Dict, Any
from uuid import UUID
from pydantic import BaseModel, Field


class DrillDownRequest(BaseModel):
    """Request parameters for drill-down query."""
    account_id: UUID
    start_date: date
    end_date: date
    page: int = Field(default=1, ge=1)
    limit: int = Field(default=50, ge=1, le=200)


class DrillDownTransaction(BaseModel):
    """Single transaction in drill-down results."""
    journal_id: UUID
    journal_number: str
    entry_date: date
    source_type: Optional[str] = None  # invoice, bill, payment, manual
    source_id: Optional[UUID] = None
    description: Optional[str] = None
    memo: Optional[str] = None
    debit: Decimal
    credit: Decimal
    running_balance: Decimal


class DrillDownResponse(BaseModel):
    """Response for drill-down endpoint."""
    account_id: UUID
    account_code: str
    account_name: str
    account_type: str
    normal_balance: str  # DEBIT or CREDIT
    period_start: date
    period_end: date
    opening_balance: Decimal
    total_debit: Decimal
    total_credit: Decimal
    closing_balance: Decimal
    transactions: List[DrillDownTransaction]
    pagination: Dict[str, Any]
