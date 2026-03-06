"""
Pydantic schemas for Tax Groups module — V124 schema.
"""

from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Dict, Any


class TaxGroupItemInput(BaseModel):
    tax_code_id: str
    sequence: int = 0


class CreateTaxGroupRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=20)
    name: str = Field(..., min_length=1, max_length=100)
    items: List[TaxGroupItemInput] = Field(default_factory=list)

    @field_validator('code')
    @classmethod
    def validate_code(cls, v):
        if not v or not v.strip():
            raise ValueError('Group code is required')
        return v.strip().upper()


class UpdateTaxGroupRequest(BaseModel):
    code: Optional[str] = Field(None, max_length=20)
    name: Optional[str] = Field(None, max_length=100)
    is_active: Optional[bool] = None
    items: Optional[List[TaxGroupItemInput]] = None

    @field_validator('code')
    @classmethod
    def validate_code(cls, v):
        if v is not None:
            return v.strip().upper()
        return v


class TaxGroupItemDetail(BaseModel):
    id: str
    tax_group_id: str
    tax_code_id: str
    tax_name: str
    rate: float
    sequence: int


class TaxGroupListItem(BaseModel):
    id: str
    code: str
    name: str
    is_active: bool
    items: List[TaxGroupItemDetail] = []
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class TaxGroupListResponse(BaseModel):
    items: List[TaxGroupListItem]
    total: int


class TaxGroupResponse(BaseModel):
    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None
