"""
Schemas for Recipe Management (Manajemen Resep F&B)
"""
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field


# =============================================================================
# MENU CATEGORIES
# =============================================================================

class CreateMenuCategoryRequest(BaseModel):
    code: str = Field(..., max_length=50)
    name: str = Field(..., max_length=100)
    description: Optional[str] = None
    parent_category_id: Optional[UUID] = None
    display_order: int = 0
    is_active: bool = True


class UpdateMenuCategoryRequest(BaseModel):
    name: Optional[str] = Field(None, max_length=100)
    description: Optional[str] = None
    parent_category_id: Optional[UUID] = None
    display_order: Optional[int] = None
    is_active: Optional[bool] = None


class MenuCategoryItem(BaseModel):
    id: str
    code: str
    name: str
    description: Optional[str]
    parent_category_id: Optional[str]
    parent_name: Optional[str]
    display_order: int
    is_active: bool
    item_count: int


class MenuCategoryListResponse(BaseModel):
    items: List[MenuCategoryItem]
    total: int


# =============================================================================
# RECIPES
# =============================================================================

class RecipeIngredientInput(BaseModel):
    product_id: UUID
    quantity: Decimal = Field(..., gt=0)
    unit: str = Field(..., max_length=50)
    is_optional: bool = False
    notes: Optional[str] = None


class RecipeInstructionInput(BaseModel):
    step_number: int = Field(..., ge=1)
    instruction: str
    duration_minutes: Optional[int] = None
    temperature: Optional[str] = None
    notes: Optional[str] = None


class CreateRecipeRequest(BaseModel):
    code: str = Field(..., max_length=50)
    name: str = Field(..., max_length=200)
    description: Optional[str] = None
    category_id: Optional[UUID] = None
    output_quantity: Decimal = Field(Decimal("1"), gt=0)
    output_unit: str = Field("portion", max_length=50)
    prep_time_minutes: int = Field(0, ge=0)
    cook_time_minutes: int = Field(0, ge=0)
    difficulty_level: Literal["easy", "medium", "hard"] = "medium"
    ingredients: List[RecipeIngredientInput] = []
    instructions: List[RecipeInstructionInput] = []


class UpdateRecipeRequest(BaseModel):
    name: Optional[str] = Field(None, max_length=200)
    description: Optional[str] = None
    category_id: Optional[UUID] = None
    output_quantity: Optional[Decimal] = None
    output_unit: Optional[str] = None
    prep_time_minutes: Optional[int] = None
    cook_time_minutes: Optional[int] = None
    difficulty_level: Optional[Literal["easy", "medium", "hard"]] = None
    is_active: Optional[bool] = None


class RecipeIngredientItem(BaseModel):
    id: str
    product_id: str
    product_name: str
    product_code: str
    quantity: Decimal
    unit: str
    unit_cost: int
    line_cost: int
    is_optional: bool
    notes: Optional[str]


class RecipeInstructionItem(BaseModel):
    id: str
    step_number: int
    instruction: str
    duration_minutes: Optional[int]
    temperature: Optional[str]
    notes: Optional[str]


class RecipeListItem(BaseModel):
    id: str
    code: str
    name: str
    category_name: Optional[str]
    output_quantity: Decimal
    output_unit: str
    prep_time_minutes: int
    cook_time_minutes: int
    total_time_minutes: int
    difficulty_level: str
    ingredient_count: int
    total_cost: int
    is_active: bool


class RecipeListResponse(BaseModel):
    items: List[RecipeListItem]
    total: int
    has_more: bool


class RecipeDetailResponse(BaseModel):
    success: bool = True
    id: str
    code: str
    name: str
    description: Optional[str]
    category_id: Optional[str]
    category_name: Optional[str]
    output_quantity: Decimal
    output_unit: str
    prep_time_minutes: int
    cook_time_minutes: int
    total_time_minutes: int
    difficulty_level: str
    ingredients: List[RecipeIngredientItem]
    instructions: List[RecipeInstructionItem]
    total_cost: int
    cost_per_portion: int
    is_active: bool
    created_at: datetime


# =============================================================================
# RECIPE MODIFIERS
# =============================================================================

class CreateModifierGroupRequest(BaseModel):
    code: str = Field(..., max_length=50)
    name: str = Field(..., max_length=100)
    selection_type: Literal["single", "multiple"] = "single"
    min_selections: int = Field(0, ge=0)
    max_selections: int = Field(1, ge=1)
    is_required: bool = False


class ModifierOptionInput(BaseModel):
    name: str = Field(..., max_length=100)
    price_adjustment: int = 0  # Can be positive or negative
    is_default: bool = False
    is_available: bool = True


class ModifierGroupItem(BaseModel):
    id: str
    code: str
    name: str
    selection_type: str
    min_selections: int
    max_selections: int
    is_required: bool
    options: List[Dict[str, Any]]


class ModifierGroupListResponse(BaseModel):
    items: List[ModifierGroupItem]
    total: int


# =============================================================================
# MENU ITEMS
# =============================================================================

class CreateMenuItemRequest(BaseModel):
    code: str = Field(..., max_length=50)
    name: str = Field(..., max_length=200)
    description: Optional[str] = None
    category_id: Optional[UUID] = None
    recipe_id: Optional[UUID] = None
    base_price: int = Field(..., ge=0)
    tax_rate: Decimal = Field(Decimal("0.11"), ge=0, le=1)  # 11% PPN default
    is_taxable: bool = True
    modifier_group_ids: List[UUID] = []
    display_order: int = 0
    image_url: Optional[str] = None


class UpdateMenuItemRequest(BaseModel):
    name: Optional[str] = Field(None, max_length=200)
    description: Optional[str] = None
    category_id: Optional[UUID] = None
    recipe_id: Optional[UUID] = None
    base_price: Optional[int] = None
    tax_rate: Optional[Decimal] = None
    is_taxable: Optional[bool] = None
    modifier_group_ids: Optional[List[UUID]] = None
    display_order: Optional[int] = None
    image_url: Optional[str] = None
    is_available: Optional[bool] = None


class MenuItemListItem(BaseModel):
    id: str
    code: str
    name: str
    description: Optional[str]
    category_name: Optional[str]
    base_price: int
    price_with_tax: int
    recipe_name: Optional[str]
    food_cost: int
    food_cost_percent: Decimal
    is_available: bool
    display_order: int


class MenuItemListResponse(BaseModel):
    items: List[MenuItemListItem]
    total: int
    has_more: bool


class MenuItemDetailResponse(BaseModel):
    success: bool = True
    id: str
    code: str
    name: str
    description: Optional[str]
    category_id: Optional[str]
    category_name: Optional[str]
    recipe_id: Optional[str]
    recipe_name: Optional[str]
    base_price: int
    tax_rate: Decimal
    price_with_tax: int
    is_taxable: bool
    food_cost: int
    food_cost_percent: Decimal
    gross_margin: int
    modifier_groups: List[ModifierGroupItem]
    is_available: bool
    image_url: Optional[str]


# =============================================================================
# RECIPE COSTING
# =============================================================================

class RecipeCostBreakdown(BaseModel):
    ingredient_name: str
    quantity: Decimal
    unit: str
    unit_cost: int
    line_cost: int
    cost_percent: Decimal


class RecipeCostingResponse(BaseModel):
    success: bool = True
    recipe_id: str
    recipe_name: str
    output_quantity: Decimal
    ingredients: List[RecipeCostBreakdown]
    total_cost: int
    cost_per_portion: int
    suggested_price_30_percent: int  # 30% food cost target
    suggested_price_25_percent: int  # 25% food cost target


# =============================================================================
# COMMON
# =============================================================================

class RecipeResponse(BaseModel):
    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None
