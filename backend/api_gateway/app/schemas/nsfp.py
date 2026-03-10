"""
NSFP Schemas — Pydantic models for NSFP range management.
"""

from pydantic import BaseModel, validator
from typing import Optional, List
from datetime import date, datetime


class NSFPRangeCreate(BaseModel):
    prefix: str
    range_start: int
    range_end: int
    allocated_date: Optional[date] = None

    @validator('prefix')
    def validate_prefix(cls, v):
        if not v or not v.strip():
            raise ValueError('Prefix tidak boleh kosong')
        return v.strip()

    @validator('range_start')
    def validate_start(cls, v):
        if v <= 0:
            raise ValueError('range_start harus > 0')
        return v

    @validator('range_end')
    def validate_end(cls, v, values):
        if 'range_start' in values and v < values['range_start']:
            raise ValueError('range_end harus >= range_start')
        if 'range_start' in values and (v - values['range_start'] + 1) > 10_000_000:
            raise ValueError('Maksimal 10 juta nomor per range')
        return v


class NSFPRangeUpdate(BaseModel):
    is_active: bool


class NSFPRangeResponse(BaseModel):
    id: str
    prefix: str
    range_start: int
    range_end: int
    current_number: int
    is_active: bool
    allocated_date: Optional[date] = None
    exhausted_at: Optional[datetime] = None
    remaining: int
    total: int
    usage_percent: float


class NSFPRangeListResponse(BaseModel):
    data: List[NSFPRangeResponse]
    total: int


class NSFPUsageResponse(BaseModel):
    total_allocated: int
    total_used: int
    total_remaining: int
    active_ranges: int
    exhausted_ranges: int
    warning: Optional[str] = None
