"""
Fixed Assets Router
===================
Fixed asset management with depreciation tracking.
Creates journal entries on activate, depreciation, disposal, and sale.

Iron Laws compliance:
- Law 5:  Period checks before journal creation
- Law 13: Advisory locks on all journal-creating paths
- Law 20: DRAFT→lines→UPDATE POSTED pattern
- Law 25: Decimal precision (no float truncation)
- Law 27: resolve_account_id for account resolution
"""
from datetime import date
from decimal import Decimal
from typing import Optional, List
from uuid import UUID
import uuid as uuid_module

import asyncpg
from fastapi import APIRouter, HTTPException, Query, Request

from ..config import settings
from ..schemas.fixed_assets import (
    AssetCategoryCreate,
    AssetCategoryUpdate,
    AssetCategoryResponse,
    AssetCategoryListResponse,
    FixedAssetCreate,
    FixedAssetUpdate,
    FixedAssetResponse,
    FixedAssetDetailResponse,
    FixedAssetListResponse,
    AssetDepreciationResponse,
    DepreciationScheduleResponse,
    AssetMaintenanceCreate,
    AssetMaintenanceResponse,
    ActivateAssetRequest,
    ActivateAssetResponse,
    DisposeAssetRequest,
    DisposeAssetResponse,
    SellAssetRequest,
    SellAssetResponse,
    CalculateDepreciationRequest,
    CalculateDepreciationItem,
    CalculateDepreciationResponse,
    PostDepreciationRequest,
    PostDepreciationResult,
    PostDepreciationResponse,
    AssetRegisterItem,
    AssetRegisterResponse,
    AssetsByCategoryItem,
    AssetsByCategoryResponse,
    AssetsByLocationItem,
    AssetsByLocationResponse,
    MaintenanceDueItem,
    MaintenanceDueResponse,
    AssetStatus,
    DisposalMethod,
    DepreciationStatus,
)
from ..services.resolve_account import resolve_account_id, resolve_accounts_by_codes

router = APIRouter()

_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        db_config = settings.get_db_config()
        _pool = await asyncpg.create_pool(**db_config, min_size=2, max_size=10)
    return _pool


def get_user_context(request: Request) -> dict:
    if not hasattr(request.state, "user"):
        raise HTTPException(status_code=401, detail="Authentication required")
    return {
        "tenant_id": request.state.user["tenant_id"],
        "user_id": request.state.user.get("user_id"),
    }


# Law 5: Period check helper
async def check_period_is_open(conn, tenant_id: str, journal_date) -> None:
    """Check if the accounting period for the given date is open."""
    period = await conn.fetchrow(
        """
        SELECT id, period_name, status FROM fiscal_periods
        WHERE tenant_id = $1 AND $2 BETWEEN start_date AND end_date
        ORDER BY start_date DESC LIMIT 1
        """,
        tenant_id,
        journal_date,
    )
    if period and period["status"] in ("CLOSED", "LOCKED"):
        raise HTTPException(
            status_code=403,
            detail=f"Cannot post to {period['status'].lower()} period ({period['period_name']})",
        )


# ============================================================================
# ASSET CATEGORIES
# ============================================================================

@router.get("/categories", response_model=AssetCategoryListResponse)
async def list_asset_categories(
    request: Request,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    is_active: Optional[bool] = None,
):
    """List asset categories"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        where_clauses = ["ac.tenant_id = $1"]
        params = [ctx["tenant_id"]]
        param_idx = 2

        if is_active is not None:
            where_clauses.append(f"ac.is_active = ${param_idx}")
            params.append(is_active)
            param_idx += 1

        where_sql = " AND ".join(where_clauses)

        total = await conn.fetchval(
            f"SELECT COUNT(*) FROM asset_categories ac WHERE {where_sql}",
            *params
        )

        rows = await conn.fetch(
            f"""
            SELECT ac.*,
                   coa1.account_code as asset_account_code, coa1.name as asset_account_name,
                   coa2.account_code as depreciation_account_code, coa2.name as depreciation_account_name,
                   coa3.account_code as accumulated_depreciation_account_code, coa3.name as accumulated_depreciation_account_name
            FROM asset_categories ac
            LEFT JOIN chart_of_accounts coa1 ON ac.asset_account_id = coa1.id
            LEFT JOIN chart_of_accounts coa2 ON ac.depreciation_account_id = coa2.id
            LEFT JOIN chart_of_accounts coa3 ON ac.accumulated_depreciation_account_id = coa3.id
            WHERE {where_sql}
            ORDER BY ac.name
            OFFSET ${param_idx} LIMIT ${param_idx + 1}
            """,
            *params, skip, limit
        )

        items = [AssetCategoryResponse(**dict(row)) for row in rows]
        return AssetCategoryListResponse(items=items, total=total)


@router.post("/categories", response_model=AssetCategoryResponse, status_code=201)
async def create_asset_category(request: Request, data: AssetCategoryCreate):
    """Create asset category"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        if data.code:
            exists = await conn.fetchval(
                "SELECT 1 FROM asset_categories WHERE tenant_id = $1 AND code = $2",
                ctx["tenant_id"], data.code
            )
            if exists:
                raise HTTPException(status_code=400, detail="Category code already exists")

        row = await conn.fetchrow(
            """
            INSERT INTO asset_categories (
                tenant_id, name, code, depreciation_method, useful_life_months,
                salvage_value_percent, asset_account_id, depreciation_account_id,
                accumulated_depreciation_account_id
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            RETURNING *
            """,
            ctx["tenant_id"], data.name, data.code, data.depreciation_method.value,
            data.useful_life_months, data.salvage_value_percent, data.asset_account_id,
            data.depreciation_account_id, data.accumulated_depreciation_account_id
        )

        return AssetCategoryResponse(**dict(row))


@router.patch("/categories/{category_id}", response_model=AssetCategoryResponse)
async def update_asset_category(request: Request, category_id: UUID, data: AssetCategoryUpdate):
    """Update asset category"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        existing = await conn.fetchrow(
            "SELECT * FROM asset_categories WHERE id = $1 AND tenant_id = $2",
            category_id, ctx["tenant_id"]
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Category not found")

        update_data = data.model_dump(exclude_unset=True)
        if not update_data:
            return AssetCategoryResponse(**dict(existing))

        if "depreciation_method" in update_data:
            update_data["depreciation_method"] = update_data["depreciation_method"].value

        set_clauses = []
        params = []
        for i, (key, value) in enumerate(update_data.items(), start=1):
            set_clauses.append(f"{key} = ${i}")
            params.append(value)

        set_clauses.append("updated_at = NOW()")
        params.extend([category_id, ctx["tenant_id"]])

        row = await conn.fetchrow(
            f"""
            UPDATE asset_categories SET {', '.join(set_clauses)}
            WHERE id = ${len(params) - 1} AND tenant_id = ${len(params)}
            RETURNING *
            """,
            *params
        )

        return AssetCategoryResponse(**dict(row))


@router.delete("/categories/{category_id}")
async def delete_asset_category(request: Request, category_id: UUID):
    """Deactivate asset category"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        # Check for assets using this category
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM fixed_assets WHERE category_id = $1",
            category_id
        )
        if count > 0:
            await conn.execute(
                "UPDATE asset_categories SET is_active = false, updated_at = NOW() WHERE id = $1",
                category_id
            )
            return {"message": "Category deactivated (has assets)"}

        await conn.execute("DELETE FROM asset_categories WHERE id = $1", category_id)
        return {"message": "Category deleted"}


# ============================================================================
# FIXED ASSETS CRUD
# ============================================================================

@router.get("", response_model=FixedAssetListResponse)
async def list_fixed_assets(
    request: Request,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    status: Optional[AssetStatus] = None,
    category_id: Optional[UUID] = None,
    warehouse_id: Optional[UUID] = None,
    search: Optional[str] = None,
):
    """List fixed assets"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        where_clauses = ["fa.tenant_id = $1"]
        params = [ctx["tenant_id"]]
        param_idx = 2

        if status:
            where_clauses.append(f"fa.status = ${param_idx}")
            params.append(status.value)
            param_idx += 1

        if category_id:
            where_clauses.append(f"fa.category_id = ${param_idx}")
            params.append(category_id)
            param_idx += 1

        if warehouse_id:
            where_clauses.append(f"fa.warehouse_id = ${param_idx}")
            params.append(warehouse_id)
            param_idx += 1

        if search:
            words = search.strip().split()
            if len(words) == 1:
                where_clauses.append(f"(fa.asset_number ILIKE ${param_idx} OR fa.name ILIKE ${param_idx})")
                params.append(f"%{words[0]}%")
                param_idx += 1
            else:
                word_conds = []
                for word in words:
                    where_clauses.append(f"(fa.asset_number ILIKE ${param_idx} OR fa.name ILIKE ${param_idx})")
                    params.append(f"%{word}%")
                    param_idx += 1
            param_idx += 1

        where_sql = " AND ".join(where_clauses)

        total = await conn.fetchval(
            f"SELECT COUNT(*) FROM fixed_assets fa WHERE {where_sql}",
            *params
        )

        rows = await conn.fetch(
            f"""
            SELECT fa.*, ac.name as category_name, v.name as vendor_name, w.name as warehouse_name
            FROM fixed_assets fa
            LEFT JOIN asset_categories ac ON fa.category_id = ac.id
            LEFT JOIN vendors v ON fa.vendor_id = v.id
            LEFT JOIN warehouses w ON fa.warehouse_id = w.id
            WHERE {where_sql}
            ORDER BY fa.asset_number
            OFFSET ${param_idx} LIMIT ${param_idx + 1}
            """,
            *params, skip, limit
        )

        items = [FixedAssetResponse(**dict(row)) for row in rows]
        return FixedAssetListResponse(items=items, total=total)


@router.get("/{asset_id}", response_model=FixedAssetDetailResponse)
async def get_fixed_asset(request: Request, asset_id: UUID):
    """Get fixed asset with depreciation and maintenance history"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        fa = await conn.fetchrow(
            """
            SELECT fa.*, ac.name as category_name, v.name as vendor_name, w.name as warehouse_name
            FROM fixed_assets fa
            LEFT JOIN asset_categories ac ON fa.category_id = ac.id
            LEFT JOIN vendors v ON fa.vendor_id = v.id
            LEFT JOIN warehouses w ON fa.warehouse_id = w.id
            WHERE fa.id = $1 AND fa.tenant_id = $2
            """,
            asset_id, ctx["tenant_id"]
        )
        if not fa:
            raise HTTPException(status_code=404, detail="Asset not found")

        depreciations = await conn.fetch(
            """
            SELECT * FROM asset_depreciations WHERE asset_id = $1 ORDER BY depreciation_date
            """,
            asset_id
        )

        maintenance = await conn.fetch(
            """
            SELECT am.*, v.name as vendor_name
            FROM asset_maintenance am
            LEFT JOIN vendors v ON am.vendor_id = v.id
            WHERE am.asset_id = $1 ORDER BY am.maintenance_date DESC
            """,
            asset_id
        )

        return FixedAssetDetailResponse(
            **dict(fa),
            depreciation_history=[AssetDepreciationResponse(**dict(d)) for d in depreciations],
            maintenance_history=[AssetMaintenanceResponse(**dict(m)) for m in maintenance],
        )


@router.post("", response_model=FixedAssetResponse, status_code=201)
async def create_fixed_asset(request: Request, data: FixedAssetCreate):
    """Create fixed asset (draft)"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        # Seed accounts if needed
        await conn.execute("SELECT seed_fixed_asset_accounts($1)", ctx["tenant_id"])

        # Generate asset number
        asset_number = await conn.fetchval(
            "SELECT generate_asset_number($1)",
            ctx["tenant_id"]
        )

        row = await conn.fetchrow(
            """
            INSERT INTO fixed_assets (
                tenant_id, asset_number, name, description, category_id,
                purchase_date, purchase_price, vendor_id, bill_id,
                warehouse_id, location_detail, depreciation_method,
                useful_life_months, salvage_value, depreciation_start_date,
                current_value, asset_account_id, depreciation_account_id,
                accumulated_depreciation_account_id, created_by
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20)
            RETURNING *
            """,
            ctx["tenant_id"], asset_number, data.name, data.description, data.category_id,
            data.purchase_date, data.purchase_price, data.vendor_id, data.bill_id,
            data.warehouse_id, data.location_detail, data.depreciation_method.value,
            data.useful_life_months, data.salvage_value, data.depreciation_start_date,
            data.purchase_price, data.asset_account_id, data.depreciation_account_id,
            data.accumulated_depreciation_account_id, ctx.get("user_id")
        )

        return FixedAssetResponse(**dict(row))


@router.patch("/{asset_id}", response_model=FixedAssetResponse)
async def update_fixed_asset(request: Request, asset_id: UUID, data: FixedAssetUpdate):
    """Update fixed asset (limited fields for active assets)"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        existing = await conn.fetchrow(
            "SELECT * FROM fixed_assets WHERE id = $1 AND tenant_id = $2",
            asset_id, ctx["tenant_id"]
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Asset not found")

        update_data = data.model_dump(exclude_unset=True)
        if not update_data:
            fa = await conn.fetchrow(
                """
                SELECT fa.*, ac.name as category_name, v.name as vendor_name, w.name as warehouse_name
                FROM fixed_assets fa
                LEFT JOIN asset_categories ac ON fa.category_id = ac.id
                LEFT JOIN vendors v ON fa.vendor_id = v.id
                LEFT JOIN warehouses w ON fa.warehouse_id = w.id
                WHERE fa.id = $1
                """,
                asset_id
            )
            return FixedAssetResponse(**dict(fa))

        set_clauses = []
        params = []
        for i, (key, value) in enumerate(update_data.items(), start=1):
            set_clauses.append(f"{key} = ${i}")
            params.append(value)

        set_clauses.append("updated_at = NOW()")
        params.extend([asset_id, ctx["tenant_id"]])

        await conn.execute(
            f"""
            UPDATE fixed_assets SET {', '.join(set_clauses)}
            WHERE id = ${len(params) - 1} AND tenant_id = ${len(params)}
            """,
            *params
        )

        row = await conn.fetchrow(
            """
            SELECT fa.*, ac.name as category_name, v.name as vendor_name, w.name as warehouse_name
            FROM fixed_assets fa
            LEFT JOIN asset_categories ac ON fa.category_id = ac.id
            LEFT JOIN vendors v ON fa.vendor_id = v.id
            LEFT JOIN warehouses w ON fa.warehouse_id = w.id
            WHERE fa.id = $1
            """,
            asset_id
        )

        return FixedAssetResponse(**dict(row))


@router.delete("/{asset_id}")
async def delete_fixed_asset(request: Request, asset_id: UUID):
    """Delete fixed asset (draft only)"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        existing = await conn.fetchrow(
            "SELECT * FROM fixed_assets WHERE id = $1 AND tenant_id = $2",
            asset_id, ctx["tenant_id"]
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Asset not found")

        if existing["status"] != "draft":
            raise HTTPException(status_code=400, detail="Can only delete draft assets")

        await conn.execute("DELETE FROM fixed_assets WHERE id = $1", asset_id)
        return {"message": "Asset deleted"}


# ============================================================================
# ACTIVATE ASSET
# ============================================================================

@router.post("/{asset_id}/activate", response_model=ActivateAssetResponse)
async def activate_fixed_asset(request: Request, asset_id: UUID, data: ActivateAssetRequest):
    """
    Activate asset - creates journal entry and depreciation schedule:
    Dr. Aset Tetap (1-20100)          purchase_price
        Cr. Kas/Bank/Hutang               purchase_price
    """
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        fa = await conn.fetchrow(
            "SELECT * FROM fixed_assets WHERE id = $1 AND tenant_id = $2",
            asset_id, ctx["tenant_id"]
        )
        if not fa:
            raise HTTPException(status_code=404, detail="Asset not found")

        if fa["status"] != "draft":
            raise HTTPException(status_code=400, detail="Asset is already activated")

        # Law 27: resolve accounts via helper
        asset_account = fa["asset_account_id"] or await resolve_account_id(conn, ctx["tenant_id"], '1-20100')

        async with conn.transaction():
            # Law 13: Advisory lock
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"FIXED_ASSET:{asset_id}")

            # Law 5: Period check
            await check_period_is_open(conn, ctx["tenant_id"], fa["purchase_date"])

            # Generate journal number
            seq = await conn.fetchrow(
                """
                INSERT INTO journal_sequences (tenant_id, last_number)
                VALUES ($1, 1)
                ON CONFLICT (tenant_id)
                DO UPDATE SET last_number = journal_sequences.last_number + 1
                RETURNING last_number
                """,
                ctx["tenant_id"]
            )
            journal_number = f"JV-{fa['purchase_date'].year}-{seq['last_number']:05d}"
            journal_id = uuid_module.uuid4()
            purchase_price = Decimal(str(fa["purchase_price"]))

            # Law 20: Step 1 — INSERT as DRAFT
            await conn.execute(
                """
                INSERT INTO journal_entries (
                    id, tenant_id, journal_number, journal_date, description,
                    source_type, source_id, status, total_debit, total_credit, created_by
                ) VALUES ($1, $2, $3, $4, $5, 'FIXED_ASSET', $6, 'DRAFT', $7, $7, $8)
                """,
                journal_id, ctx["tenant_id"], journal_number, fa["purchase_date"],
                f"Asset Purchase - {fa['name']} ({fa['asset_number']})",
                asset_id, purchase_price, ctx.get("user_id")
            )

            # Law 20: Step 2 — INSERT journal_lines
            # Dr. Aset Tetap
            await conn.execute(
                """
                INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo)
                VALUES ($1, $2, 1, $3, $4, 0, $5)
                """,
                uuid_module.uuid4(), journal_id, asset_account, purchase_price,
                f"Asset Purchase - {fa['asset_number']}"
            )

            # Cr. Payment account
            await conn.execute(
                """
                INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo)
                VALUES ($1, $2, 2, $3, 0, $4, $5)
                """,
                uuid_module.uuid4(), journal_id, data.payment_account_id, purchase_price,
                f"Asset Purchase - {fa['asset_number']}"
            )

            # Law 20: Step 3 — UPDATE to POSTED
            await conn.execute("UPDATE journal_entries SET status = 'POSTED' WHERE id = $1", journal_id)

            # Update asset status
            await conn.execute(
                """
                UPDATE fixed_assets SET status = 'active', updated_at = NOW()
                WHERE id = $1
                """,
                asset_id
            )

            # Generate depreciation schedule
            schedule_count = await conn.fetchval(
                "SELECT generate_depreciation_schedule($1)",
                asset_id
            )

            return ActivateAssetResponse(
                asset_id=asset_id,
                asset_number=fa["asset_number"],
                status=AssetStatus.active,
                journal_id=journal_id,
                journal_number=journal_number,
                depreciation_schedule_count=schedule_count,
            )


# ============================================================================
# DEPRECIATION
# ============================================================================

@router.get("/{asset_id}/depreciation-schedule", response_model=DepreciationScheduleResponse)
async def get_depreciation_schedule(request: Request, asset_id: UUID):
    """Get asset depreciation schedule"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        fa = await conn.fetchrow(
            """
            SELECT fa.*, ac.name as category_name
            FROM fixed_assets fa
            LEFT JOIN asset_categories ac ON fa.category_id = ac.id
            WHERE fa.id = $1 AND fa.tenant_id = $2
            """,
            asset_id, ctx["tenant_id"]
        )
        if not fa:
            raise HTTPException(status_code=404, detail="Asset not found")

        schedule = await conn.fetch(
            "SELECT * FROM asset_depreciations WHERE asset_id = $1 ORDER BY depreciation_date",
            asset_id
        )

        scheduled = [s for s in schedule if s["status"] == "scheduled"]
        total_dep = sum(Decimal(str(s["depreciation_amount"])) for s in schedule)

        return DepreciationScheduleResponse(
            asset=FixedAssetResponse(**dict(fa)),
            schedule=[AssetDepreciationResponse(**dict(s)) for s in schedule],
            total_depreciation=total_dep,
            months_remaining=len(scheduled),
        )


@router.get("/depreciation-due", response_model=CalculateDepreciationResponse)
async def get_depreciation_due(
    request: Request,
    year: int = Query(...),
    month: int = Query(..., ge=1, le=12),
):
    """Get assets due for depreciation"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        rows = await conn.fetch(
            "SELECT * FROM get_depreciation_due($1, $2, $3)",
            ctx["tenant_id"], year, month
        )

        items = [CalculateDepreciationItem(
            asset_id=row["asset_id"],
            asset_number=row["asset_number"],
            asset_name=row["asset_name"],
            depreciation_amount=row["depreciation_amount"],
            accumulated_amount=row["accumulated_amount"],
            book_value=row["book_value"],
        ) for row in rows]

        return CalculateDepreciationResponse(
            year=year,
            month=month,
            items=items,
            total_depreciation=sum(Decimal(str(i.depreciation_amount)) for i in items),
            asset_count=len(items),
        )


@router.post("/post-depreciation", response_model=PostDepreciationResponse)
async def post_depreciation(request: Request, data: PostDepreciationRequest):
    """
    Post depreciation for period - creates journal entries:
    Dr. Beban Penyusutan (5-30100)        depreciation_amount
        Cr. Akumulasi Penyusutan (1-20200)    depreciation_amount
    """
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        # Get scheduled depreciations for period
        depreciations = await conn.fetch(
            """
            SELECT ad.*, fa.asset_number, fa.name as asset_name,
                   fa.depreciation_account_id, fa.accumulated_depreciation_account_id
            FROM asset_depreciations ad
            JOIN fixed_assets fa ON ad.asset_id = fa.id
            WHERE fa.tenant_id = $1
            AND ad.period_year = $2
            AND ad.period_month = $3
            AND ad.status = 'scheduled'
            """,
            ctx["tenant_id"], data.year, data.month
        )

        if not depreciations:
            return PostDepreciationResponse(
                year=data.year,
                month=data.month,
                posted=0,
                failed=0,
                total_depreciation=Decimal("0"),
                results=[],
            )

        # Law 27: resolve default accounts via helper
        depreciation_expense = await resolve_account_id(conn, ctx["tenant_id"], '5-30100')
        accumulated_dep = await resolve_account_id(conn, ctx["tenant_id"], '1-20200')

        results = []
        posted = 0
        failed = 0
        total_depreciation = Decimal("0")

        for dep in depreciations:
            try:
                async with conn.transaction():
                    # Law 13: Advisory lock
                    await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"DEPRECIATION:{dep['asset_id']}")

                    # Law 5: Period check
                    await check_period_is_open(conn, ctx["tenant_id"], dep["depreciation_date"])

                    # Generate journal number
                    seq = await conn.fetchrow(
                        """
                        INSERT INTO journal_sequences (tenant_id, last_number)
                        VALUES ($1, 1)
                        ON CONFLICT (tenant_id)
                        DO UPDATE SET last_number = journal_sequences.last_number + 1
                        RETURNING last_number
                        """,
                        ctx["tenant_id"]
                    )
                    journal_number = f"JV-{data.year}-{seq['last_number']:05d}"
                    journal_id = uuid_module.uuid4()
                    dep_amount = Decimal(str(dep["depreciation_amount"]))

                    dep_account = dep["depreciation_account_id"] or depreciation_expense
                    accum_account = dep["accumulated_depreciation_account_id"] or accumulated_dep

                    # Law 20: Step 1 — INSERT as DRAFT
                    await conn.execute(
                        """
                        INSERT INTO journal_entries (
                            id, tenant_id, journal_number, journal_date, description,
                            source_type, source_id, status, total_debit, total_credit, created_by
                        ) VALUES ($1, $2, $3, $4, $5, 'DEPRECIATION', $6, 'DRAFT', $7, $7, $8)
                        """,
                        journal_id, ctx["tenant_id"], journal_number, dep["depreciation_date"],
                        f"Depreciation - {dep['asset_name']} ({data.year}/{data.month})",
                        dep["id"], dep_amount, ctx.get("user_id")
                    )

                    # Law 20: Step 2 — INSERT journal_lines
                    # Dr. Beban Penyusutan
                    await conn.execute(
                        """
                        INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo)
                        VALUES ($1, $2, 1, $3, $4, 0, $5)
                        """,
                        uuid_module.uuid4(), journal_id, dep_account, dep_amount,
                        f"Depreciation - {dep['asset_number']}"
                    )

                    # Cr. Akumulasi Penyusutan
                    await conn.execute(
                        """
                        INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo)
                        VALUES ($1, $2, 2, $3, 0, $4, $5)
                        """,
                        uuid_module.uuid4(), journal_id, accum_account, dep_amount,
                        f"Accumulated Depreciation - {dep['asset_number']}"
                    )

                    # Law 20: Step 3 — UPDATE to POSTED
                    await conn.execute("UPDATE journal_entries SET status = 'POSTED' WHERE id = $1", journal_id)

                    # Update depreciation record
                    await conn.execute(
                        """
                        UPDATE asset_depreciations SET status = 'posted', journal_id = $2, posted_at = NOW()
                        WHERE id = $1
                        """,
                        dep["id"], journal_id
                    )

                    results.append(PostDepreciationResult(
                        asset_id=dep["asset_id"],
                        asset_number=dep["asset_number"],
                        depreciation_amount=dep_amount,
                        journal_id=journal_id,
                        success=True,
                    ))
                    posted += 1
                    total_depreciation += dep_amount

            except Exception as e:
                results.append(PostDepreciationResult(
                    asset_id=dep["asset_id"],
                    asset_number=dep["asset_number"],
                    depreciation_amount=dep["depreciation_amount"],
                    success=False,
                    error=str(e),
                ))
                failed += 1

        return PostDepreciationResponse(
            year=data.year,
            month=data.month,
            posted=posted,
            failed=failed,
            total_depreciation=total_depreciation,
            results=results,
        )


# ============================================================================
# DISPOSE/SELL ASSET
# ============================================================================

@router.post("/{asset_id}/dispose", response_model=DisposeAssetResponse)
async def dispose_fixed_asset(request: Request, asset_id: UUID, data: DisposeAssetRequest):
    """
    Dispose asset (scrapped, donated, lost) - creates journal:
    Dr. Akumulasi Penyusutan (1-20200)    accumulated_depreciation
    Dr. Rugi Penjualan Aset (8-20200)     remaining_book_value
        Cr. Aset Tetap (1-20100)              purchase_price
    """
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        fa = await conn.fetchrow(
            "SELECT * FROM fixed_assets WHERE id = $1 AND tenant_id = $2",
            asset_id, ctx["tenant_id"]
        )
        if not fa:
            raise HTTPException(status_code=404, detail="Asset not found")

        if fa["status"] not in ("active", "fully_depreciated"):
            raise HTTPException(status_code=400, detail="Asset cannot be disposed in current status")

        # Law 27: resolve accounts via helper
        asset_account = fa["asset_account_id"] or await resolve_account_id(conn, ctx["tenant_id"], '1-20100')
        accumulated_dep = fa["accumulated_depreciation_account_id"] or await resolve_account_id(conn, ctx["tenant_id"], '1-20200')
        loss_account = await resolve_account_id(conn, ctx["tenant_id"], '8-20200')

        book_value = Decimal(str(fa["current_value"]))
        purchase_price = Decimal(str(fa["purchase_price"]))
        accum_dep_amount = Decimal(str(fa["accumulated_depreciation"]))

        async with conn.transaction():
            # Law 13: Advisory lock
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"ASSET_DISPOSAL:{asset_id}")

            # Law 5: Period check
            await check_period_is_open(conn, ctx["tenant_id"], data.disposal_date)

            # Generate journal number
            seq = await conn.fetchrow(
                """
                INSERT INTO journal_sequences (tenant_id, last_number)
                VALUES ($1, 1)
                ON CONFLICT (tenant_id)
                DO UPDATE SET last_number = journal_sequences.last_number + 1
                RETURNING last_number
                """,
                ctx["tenant_id"]
            )
            journal_number = f"JV-{data.disposal_date.year}-{seq['last_number']:05d}"
            journal_id = uuid_module.uuid4()

            # Law 20: Step 1 — INSERT as DRAFT
            await conn.execute(
                """
                INSERT INTO journal_entries (
                    id, tenant_id, journal_number, journal_date, description,
                    source_type, source_id, status, total_debit, total_credit, created_by
                ) VALUES ($1, $2, $3, $4, $5, 'ASSET_DISPOSAL', $6, 'DRAFT', $7, $7, $8)
                """,
                journal_id, ctx["tenant_id"], journal_number, data.disposal_date,
                f"Asset Disposal - {fa['name']} ({fa['asset_number']})",
                asset_id, purchase_price, ctx.get("user_id")
            )

            # Law 20: Step 2 — INSERT journal_lines
            line_number = 1

            # Dr. Akumulasi Penyusutan
            if accum_dep_amount > 0:
                await conn.execute(
                    """
                    INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo)
                    VALUES ($1, $2, $3, $4, $5, 0, $6)
                    """,
                    uuid_module.uuid4(), journal_id, line_number, accumulated_dep, accum_dep_amount,
                    f"Accumulated Depreciation - {fa['asset_number']}"
                )
                line_number += 1

            # Dr. Rugi (book value)
            if book_value > 0:
                await conn.execute(
                    """
                    INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo)
                    VALUES ($1, $2, $3, $4, $5, 0, $6)
                    """,
                    uuid_module.uuid4(), journal_id, line_number, loss_account, book_value,
                    f"Loss on Disposal - {fa['asset_number']}"
                )
                line_number += 1

            # Cr. Aset Tetap
            await conn.execute(
                """
                INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo)
                VALUES ($1, $2, $3, $4, 0, $5, $6)
                """,
                uuid_module.uuid4(), journal_id, line_number, asset_account, purchase_price,
                f"Asset Disposal - {fa['asset_number']}"
            )

            # Law 20: Step 3 — UPDATE to POSTED
            await conn.execute("UPDATE journal_entries SET status = 'POSTED' WHERE id = $1", journal_id)

            # Update asset
            await conn.execute(
                """
                UPDATE fixed_assets SET
                    status = 'disposed',
                    disposal_date = $2,
                    disposal_method = $3,
                    disposal_journal_id = $4,
                    gain_loss_amount = $5,
                    updated_at = NOW()
                WHERE id = $1
                """,
                asset_id, data.disposal_date, data.disposal_method.value,
                journal_id, -book_value
            )

            return DisposeAssetResponse(
                asset_id=asset_id,
                asset_number=fa["asset_number"],
                status=AssetStatus.disposed,
                disposal_method=data.disposal_method,
                book_value_at_disposal=book_value,
                loss_amount=book_value,
                journal_id=journal_id,
            )


@router.post("/{asset_id}/sell", response_model=SellAssetResponse)
async def sell_fixed_asset(request: Request, asset_id: UUID, data: SellAssetRequest):
    """
    Sell asset - creates journal with gain/loss:
    Dr. Kas/Bank/Piutang                  sale_price
    Dr. Akumulasi Penyusutan (1-20200)    accumulated_depreciation
        Cr. Aset Tetap (1-20100)              purchase_price
        Cr. Laba Penjualan Aset (8-10200)     gain (if sale > book value)
    -- OR --
        Dr. Rugi Penjualan Aset (8-20200)     loss (if sale < book value)
    """
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        fa = await conn.fetchrow(
            "SELECT * FROM fixed_assets WHERE id = $1 AND tenant_id = $2",
            asset_id, ctx["tenant_id"]
        )
        if not fa:
            raise HTTPException(status_code=404, detail="Asset not found")

        if fa["status"] not in ("active", "fully_depreciated"):
            raise HTTPException(status_code=400, detail="Asset cannot be sold in current status")

        book_value = Decimal(str(fa["current_value"]))
        sale_price = Decimal(str(data.sale_price))
        gain_loss = sale_price - book_value
        purchase_price = Decimal(str(fa["purchase_price"]))
        accum_dep_amount = Decimal(str(fa["accumulated_depreciation"]))

        # Law 27: resolve accounts via helper
        asset_account = fa["asset_account_id"] or await resolve_account_id(conn, ctx["tenant_id"], '1-20100')
        accumulated_dep = fa["accumulated_depreciation_account_id"] or await resolve_account_id(conn, ctx["tenant_id"], '1-20200')
        gain_account = await resolve_account_id(conn, ctx["tenant_id"], '8-10200')
        loss_account = await resolve_account_id(conn, ctx["tenant_id"], '8-20200')

        # Calculate total_debit for balanced entry
        total_debit = sale_price + accum_dep_amount
        if gain_loss < 0:
            total_debit += abs(gain_loss)

        async with conn.transaction():
            # Law 13: Advisory lock
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"ASSET_SALE:{asset_id}")

            # Law 5: Period check
            await check_period_is_open(conn, ctx["tenant_id"], data.sale_date)

            # Generate journal number
            seq = await conn.fetchrow(
                """
                INSERT INTO journal_sequences (tenant_id, last_number)
                VALUES ($1, 1)
                ON CONFLICT (tenant_id)
                DO UPDATE SET last_number = journal_sequences.last_number + 1
                RETURNING last_number
                """,
                ctx["tenant_id"]
            )
            journal_number = f"JV-{data.sale_date.year}-{seq['last_number']:05d}"
            journal_id = uuid_module.uuid4()

            # total_credit = purchase_price + gain (if any)
            total_credit = purchase_price
            if gain_loss > 0:
                total_credit += gain_loss

            # Law 20: Step 1 — INSERT as DRAFT
            await conn.execute(
                """
                INSERT INTO journal_entries (
                    id, tenant_id, journal_number, journal_date, description,
                    source_type, source_id, status, total_debit, total_credit, created_by
                ) VALUES ($1, $2, $3, $4, $5, 'ASSET_SALE', $6, 'DRAFT', $7, $8, $9)
                """,
                journal_id, ctx["tenant_id"], journal_number, data.sale_date,
                f"Asset Sale - {fa['name']} ({fa['asset_number']})",
                asset_id, total_debit, total_credit, ctx.get("user_id")
            )

            # Law 20: Step 2 — INSERT journal_lines
            line_number = 1

            # Dr. Receivable/Cash
            await conn.execute(
                """
                INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo)
                VALUES ($1, $2, $3, $4, $5, 0, $6)
                """,
                uuid_module.uuid4(), journal_id, line_number, data.receivable_account_id, sale_price,
                f"Asset Sale - {fa['asset_number']}"
            )
            line_number += 1

            # Dr. Akumulasi Penyusutan
            if accum_dep_amount > 0:
                await conn.execute(
                    """
                    INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo)
                    VALUES ($1, $2, $3, $4, $5, 0, $6)
                    """,
                    uuid_module.uuid4(), journal_id, line_number, accumulated_dep, accum_dep_amount,
                    f"Accumulated Depreciation - {fa['asset_number']}"
                )
                line_number += 1

            # Dr. Loss (if loss)
            if gain_loss < 0:
                await conn.execute(
                    """
                    INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo)
                    VALUES ($1, $2, $3, $4, $5, 0, $6)
                    """,
                    uuid_module.uuid4(), journal_id, line_number, loss_account, abs(gain_loss),
                    f"Loss on Sale - {fa['asset_number']}"
                )
                line_number += 1

            # Cr. Aset Tetap
            await conn.execute(
                """
                INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo)
                VALUES ($1, $2, $3, $4, 0, $5, $6)
                """,
                uuid_module.uuid4(), journal_id, line_number, asset_account, purchase_price,
                f"Asset Sale - {fa['asset_number']}"
            )
            line_number += 1

            # Cr. Gain (if gain)
            if gain_loss > 0:
                await conn.execute(
                    """
                    INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo)
                    VALUES ($1, $2, $3, $4, 0, $5, $6)
                    """,
                    uuid_module.uuid4(), journal_id, line_number, gain_account, gain_loss,
                    f"Gain on Sale - {fa['asset_number']}"
                )

            # Law 20: Step 3 — UPDATE to POSTED
            await conn.execute("UPDATE journal_entries SET status = 'POSTED' WHERE id = $1", journal_id)

            # Update asset
            await conn.execute(
                """
                UPDATE fixed_assets SET
                    status = 'sold',
                    disposal_date = $2,
                    disposal_method = 'sold',
                    disposal_price = $3,
                    disposal_journal_id = $4,
                    gain_loss_amount = $5,
                    updated_at = NOW()
                WHERE id = $1
                """,
                asset_id, data.sale_date, sale_price, journal_id, gain_loss
            )

            return SellAssetResponse(
                asset_id=asset_id,
                asset_number=fa["asset_number"],
                status=AssetStatus.sold,
                sale_price=sale_price,
                book_value_at_sale=book_value,
                gain_loss_amount=gain_loss,
                journal_id=journal_id,
            )


# ============================================================================
# REPORTS
# ============================================================================

@router.get("/register", response_model=AssetRegisterResponse)
async def get_asset_register(request: Request, status: Optional[AssetStatus] = None):
    """Get asset register report"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        rows = await conn.fetch(
            "SELECT * FROM get_asset_register($1, $2)",
            ctx["tenant_id"], status.value if status else None
        )

        items = [AssetRegisterItem(**dict(row)) for row in rows]

        return AssetRegisterResponse(
            items=items,
            total_purchase_price=sum(Decimal(str(i.purchase_price)) for i in items),
            total_current_value=sum(Decimal(str(i.current_value)) for i in items),
            total_accumulated_depreciation=sum(Decimal(str(i.accumulated_depreciation)) for i in items),
            asset_count=len(items),
        )


@router.get("/by-category", response_model=AssetsByCategoryResponse)
async def get_assets_by_category(request: Request):
    """Get assets summary by category"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        rows = await conn.fetch(
            "SELECT * FROM get_assets_by_category($1)",
            ctx["tenant_id"]
        )

        return AssetsByCategoryResponse(
            items=[AssetsByCategoryItem(**dict(row)) for row in rows]
        )


@router.get("/by-location", response_model=AssetsByLocationResponse)
async def get_assets_by_location(request: Request):
    """Get assets summary by location"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        rows = await conn.fetch(
            "SELECT * FROM get_assets_by_location($1)",
            ctx["tenant_id"]
        )

        return AssetsByLocationResponse(
            items=[AssetsByLocationItem(**dict(row)) for row in rows]
        )


# ============================================================================
# MAINTENANCE
# ============================================================================

@router.get("/{asset_id}/maintenance")
async def get_asset_maintenance(request: Request, asset_id: UUID):
    """Get asset maintenance history"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        rows = await conn.fetch(
            """
            SELECT am.*, v.name as vendor_name
            FROM asset_maintenance am
            LEFT JOIN vendors v ON am.vendor_id = v.id
            WHERE am.asset_id = $1
            ORDER BY am.maintenance_date DESC
            """,
            asset_id
        )

        return [AssetMaintenanceResponse(**dict(row)) for row in rows]


@router.post("/{asset_id}/maintenance", response_model=AssetMaintenanceResponse, status_code=201)
async def log_asset_maintenance(request: Request, asset_id: UUID, data: AssetMaintenanceCreate):
    """Log maintenance for asset"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        # Verify asset exists
        fa = await conn.fetchval(
            "SELECT 1 FROM fixed_assets WHERE id = $1 AND tenant_id = $2",
            asset_id, ctx["tenant_id"]
        )
        if not fa:
            raise HTTPException(status_code=404, detail="Asset not found")

        row = await conn.fetchrow(
            """
            INSERT INTO asset_maintenance (
                asset_id, maintenance_date, description, cost, vendor_id,
                bill_id, maintenance_type, next_maintenance_date
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            RETURNING *
            """,
            asset_id, data.maintenance_date, data.description, data.cost,
            data.vendor_id, data.bill_id,
            data.maintenance_type.value if data.maintenance_type else None,
            data.next_maintenance_date
        )

        vendor_name = None
        if data.vendor_id:
            vendor_name = await conn.fetchval(
                "SELECT name FROM vendors WHERE id = $1",
                data.vendor_id
            )

        return AssetMaintenanceResponse(**dict(row), vendor_name=vendor_name)


@router.get("/maintenance-due", response_model=MaintenanceDueResponse)
async def get_maintenance_due(request: Request, days_ahead: int = Query(30, ge=1, le=365)):
    """Get assets with upcoming maintenance"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        rows = await conn.fetch(
            "SELECT * FROM get_maintenance_due($1, $2)",
            ctx["tenant_id"], days_ahead
        )

        return MaintenanceDueResponse(
            days_ahead=days_ahead,
            items=[MaintenanceDueItem(**dict(row)) for row in rows],
        )
