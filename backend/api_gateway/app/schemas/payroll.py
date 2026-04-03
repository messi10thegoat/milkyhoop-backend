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


class UpdatePayrollRequest(BaseModel):
    payment_date: Optional[date] = None
    description: Optional[str] = None
    payment_method: Optional[str] = None
    bank_account_id: Optional[UUID] = None
    variable_inputs: Optional[List[VariableInput]] = None


class CreatePayrollPaymentRequest(BaseModel):
    payroll_id: UUID
    payment_type: str = Field(..., pattern=r"^(salary|pph21|bpjs)$")
    payment_date: date
    bank_account_id: UUID
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
    calculation_method: str = Field("fixed", pattern=r"^(fixed|percentage)$")
    percentage_base: Optional[str] = None
    sort_order: int = 0


class UpdateSalaryComponentRequest(BaseModel):
    name: Optional[str] = None
    is_taxable: Optional[bool] = None
    is_fixed: Optional[bool] = None
    default_amount: Optional[float] = None
    calculation_method: Optional[str] = None
    percentage_base: Optional[str] = None
    sort_order: Optional[int] = None
    is_active: Optional[bool] = None


class UpdateBpjsConfigRequest(BaseModel):
    configs: List[Dict[str, Any]]
