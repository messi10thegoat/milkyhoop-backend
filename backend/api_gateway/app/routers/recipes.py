"""
Router for Recipe Management (Manajemen Resep F&B)
"""
from decimal import Decimal
from typing import Optional
from uuid import UUID

import asyncpg
from fastapi import APIRouter, HTTPException, Query, Request

from ..config import settings
from ..schemas.recipes import (
    CreateMenuCategoryRequest,
    CreateMenuItemRequest,
    CreateModifierGroupRequest,
    CreateRecipeRequest,
    MenuCategoryListResponse,
    MenuItemDetailResponse,
    MenuItemListResponse,
    ModifierGroupListResponse,
    ModifierOptionInput,
    RecipeCostingResponse,
    RecipeDetailResponse,
    RecipeIngredientInput,
    RecipeInstructionInput,
    RecipeListResponse,
    RecipeResponse,
    UpdateMenuCategoryRequest,
    UpdateMenuItemRequest,
    UpdateRecipeRequest,
)

router = APIRouter()

_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        db_config = settings.get_db_config()
        _pool = await asyncpg.create_pool(
            **db_config,
            min_size=2,
            max_size=10,
            command_timeout=60
        )
    return _pool


def get_user_context(request: Request) -> dict:
    if not hasattr(request.state, 'user') or not request.state.user:
        raise HTTPException(status_code=401, detail="Authentication required")
    user = request.state.user
    tenant_id = user.get("tenant_id")
    user_id = user.get("user_id")
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Invalid user context")
    return {"tenant_id": tenant_id, "user_id": UUID(user_id) if user_id else None}


# =============================================================================
# HEALTH CHECK
# =============================================================================

@router.get("/health")
async def health_check():
    return {"status": "healthy", "service": "recipes"}


# =============================================================================
# MENU CATEGORIES
# =============================================================================

@router.get("/categories", response_model=MenuCategoryListResponse)
async def list_menu_categories(
    request: Request,
    is_active: Optional[bool] = None,
    parent_id: Optional[UUID] = None
):
    """List menu categories."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

        where_clauses = ["mc.tenant_id = $1"]
        params = [ctx["tenant_id"]]
        param_idx = 2

        if is_active is not None:
            where_clauses.append(f"mc.is_active = ${param_idx}")
            params.append(is_active)
            param_idx += 1

        if parent_id:
            where_clauses.append(f"mc.parent_category_id = ${param_idx}")
            params.append(parent_id)
            param_idx += 1

        where_sql = " AND ".join(where_clauses)

        rows = await conn.fetch(f"""
            SELECT
                mc.id, mc.id as code_placeholder, mc.name, mc.description,
                mc.parent_category_id, pc.name as parent_name,
                mc.display_order, mc.is_active,
                (SELECT COUNT(*) FROM menu_items mi WHERE mi.category_id = mc.id) as item_count
            FROM menu_categories mc
            LEFT JOIN menu_categories pc ON pc.id = mc.parent_category_id
            WHERE {where_sql}
            ORDER BY mc.display_order, mc.name
        """, *params)

        items = [{
            "id": str(r["id"]),
            "code": r["code"],
            "name": r["name"],
            "description": r["description"],
            "parent_category_id": str(r["parent_category_id"]) if r["parent_category_id"] else None,
            "parent_name": r["parent_name"],
            "display_order": r["display_order"],
            "is_active": r["is_active"],
            "item_count": r["item_count"]
        } for r in rows]

        return MenuCategoryListResponse(items=items, total=len(items))


@router.post("/categories", response_model=RecipeResponse)
async def create_menu_category(
    request: Request,
    data: CreateMenuCategoryRequest
):
    """Create a menu category."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

        # Check duplicate code
        exists = await conn.fetchval(
            "SELECT 1 FROM menu_categories WHERE tenant_id = $1 AND code = $2",
            ctx["tenant_id"], data.code
        )
        if exists:
            raise HTTPException(status_code=400, detail=f"Category code {data.code} already exists")

        row = await conn.fetchrow("""
            INSERT INTO menu_categories (
                tenant_id, code, name, description,
                parent_category_id, display_order, is_active, created_by
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            RETURNING id
        """,
            ctx["tenant_id"], data.code, data.name, data.description,
            data.parent_category_id, data.display_order, data.is_active,
            ctx["user_id"]
        )

        return RecipeResponse(
            success=True,
            message="Menu category created",
            data={"id": str(row["id"])}
        )


@router.put("/categories/{category_id}", response_model=RecipeResponse)
async def update_menu_category(
    request: Request,
    category_id: UUID,
    data: UpdateMenuCategoryRequest
):
    """Update a menu category."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

        existing = await conn.fetchrow(
            "SELECT id FROM menu_categories WHERE id = $1 AND tenant_id = $2",
            category_id, ctx["tenant_id"]
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Category not found")

        updates = []
        params = []
        param_idx = 1

        for field, value in data.model_dump(exclude_unset=True).items():
            updates.append(f"{field} = ${param_idx}")
            params.append(value)
            param_idx += 1

        if not updates:
            raise HTTPException(status_code=400, detail="No fields to update")

        params.append(category_id)
        await conn.execute(f"""
            UPDATE menu_categories
            SET {', '.join(updates)}, updated_at = NOW()
            WHERE id = ${param_idx}
        """, *params)

        return RecipeResponse(success=True, message="Category updated")


# =============================================================================
# RECIPES
# =============================================================================

@router.get("", response_model=RecipeListResponse)
async def list_recipes_root(
    request: Request,
    category: Optional[str] = None,
    search: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0)
):
    """List recipes (root endpoint)."""
    return await list_recipes_internal(request, category, search, status, limit, offset)


@router.get("/recipes", response_model=RecipeListResponse)
async def list_recipes(
    request: Request,
    category: Optional[str] = None,
    search: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0)
):
    """List recipes."""
    return await list_recipes_internal(request, category, search, status, limit, offset)


async def list_recipes_internal(
    request: Request,
    category: Optional[str] = None,
    search: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0
):
    """Internal function to list recipes."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

        where_clauses = ["r.tenant_id = $1"]
        params = [ctx["tenant_id"]]
        param_idx = 2

        if category:
            where_clauses.append(f"r.category = ${param_idx}")
            params.append(category)
            param_idx += 1

        if search:
            where_clauses.append(f"(r.recipe_name ILIKE ${param_idx} OR r.recipe_code ILIKE ${param_idx})")
            params.append(f"%{search}%")
            param_idx += 1

        if status:
            where_clauses.append(f"r.status = ${param_idx}")
            params.append(status)
            param_idx += 1

        where_sql = " AND ".join(where_clauses)

        total = await conn.fetchval(
            f"SELECT COUNT(*) FROM recipes r WHERE {where_sql}", *params
        )

        params.extend([limit, offset])
        rows = await conn.fetch(f"""
            SELECT
                r.id,
                r.recipe_code as code,
                r.recipe_name as name,
                r.category as category_name,
                r.yield_quantity as output_quantity,
                r.yield_unit as output_unit,
                r.prep_time_minutes,
                r.cook_time_minutes,
                r.total_time_minutes,
                r.status,
                (SELECT COUNT(*) FROM recipe_ingredients ri WHERE ri.recipe_id = r.id) as ingredient_count,
                COALESCE(r.ingredient_cost, 0) as total_cost
            FROM recipes r
            WHERE {where_sql}
            ORDER BY r.recipe_name
            LIMIT ${param_idx} OFFSET ${param_idx + 1}
        """, *params)

        items = [{
            "id": str(r["id"]),
            "code": r["code"],
            "name": r["name"],
            "category_name": r["category_name"],
            "output_quantity": float(r["output_quantity"]) if r["output_quantity"] else None,
            "output_unit": r["output_unit"],
            "prep_time_minutes": r["prep_time_minutes"],
            "cook_time_minutes": r["cook_time_minutes"],
            "total_time_minutes": r["total_time_minutes"],
            "difficulty_level": None,
            "ingredient_count": r["ingredient_count"],
            "total_cost": r["total_cost"],
            "is_active": r["status"] == "active"
        } for r in rows]

        return RecipeListResponse(
            items=items,
            total=total,
            has_more=(offset + len(items)) < total
        )


@router.get("/recipes/{recipe_id}", response_model=RecipeDetailResponse)
async def get_recipe(request: Request, recipe_id: UUID):
    """Get recipe details with ingredients and instructions."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

        recipe = await conn.fetchrow("""
            SELECT
                r.*, mc.name as category_name
            FROM recipes r
            LEFT JOIN menu_categories mc ON mc.id = r.category_id
            WHERE r.id = $1 AND r.tenant_id = $2
        """, recipe_id, ctx["tenant_id"])

        if not recipe:
            raise HTTPException(status_code=404, detail="Recipe not found")

        # Get ingredients
        ingredients = await conn.fetch("""
            SELECT
                ri.id, ri.product_id, p.nama_produk as product_name, p.code as product_code,
                ri.quantity, ri.unit, p.unit_cost,
                (ri.quantity * p.unit_cost)::BIGINT as line_cost,
                ri.is_optional, ri.notes
            FROM recipe_ingredients ri
            JOIN products p ON p.id = ri.product_id
            WHERE ri.recipe_id = $1
            ORDER BY ri.id
        """, recipe_id)

        # Get instructions
        instructions = await conn.fetch("""
            SELECT id, step_number, instruction, duration_minutes, temperature, notes
            FROM recipe_instructions
            WHERE recipe_id = $1
            ORDER BY step_number
        """, recipe_id)

        total_cost = sum(i["line_cost"] for i in ingredients)
        output_qty = float(recipe["output_quantity"]) if recipe["output_quantity"] else 1.0
        cost_per_portion = int(total_cost / output_qty)

        return RecipeDetailResponse(
            success=True,
            id=str(recipe["id"]),
            code=recipe["code"],
            name=recipe["name"],
            description=recipe["description"],
            category_id=str(recipe["category_id"]) if recipe["category_id"] else None,
            category_name=recipe["category_name"],
            output_quantity=recipe["output_quantity"],
            output_unit=recipe["output_unit"],
            prep_time_minutes=recipe["prep_time_minutes"],
            cook_time_minutes=recipe["cook_time_minutes"],
            total_time_minutes=recipe["prep_time_minutes"] + recipe["cook_time_minutes"],
            difficulty_level=recipe["difficulty_level"],
            ingredients=[{
                "id": str(i["id"]),
                "product_id": str(i["product_id"]),
                "product_name": i["product_name"],
                "product_code": i["product_code"],
                "quantity": i["quantity"],
                "unit": i["unit"],
                "unit_cost": i["unit_cost"],
                "line_cost": i["line_cost"],
                "is_optional": i["is_optional"],
                "notes": i["notes"]
            } for i in ingredients],
            instructions=[{
                "id": str(i["id"]),
                "step_number": i["step_number"],
                "instruction": i["instruction"],
                "duration_minutes": i["duration_minutes"],
                "temperature": i["temperature"],
                "notes": i["notes"]
            } for i in instructions],
            total_cost=total_cost,
            cost_per_portion=cost_per_portion,
            is_active=recipe["is_active"],
            created_at=recipe["created_at"]
        )


@router.post("/recipes", response_model=RecipeResponse)
async def create_recipe(request: Request, data: CreateRecipeRequest):
    """Create a new recipe with ingredients and instructions."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

        async with conn.transaction():
            # Check duplicate code
            exists = await conn.fetchval(
                "SELECT 1 FROM recipes WHERE tenant_id = $1 AND code = $2",
                ctx["tenant_id"], data.code
            )
            if exists:
                raise HTTPException(status_code=400, detail=f"Recipe code {data.code} already exists")

            # Create recipe
            recipe = await conn.fetchrow("""
                INSERT INTO recipes (
                    tenant_id, code, name, description, category_id,
                    output_quantity, output_unit, prep_time_minutes,
                    cook_time_minutes, difficulty_level, created_by
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                RETURNING id
            """,
                ctx["tenant_id"], data.code, data.name, data.description,
                data.category_id, data.output_quantity, data.output_unit,
                data.prep_time_minutes, data.cook_time_minutes,
                data.difficulty_level, ctx["user_id"]
            )

            recipe_id = recipe["id"]

            # Add ingredients
            for ing in data.ingredients:
                await conn.execute("""
                    INSERT INTO recipe_ingredients (
                        recipe_id, product_id, quantity, unit, is_optional, notes
                    )
                    VALUES ($1, $2, $3, $4, $5, $6)
                """,
                    recipe_id, ing.product_id, ing.quantity,
                    ing.unit, ing.is_optional, ing.notes
                )

            # Add instructions
            for inst in data.instructions:
                await conn.execute("""
                    INSERT INTO recipe_instructions (
                        recipe_id, step_number, instruction,
                        duration_minutes, temperature, notes
                    )
                    VALUES ($1, $2, $3, $4, $5, $6)
                """,
                    recipe_id, inst.step_number, inst.instruction,
                    inst.duration_minutes, inst.temperature, inst.notes
                )

            return RecipeResponse(
                success=True,
                message="Recipe created",
                data={"id": str(recipe_id)}
            )


@router.put("/recipes/{recipe_id}", response_model=RecipeResponse)
async def update_recipe(
    request: Request,
    recipe_id: UUID,
    data: UpdateRecipeRequest
):
    """Update recipe details."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

        existing = await conn.fetchrow(
            "SELECT id FROM recipes WHERE id = $1 AND tenant_id = $2",
            recipe_id, ctx["tenant_id"]
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Recipe not found")

        updates = []
        params = []
        param_idx = 1

        for field, value in data.model_dump(exclude_unset=True).items():
            updates.append(f"{field} = ${param_idx}")
            params.append(value)
            param_idx += 1

        if not updates:
            raise HTTPException(status_code=400, detail="No fields to update")

        params.append(recipe_id)
        await conn.execute(f"""
            UPDATE recipes
            SET {', '.join(updates)}, updated_at = NOW()
            WHERE id = ${param_idx}
        """, *params)

        return RecipeResponse(success=True, message="Recipe updated")


@router.post("/recipes/{recipe_id}/ingredients", response_model=RecipeResponse)
async def add_recipe_ingredient(
    request: Request,
    recipe_id: UUID,
    data: RecipeIngredientInput
):
    """Add ingredient to recipe."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

        recipe = await conn.fetchrow(
            "SELECT id FROM recipes WHERE id = $1 AND tenant_id = $2",
            recipe_id, ctx["tenant_id"]
        )
        if not recipe:
            raise HTTPException(status_code=404, detail="Recipe not found")

        row = await conn.fetchrow("""
            INSERT INTO recipe_ingredients (
                recipe_id, product_id, quantity, unit, is_optional, notes
            )
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING id
        """,
            recipe_id, data.product_id, data.quantity,
            data.unit, data.is_optional, data.notes
        )

        return RecipeResponse(
            success=True,
            message="Ingredient added",
            data={"id": str(row["id"])}
        )


@router.delete("/recipes/{recipe_id}/ingredients/{ingredient_id}", response_model=RecipeResponse)
async def remove_recipe_ingredient(
    request: Request,
    recipe_id: UUID,
    ingredient_id: UUID
):
    """Remove ingredient from recipe."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

        deleted = await conn.fetchval("""
            DELETE FROM recipe_ingredients
            WHERE id = $1 AND recipe_id = $2
            AND recipe_id IN (SELECT id FROM recipes WHERE tenant_id = $3)
            RETURNING id
        """, ingredient_id, recipe_id, ctx["tenant_id"])

        if not deleted:
            raise HTTPException(status_code=404, detail="Ingredient not found")

        return RecipeResponse(success=True, message="Ingredient removed")


@router.post("/recipes/{recipe_id}/instructions", response_model=RecipeResponse)
async def add_recipe_instruction(
    request: Request,
    recipe_id: UUID,
    data: RecipeInstructionInput
):
    """Add instruction to recipe."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

        recipe = await conn.fetchrow(
            "SELECT id FROM recipes WHERE id = $1 AND tenant_id = $2",
            recipe_id, ctx["tenant_id"]
        )
        if not recipe:
            raise HTTPException(status_code=404, detail="Recipe not found")

        row = await conn.fetchrow("""
            INSERT INTO recipe_instructions (
                recipe_id, step_number, instruction,
                duration_minutes, temperature, notes
            )
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING id
        """,
            recipe_id, data.step_number, data.instruction,
            data.duration_minutes, data.temperature, data.notes
        )

        return RecipeResponse(
            success=True,
            message="Instruction added",
            data={"id": str(row["id"])}
        )


# =============================================================================
# RECIPE COSTING
# =============================================================================

@router.get("/recipes/{recipe_id}/costing", response_model=RecipeCostingResponse)
async def get_recipe_costing(request: Request, recipe_id: UUID):
    """Get detailed cost breakdown for a recipe."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

        recipe = await conn.fetchrow("""
            SELECT id, name, output_quantity
            FROM recipes
            WHERE id = $1 AND tenant_id = $2
        """, recipe_id, ctx["tenant_id"])

        if not recipe:
            raise HTTPException(status_code=404, detail="Recipe not found")

        ingredients = await conn.fetch("""
            SELECT
                p.nama_produk as ingredient_name,
                ri.quantity, ri.unit, p.unit_cost,
                (ri.quantity * p.unit_cost)::BIGINT as line_cost
            FROM recipe_ingredients ri
            JOIN products p ON p.id = ri.product_id
            WHERE ri.recipe_id = $1
        """, recipe_id)

        total_cost = sum(i["line_cost"] for i in ingredients)
        output_qty = float(recipe["output_quantity"]) if recipe["output_quantity"] else 1.0
        cost_per_portion = int(total_cost / output_qty)

        breakdown = []
        for ing in ingredients:
            cost_percent = Decimal(str(ing["line_cost"])) / Decimal(str(total_cost)) * 100 if total_cost > 0 else Decimal("0")
            breakdown.append({
                "ingredient_name": ing["ingredient_name"],
                "quantity": ing["quantity"],
                "unit": ing["unit"],
                "unit_cost": ing["unit_cost"],
                "line_cost": ing["line_cost"],
                "cost_percent": round(cost_percent, 2)
            })

        # Calculate suggested prices for different food cost targets
        suggested_30 = int(cost_per_portion / 0.30) if cost_per_portion > 0 else 0
        suggested_25 = int(cost_per_portion / 0.25) if cost_per_portion > 0 else 0

        return RecipeCostingResponse(
            success=True,
            recipe_id=str(recipe_id),
            recipe_name=recipe["name"],
            output_quantity=recipe["output_quantity"],
            ingredients=breakdown,
            total_cost=total_cost,
            cost_per_portion=cost_per_portion,
            suggested_price_30_percent=suggested_30,
            suggested_price_25_percent=suggested_25
        )


# =============================================================================
# MODIFIER GROUPS
# =============================================================================

@router.get("/modifiers", response_model=ModifierGroupListResponse)
async def list_modifier_groups(request: Request):
    """List modifier groups."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

        groups = await conn.fetch("""
            SELECT id, code, name, selection_type,
                   min_selections, max_selections, is_required
            FROM recipe_modifier_groups
            WHERE tenant_id = $1
            ORDER BY name
        """, ctx["tenant_id"])

        items = []
        for g in groups:
            options = await conn.fetch("""
                SELECT id, name, price_adjustment, is_default, is_available
                FROM recipe_modifier_options
                WHERE modifier_group_id = $1
                ORDER BY display_order
            """, g["id"])

            items.append({
                "id": str(g["id"]),
                "code": g["code"],
                "name": g["name"],
                "selection_type": g["selection_type"],
                "min_selections": g["min_selections"],
                "max_selections": g["max_selections"],
                "is_required": g["is_required"],
                "options": [dict(o) for o in options]
            })

        return ModifierGroupListResponse(items=items, total=len(items))


@router.post("/modifiers", response_model=RecipeResponse)
async def create_modifier_group(
    request: Request,
    data: CreateModifierGroupRequest
):
    """Create a modifier group."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

        row = await conn.fetchrow("""
            INSERT INTO recipe_modifier_groups (
                tenant_id, code, name, selection_type,
                min_selections, max_selections, is_required, created_by
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            RETURNING id
        """,
            ctx["tenant_id"], data.code, data.name, data.selection_type,
            data.min_selections, data.max_selections, data.is_required,
            ctx["user_id"]
        )

        return RecipeResponse(
            success=True,
            message="Modifier group created",
            data={"id": str(row["id"])}
        )


@router.post("/modifiers/{group_id}/options", response_model=RecipeResponse)
async def add_modifier_option(
    request: Request,
    group_id: UUID,
    data: ModifierOptionInput
):
    """Add option to modifier group."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

        group = await conn.fetchrow(
            "SELECT id FROM recipe_modifier_groups WHERE id = $1 AND tenant_id = $2",
            group_id, ctx["tenant_id"]
        )
        if not group:
            raise HTTPException(status_code=404, detail="Modifier group not found")

        # Get next display order
        max_order = await conn.fetchval("""
            SELECT COALESCE(MAX(display_order), 0)
            FROM recipe_modifier_options
            WHERE modifier_group_id = $1
        """, group_id)

        row = await conn.fetchrow("""
            INSERT INTO recipe_modifier_options (
                modifier_group_id, name, price_adjustment,
                is_default, is_available, display_order
            )
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING id
        """,
            group_id, data.name, data.price_adjustment,
            data.is_default, data.is_available, max_order + 1
        )

        return RecipeResponse(
            success=True,
            message="Modifier option added",
            data={"id": str(row["id"])}
        )


# =============================================================================
# MENU ITEMS
# =============================================================================

@router.get("/menu-items", response_model=MenuItemListResponse)
async def list_menu_items(
    request: Request,
    category_id: Optional[UUID] = None,
    search: Optional[str] = None,
    is_available: Optional[bool] = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0)
):
    """List menu items."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

        where_clauses = ["mi.tenant_id = $1"]
        params = [ctx["tenant_id"]]
        param_idx = 2

        if category_id:
            where_clauses.append(f"mi.category_id = ${param_idx}")
            params.append(category_id)
            param_idx += 1

        if search:
            where_clauses.append(f"(mi.name ILIKE ${param_idx} OR mi.code ILIKE ${param_idx})")
            params.append(f"%{search}%")
            param_idx += 1

        if is_available is not None:
            where_clauses.append(f"mi.is_available = ${param_idx}")
            params.append(is_available)
            param_idx += 1

        where_sql = " AND ".join(where_clauses)

        total = await conn.fetchval(
            f"SELECT COUNT(*) FROM menu_items mi WHERE {where_sql}", *params
        )

        params.extend([limit, offset])
        rows = await conn.fetch(f"""
            SELECT
                mi.id, mi.code, mi.name, mi.description,
                mc.name as category_name, mi.base_price,
                (mi.base_price * (1 + mi.tax_rate))::BIGINT as price_with_tax,
                r.name as recipe_name,
                COALESCE((SELECT SUM(ri.quantity * p.unit_cost)::BIGINT / NULLIF(r.output_quantity, 0)
                          FROM recipe_ingredients ri
                          JOIN products p ON p.id = ri.product_id
                          WHERE ri.recipe_id = mi.recipe_id), 0) as food_cost,
                mi.is_available, mi.display_order
            FROM menu_items mi
            LEFT JOIN menu_categories mc ON mc.id = mi.category_id
            LEFT JOIN recipes r ON r.id = mi.recipe_id
            WHERE {where_sql}
            ORDER BY mi.display_order, mi.name
            LIMIT ${param_idx} OFFSET ${param_idx + 1}
        """, *params)

        items = []
        for r in rows:
            food_cost = r["food_cost"] or 0
            base_price = r["base_price"] or 0
            food_cost_percent = Decimal(str(food_cost)) / Decimal(str(base_price)) * 100 if base_price > 0 else Decimal("0")

            items.append({
                "id": str(r["id"]),
                "code": r["code"],
                "name": r["name"],
                "description": r["description"],
                "category_name": r["category_name"],
                "base_price": r["base_price"],
                "price_with_tax": r["price_with_tax"],
                "recipe_name": r["recipe_name"],
                "food_cost": food_cost,
                "food_cost_percent": round(food_cost_percent, 2),
                "is_available": r["is_available"],
                "display_order": r["display_order"]
            })

        return MenuItemListResponse(
            items=items,
            total=total,
            has_more=(offset + len(items)) < total
        )


@router.post("/menu-items", response_model=RecipeResponse)
async def create_menu_item(request: Request, data: CreateMenuItemRequest):
    """Create a menu item."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

        async with conn.transaction():
            # Check duplicate code
            exists = await conn.fetchval(
                "SELECT 1 FROM menu_items WHERE tenant_id = $1 AND code = $2",
                ctx["tenant_id"], data.code
            )
            if exists:
                raise HTTPException(status_code=400, detail=f"Menu item code {data.code} already exists")

            row = await conn.fetchrow("""
                INSERT INTO menu_items (
                    tenant_id, code, name, description, category_id,
                    recipe_id, base_price, tax_rate, is_taxable,
                    display_order, image_url, created_by
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                RETURNING id
            """,
                ctx["tenant_id"], data.code, data.name, data.description,
                data.category_id, data.recipe_id, data.base_price,
                data.tax_rate, data.is_taxable, data.display_order,
                data.image_url, ctx["user_id"]
            )

            menu_item_id = row["id"]

            # Link modifier groups
            for group_id in data.modifier_group_ids:
                await conn.execute("""
                    INSERT INTO menu_item_modifiers (menu_item_id, modifier_group_id)
                    VALUES ($1, $2)
                    ON CONFLICT DO NOTHING
                """, menu_item_id, group_id)

            return RecipeResponse(
                success=True,
                message="Menu item created",
                data={"id": str(menu_item_id)}
            )


@router.get("/menu-items/{item_id}", response_model=MenuItemDetailResponse)
async def get_menu_item(request: Request, item_id: UUID):
    """Get menu item details."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

        item = await conn.fetchrow("""
            SELECT
                mi.*, mc.name as category_name, r.name as recipe_name
            FROM menu_items mi
            LEFT JOIN menu_categories mc ON mc.id = mi.category_id
            LEFT JOIN recipes r ON r.id = mi.recipe_id
            WHERE mi.id = $1 AND mi.tenant_id = $2
        """, item_id, ctx["tenant_id"])

        if not item:
            raise HTTPException(status_code=404, detail="Menu item not found")

        # Calculate food cost
        food_cost = 0
        if item["recipe_id"]:
            food_cost = await conn.fetchval("""
                SELECT COALESCE(SUM(ri.quantity * p.unit_cost)::BIGINT / NULLIF(r.output_quantity, 0), 0)
                FROM recipe_ingredients ri
                JOIN products p ON p.id = ri.product_id
                JOIN recipes r ON r.id = ri.recipe_id
                WHERE ri.recipe_id = $1
            """, item["recipe_id"]) or 0

        # Get modifier groups
        modifier_groups = await conn.fetch("""
            SELECT rmg.id, rmg.code, rmg.name, rmg.selection_type,
                   rmg.min_selections, rmg.max_selections, rmg.is_required
            FROM recipe_modifier_groups rmg
            JOIN menu_item_modifiers mim ON mim.modifier_group_id = rmg.id
            WHERE mim.menu_item_id = $1
        """, item_id)

        groups_with_options = []
        for g in modifier_groups:
            options = await conn.fetch("""
                SELECT id, name, price_adjustment, is_default, is_available
                FROM recipe_modifier_options
                WHERE modifier_group_id = $1
                ORDER BY display_order
            """, g["id"])

            groups_with_options.append({
                "id": str(g["id"]),
                "code": g["code"],
                "name": g["name"],
                "selection_type": g["selection_type"],
                "min_selections": g["min_selections"],
                "max_selections": g["max_selections"],
                "is_required": g["is_required"],
                "options": [dict(o) for o in options]
            })

        base_price = item["base_price"] or 0
        price_with_tax = int(base_price * (1 + float(item["tax_rate"])))
        food_cost_percent = Decimal(str(food_cost)) / Decimal(str(base_price)) * 100 if base_price > 0 else Decimal("0")
        gross_margin = base_price - food_cost

        return MenuItemDetailResponse(
            success=True,
            id=str(item["id"]),
            code=item["code"],
            name=item["name"],
            description=item["description"],
            category_id=str(item["category_id"]) if item["category_id"] else None,
            category_name=item["category_name"],
            recipe_id=str(item["recipe_id"]) if item["recipe_id"] else None,
            recipe_name=item["recipe_name"],
            base_price=base_price,
            tax_rate=item["tax_rate"],
            price_with_tax=price_with_tax,
            is_taxable=item["is_taxable"],
            food_cost=food_cost,
            food_cost_percent=round(food_cost_percent, 2),
            gross_margin=gross_margin,
            modifier_groups=groups_with_options,
            is_available=item["is_available"],
            image_url=item["image_url"]
        )


@router.put("/menu-items/{item_id}", response_model=RecipeResponse)
async def update_menu_item(
    request: Request,
    item_id: UUID,
    data: UpdateMenuItemRequest
):
    """Update menu item."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

        existing = await conn.fetchrow(
            "SELECT id FROM menu_items WHERE id = $1 AND tenant_id = $2",
            item_id, ctx["tenant_id"]
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Menu item not found")

        async with conn.transaction():
            # Handle modifier groups separately
            modifier_group_ids = data.modifier_group_ids
            update_data = data.model_dump(exclude_unset=True, exclude={"modifier_group_ids"})

            if update_data:
                updates = []
                params = []
                param_idx = 1

                for field, value in update_data.items():
                    updates.append(f"{field} = ${param_idx}")
                    params.append(value)
                    param_idx += 1

                params.append(item_id)
                await conn.execute(f"""
                    UPDATE menu_items
                    SET {', '.join(updates)}, updated_at = NOW()
                    WHERE id = ${param_idx}
                """, *params)

            # Update modifier groups if provided
            if modifier_group_ids is not None:
                await conn.execute(
                    "DELETE FROM menu_item_modifiers WHERE menu_item_id = $1",
                    item_id
                )
                for group_id in modifier_group_ids:
                    await conn.execute("""
                        INSERT INTO menu_item_modifiers (menu_item_id, modifier_group_id)
                        VALUES ($1, $2)
                    """, item_id, group_id)

        return RecipeResponse(success=True, message="Menu item updated")
