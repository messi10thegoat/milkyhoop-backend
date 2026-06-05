"""Pydantic schemas for Employee endpoints."""

from pydantic import BaseModel, Field
from typing import Optional, List
from uuid import UUID
from datetime import date


class CreateEmployeeRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    pay_group_id: UUID = Field(
        ..., description="Required: DB column pay_group_id NOT NULL"
    )
    employee_code: Optional[str] = None
    email: Optional[str] = None
    department: Optional[str] = None
    position: Optional[str] = None
    nik: Optional[str] = Field(None, max_length=16)
    npwp: Optional[str] = Field(None, max_length=20)
    ptkp_status: str = Field("TK0", pattern=r"^(TK[0-3]|K[0-3]|KI[0-3])$")
    tax_method: str = Field("gross", pattern=r"^(gross|nett)$")
    marital_status: Optional[str] = None
    date_of_birth: Optional[date] = None
    gender: Optional[str] = Field(None, max_length=1)
    religion: Optional[str] = None
    join_date: Optional[date] = None
    employee_type: str = Field("tetap", pattern=r"^(tetap|tidak_tetap)$")
    bpjs_kes_number: Optional[str] = None
    bpjs_tk_number: Optional[str] = None
    bank_name: Optional[str] = None
    bank_account_number: Optional[str] = None
    bank_account_name: Optional[str] = None
    jkk_risk_level: int = Field(1, ge=1, le=5)
    is_bpjs_kes: bool = True
    is_bpjs_jht: bool = True
    is_bpjs_jp: bool = True
    phone: Optional[str] = None
    address: Optional[str] = None


class UpdateEmployeeRequest(BaseModel):
    name: Optional[str] = None
    employee_code: Optional[str] = None
    email: Optional[str] = None
    department: Optional[str] = None
    position: Optional[str] = None
    nik: Optional[str] = None
    npwp: Optional[str] = None
    ptkp_status: Optional[str] = None
    tax_method: Optional[str] = None
    marital_status: Optional[str] = None
    date_of_birth: Optional[date] = None
    gender: Optional[str] = None
    religion: Optional[str] = None
    join_date: Optional[date] = None
    resign_date: Optional[date] = None
    employee_type: Optional[str] = None
    bpjs_kes_number: Optional[str] = None
    bpjs_tk_number: Optional[str] = None
    bank_name: Optional[str] = None
    bank_account_number: Optional[str] = None
    bank_account_name: Optional[str] = None
    jkk_risk_level: Optional[int] = None
    is_bpjs_kes: Optional[bool] = None
    is_bpjs_jht: Optional[bool] = None
    is_bpjs_jp: Optional[bool] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    is_active: Optional[bool] = None


class SalaryConfigItem(BaseModel):
    component_id: UUID
    amount: float = 0
    percentage: Optional[float] = None
    effective_date: date


class SetSalaryConfigRequest(BaseModel):
    configs: List[SalaryConfigItem]
