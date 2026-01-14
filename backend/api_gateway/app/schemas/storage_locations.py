"""
Pydantic schemas for Storage Locations module.
"""

from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Dict, Any, Literal


class CreateStorageLocationRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=50)
    name: str = Field(..., min_length=1, max_length=255)
    parent_id: Optional[str] = None
    location_type: Literal["warehouse", "zone", "rack", "bin", "shelf", "other"] = "bin"
    address: Optional[str] = None
    capacity_info: Optional[str] = None
    temperature_range: Optional[str] = Field(None, max_length=50)
    description: Optional[str] = None
    is_default: bool = False

    @field_validator('code')
    @classmethod
    def validate_code(cls, v):
        return v.strip().upper() if v else v


class UpdateStorageLocationRequest(BaseModel):
    code: Optional[str] = Field(None, max_length=50)
    name: Optional[str] = Field(None, max_length=255)
    parent_id: Optional[str] = None
    location_type: Optional[Literal["warehouse", "zone", "rack", "bin", "shelf", "other"]] = None
    address: Optional[str] = None
    capacity_info: Optional[str] = None
    temperature_range: Optional[str] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None
    is_default: Optional[bool] = None


class StorageLocationListItem(BaseModel):
    id: str
    code: str
    name: str
    location_type: str
    parent_id: Optional[str] = None
    is_active: bool
    is_default: bool


class StorageLocationListResponse(BaseModel):
    items: List[StorageLocationListItem]
    total: int
    has_more: bool


class StorageLocationTreeItem(BaseModel):
    id: str
    code: str
    name: str
    location_type: str
    is_active: bool
    children: List["StorageLocationTreeItem"] = []


class StorageLocationTreeResponse(BaseModel):
    items: List[StorageLocationTreeItem]


class StorageLocationDetail(BaseModel):
    id: str
    code: str
    name: str
    location_type: str
    parent_id: Optional[str] = None
    parent_name: Optional[str] = None
    address: Optional[str] = None
    capacity_info: Optional[str] = None
    temperature_range: Optional[str] = None
    description: Optional[str] = None
    is_active: bool
    is_default: bool
    created_at: str
    updated_at: str


class StorageLocationDetailResponse(BaseModel):
    success: bool = True
    data: StorageLocationDetail


class StorageLocationResponse(BaseModel):
    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None


class StorageLocationDropdownItem(BaseModel):
    id: str
    code: str
    name: str
    location_type: str
    full_name: str


class StorageLocationDropdownResponse(BaseModel):
    items: List[StorageLocationDropdownItem]
