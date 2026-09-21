"""Pydantic schemas for Payroll endpoints."""

from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from uuid import UUID
from datetime import date


class CreatePayrollRequest(BaseModel):
    period_start: date
    period_end: date
    payment_date: Optional[date] = None
    description: Optional[str] = None
    employee_ids: List[UUID]
    payment_method: Optional[str] = None
    bank_account_id: Optional[UUID] = None


class VariableInput(BaseModel):
    employee_id: UUID
    component_code: str
    amount: Optional[float] = None
    overtime_hours: Optional[float] = None
    days_worked: Optional[float] = None


class PieceLineInput(BaseModel):
    employee_id: UUID
    description: str
    job_reference: Optional[str] = None
    work_order_id: Optional[UUID] = None
    quantity: float
    rate: float
    sort_order: int = 0


class UpdatePayrollRequest(BaseModel):
    payment_date: Optional[date] = None
    description: Optional[str] = None
    payment_method: Optional[str] = None
    bank_account_id: Optional[UUID] = None
    variable_inputs: Optional[List[VariableInput]] = None
    piece_lines: Optional[List[PieceLineInput]] = None


class CreatePayrollPaymentRequest(BaseModel):
    payroll_id: UUID
    payment_type: str = Field(..., pattern=r"^(salary|pph21|bpjs)$")
    payment_date: date
    # bank_account_id = the TRANSFER account. Optional: a cash-only salary run needs no
    # transfer account (and non-salary payments always use it). cash_account_id = the Kas
    # bank_accounts row for cash-paid staff. The handler requires each only when the run's
    # split actually uses that method (TF/CASH, V284).
    bank_account_id: Optional[UUID] = None
    cash_account_id: Optional[UUID] = None
    reference_number: Optional[str] = None
    notes: Optional[str] = None


class VoidPayrollRequest(BaseModel):
    reason: str = Field(..., min_length=1)


class RejectPayrollRequest(BaseModel):
    reason: Optional[str] = None


class CreateSalaryComponentRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=30)
    name: str = Field(..., min_length=1, max_length=100)
    type: str = Field(..., pattern=r"^(earning|deduction|employer_cost)$")
    category: str
    is_taxable: bool = True
    is_fixed: bool = True
    default_amount: float = 0
    calculation_method: str = Field("fixed", pattern=r"^(fixed|percentage|daily|hourly|overtime)$")
    percentage_base: Optional[str] = None
    sort_order: int = 0


class UpdateSalaryComponentRequest(BaseModel):
    name: Optional[str] = None
    is_taxable: Optional[bool] = None
    is_fixed: Optional[bool] = None
    default_amount: Optional[float] = None
    # Validate the same enum as create -- an unrecognised method would be silently treated
    # as flat by the calc engine (the "looks wired, changes nothing" trap). Reject at the API.
    calculation_method: Optional[str] = Field(default=None, pattern=r"^(fixed|percentage|daily|hourly|overtime)$")
    percentage_base: Optional[str] = None
    sort_order: Optional[int] = None
    is_active: Optional[bool] = None


class UpdateBpjsConfigRequest(BaseModel):
    configs: List[Dict[str, Any]]
