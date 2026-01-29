"""
Accounting Settings Router
Separated from reports.py for Law 0 compliance (Separation of Concerns)
"""

import uuid
import logging
import asyncpg
from datetime import date
from fastapi import APIRouter, HTTPException, Request, Query
from pydantic import BaseModel
from typing import Optional
from enum import Enum

from ..config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/settings", tags=["settings"])

# Connection pool
_pool: asyncpg.Pool = None


async def get_pool() -> asyncpg.Pool:
    """Get or create connection pool."""
    global _pool
    if _pool is None:
        db_config = settings.get_db_config()
        _pool = await asyncpg.create_pool(
            **db_config, min_size=2, max_size=10, command_timeout=60
        )
    return _pool


async def get_db_connection():
    """Get database connection."""
    db_config = settings.get_db_config()
    return await asyncpg.connect(**db_config)


# ============================================================
# Pydantic Models
# ============================================================

class AccountingSettingsResponse(BaseModel):
    id: str
    tenant_id: str
    default_report_basis: str
    fiscal_year_start_month: int
    base_currency_code: str
    decimal_places: int
    thousand_separator: str
    decimal_separator: str
    date_format: str
    created_at: str
    updated_at: str


class AccountingSettingsDetailResponse(BaseModel):
    success: bool
    data: AccountingSettingsResponse


class UpdateAccountingSettingsRequest(BaseModel):
    default_report_basis: Optional[str] = None
    fiscal_year_start_month: Optional[int] = None
    base_currency_code: Optional[str] = None
    decimal_places: Optional[int] = None
    thousand_separator: Optional[str] = None
    decimal_separator: Optional[str] = None
    date_format: Optional[str] = None


class CreateAccountingSettingsRequest(BaseModel):
    default_report_basis: str = "accrual"
    fiscal_year_start_month: int = 1
    base_currency_code: str = "IDR"


class AgingType(str, Enum):
    ar = "ar"
    ap = "ap"


class CreateSnapshotRequest(BaseModel):
    snapshot_type: AgingType
    as_of_date: Optional[date] = None


class CreateSnapshotResponse(BaseModel):
    snapshot_id: str
    snapshot_type: AgingType
    as_of_date: date


# ============================================================
# Endpoints
# ============================================================

@router.get("/accounting", response_model=AccountingSettingsDetailResponse)
async def get_accounting_settings(request: Request):
    """Get tenant accounting settings (read-only, does not create)."""
    try:
        if not hasattr(request.state, "user") or not request.state.user:
            raise HTTPException(status_code=401, detail="Authentication required")

        tenant_id = request.state.user.get("tenant_id")
        if not tenant_id:
            raise HTTPException(status_code=401, detail="Invalid user context")

        conn = await get_db_connection()
        try:
            row = await conn.fetchrow(
                "SELECT * FROM accounting_settings WHERE tenant_id = $1",
                tenant_id,
            )

            if not row:
                raise HTTPException(
                    status_code=404, 
                    detail="Accounting settings not found. Use POST to create."
                )

            return AccountingSettingsDetailResponse(
                success=True,
                data=AccountingSettingsResponse(
                    id=str(row["id"]),
                    tenant_id=row["tenant_id"],
                    default_report_basis=row["default_report_basis"] or "accrual",
                    fiscal_year_start_month=row["fiscal_year_start_month"] or 1,
                    base_currency_code=row["base_currency_code"] or "IDR",
                    decimal_places=row["decimal_places"] or 0,
                    thousand_separator=row["thousand_separator"] or ".",
                    decimal_separator=row["decimal_separator"] or ",",
                    date_format=row["date_format"] or "DD/MM/YYYY",
                    created_at=row["created_at"].isoformat() if row["created_at"] else "",
                    updated_at=row["updated_at"].isoformat() if row["updated_at"] else "",
                ),
            )
        finally:
            await conn.close()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get accounting settings error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get accounting settings")


@router.post("/accounting", response_model=AccountingSettingsDetailResponse)
async def create_accounting_settings(request: Request, data: CreateAccountingSettingsRequest):
    """Create tenant accounting settings."""
    try:
        if not hasattr(request.state, "user") or not request.state.user:
            raise HTTPException(status_code=401, detail="Authentication required")

        tenant_id = request.state.user.get("tenant_id")
        if not tenant_id:
            raise HTTPException(status_code=401, detail="Invalid user context")

        conn = await get_db_connection()
        try:
            existing = await conn.fetchrow(
                "SELECT id FROM accounting_settings WHERE tenant_id = $1",
                tenant_id,
            )

            if existing:
                raise HTTPException(
                    status_code=409,
                    detail="Accounting settings already exist. Use PATCH to update."
                )

            new_id = str(uuid.uuid4())
            await conn.execute(
                """
                INSERT INTO accounting_settings (
                    id, tenant_id, default_report_basis, 
                    fiscal_year_start_month, base_currency_code
                ) VALUES ($1, $2, $3, $4, $5)
                """,
                new_id, tenant_id, data.default_report_basis,
                data.fiscal_year_start_month, data.base_currency_code,
            )

            row = await conn.fetchrow(
                "SELECT * FROM accounting_settings WHERE tenant_id = $1",
                tenant_id,
            )

            return AccountingSettingsDetailResponse(
                success=True,
                data=AccountingSettingsResponse(
                    id=str(row["id"]),
                    tenant_id=row["tenant_id"],
                    default_report_basis=row["default_report_basis"] or "accrual",
                    fiscal_year_start_month=row["fiscal_year_start_month"] or 1,
                    base_currency_code=row["base_currency_code"] or "IDR",
                    decimal_places=row["decimal_places"] or 0,
                    thousand_separator=row["thousand_separator"] or ".",
                    decimal_separator=row["decimal_separator"] or ",",
                    date_format=row["date_format"] or "DD/MM/YYYY",
                    created_at=row["created_at"].isoformat() if row["created_at"] else "",
                    updated_at=row["updated_at"].isoformat() if row["updated_at"] else "",
                ),
            )
        finally:
            await conn.close()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Create accounting settings error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create accounting settings")


@router.patch("/accounting", response_model=AccountingSettingsDetailResponse)
async def update_accounting_settings(request: Request, data: UpdateAccountingSettingsRequest):
    """Update tenant accounting settings."""
    try:
        if not hasattr(request.state, "user") or not request.state.user:
            raise HTTPException(status_code=401, detail="Authentication required")

        tenant_id = request.state.user.get("tenant_id")
        if not tenant_id:
            raise HTTPException(status_code=401, detail="Invalid user context")

        conn = await get_db_connection()
        try:
            existing = await conn.fetchrow(
                "SELECT id FROM accounting_settings WHERE tenant_id = $1",
                tenant_id,
            )

            if not existing:
                raise HTTPException(
                    status_code=404,
                    detail="Accounting settings not found. Use POST to create."
                )

            updates = []
            params = [tenant_id]
            param_idx = 2

            if data.default_report_basis is not None:
                updates.append(f"default_report_basis = ${param_idx}")
                params.append(data.default_report_basis)
                param_idx += 1

            if data.fiscal_year_start_month is not None:
                updates.append(f"fiscal_year_start_month = ${param_idx}")
                params.append(data.fiscal_year_start_month)
                param_idx += 1

            if data.base_currency_code is not None:
                updates.append(f"base_currency_code = ${param_idx}")
                params.append(data.base_currency_code)
                param_idx += 1

            if data.decimal_places is not None:
                updates.append(f"decimal_places = ${param_idx}")
                params.append(data.decimal_places)
                param_idx += 1

            if data.thousand_separator is not None:
                updates.append(f"thousand_separator = ${param_idx}")
                params.append(data.thousand_separator)
                param_idx += 1

            if data.decimal_separator is not None:
                updates.append(f"decimal_separator = ${param_idx}")
                params.append(data.decimal_separator)
                param_idx += 1

            if data.date_format is not None:
                updates.append(f"date_format = ${param_idx}")
                params.append(data.date_format)
                param_idx += 1

            if updates:
                updates.append("updated_at = NOW()")
                update_sql = f"""
                    UPDATE accounting_settings
                    SET {", ".join(updates)}
                    WHERE tenant_id = $1
                """
                await conn.execute(update_sql, *params)

            row = await conn.fetchrow(
                "SELECT * FROM accounting_settings WHERE tenant_id = $1",
                tenant_id,
            )

            return AccountingSettingsDetailResponse(
                success=True,
                data=AccountingSettingsResponse(
                    id=str(row["id"]),
                    tenant_id=row["tenant_id"],
                    default_report_basis=row["default_report_basis"] or "accrual",
                    fiscal_year_start_month=row["fiscal_year_start_month"] or 1,
                    base_currency_code=row["base_currency_code"] or "IDR",
                    decimal_places=row["decimal_places"] or 0,
                    thousand_separator=row["thousand_separator"] or ".",
                    decimal_separator=row["decimal_separator"] or ",",
                    date_format=row["date_format"] or "DD/MM/YYYY",
                    created_at=row["created_at"].isoformat() if row["created_at"] else "",
                    updated_at=row["updated_at"].isoformat() if row["updated_at"] else "",
                ),
            )
        finally:
            await conn.close()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Update accounting settings error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update accounting settings")


@router.post("/aging-snapshot", response_model=CreateSnapshotResponse)
async def create_aging_snapshot(request: Request, data: CreateSnapshotRequest):
    """Create an aging snapshot for trend analysis."""
    try:
        if not hasattr(request.state, "user") or not request.state.user:
            raise HTTPException(status_code=401, detail="Authentication required")

        tenant_id = request.state.user.get("tenant_id")
        if not tenant_id:
            raise HTTPException(status_code=401, detail="Invalid user context")

        as_of_date = data.as_of_date or date.today()
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tenant_id
            )

            if data.snapshot_type == AgingType.ar:
                snapshot_id = await conn.fetchval(
                    "SELECT create_ar_aging_snapshot($1, $2)", tenant_id, as_of_date
                )
            else:
                snapshot_id = await conn.fetchval(
                    "SELECT create_ap_aging_snapshot($1, $2)", tenant_id, as_of_date
                )

            return CreateSnapshotResponse(
                snapshot_id=str(snapshot_id),
                snapshot_type=data.snapshot_type,
                as_of_date=as_of_date,
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Create aging snapshot error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create aging snapshot")
