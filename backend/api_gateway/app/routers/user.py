"""
User router - User profile and tenant management endpoints.
"""
import logging
from typing import List, Optional
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
import asyncpg
from ..config import settings

logger = logging.getLogger(__name__)
router = APIRouter()


async def get_db_connection():
    db_config = settings.get_db_config()
    return await asyncpg.connect(**db_config)


def _get_user_id(request: Request) -> str:
    """Extract user_id from JWT token."""
    user = getattr(request.state, "user", None)
    if not user:
        return ""
    if isinstance(user, dict):
        return user.get("id", "") or user.get("user_id", "")
    return getattr(user, "id", "") or getattr(user, "user_id", "")


def get_tenant_id(request: Request) -> str:
    """Extract tenant_id from JWT token."""
    user = getattr(request.state, "user", None)
    if user:
        if hasattr(user, "tenant_id"):
            return user.tenant_id
        if isinstance(user, dict):
            return user.get("tenant_id", "")
    return request.headers.get("X-Tenant-ID", "")


# ── Models ──────────────────────────────────────────


class TenantInfo(BaseModel):
    id: str
    name: str
    slug: str
    is_active: bool = True
    logo_url: Optional[str] = None


class UserTenantsResponse(BaseModel):
    success: bool
    data: List[TenantInfo]


class UserProfileResponse(BaseModel):
    success: bool
    display_name: Optional[str] = None
    email: Optional[str] = None


class UpdateProfileRequest(BaseModel):
    display_name: str


# ── Endpoints ───────────────────────────────────────


@router.get("/user/tenants", response_model=UserTenantsResponse)
async def get_user_tenants(request: Request):
    """Get list of tenants the current user has access to."""
    tenant_id = get_tenant_id(request)
    # Extract email for fallback display name
    user = getattr(request.state, "user", {})
    user_email = (
        user.get("email", "") if isinstance(user, dict) else getattr(user, "email", "")
    )

    if tenant_id:
        # Fetch real display_name and logo_url from Tenant table
        conn = await get_db_connection()
        try:
            row = await conn.fetchrow(
                'SELECT display_name, alias, logo_url FROM "Tenant" WHERE id = $1',
                tenant_id,
            )
            name = (
                (row["display_name"] if row and row["display_name"] else None)
                or (row["alias"] if row and row["alias"] else None)
                or tenant_id
                or user_email
            )
            slug = row["alias"] if row and row["alias"] else tenant_id
            logo_url = row["logo_url"] if row else None
        except Exception as e:
            logger.error(f"[user/tenants] DB error: {e}")
            name = tenant_id or user_email
            slug = tenant_id
            logo_url = None
        finally:
            await conn.close()

        return UserTenantsResponse(
            success=True,
            data=[
                TenantInfo(
                    id=tenant_id,
                    name=name,
                    slug=slug,
                    is_active=True,
                    logo_url=logo_url,
                )
            ],
        )

    return UserTenantsResponse(success=True, data=[])


@router.get("/user/profile", response_model=UserProfileResponse)
async def get_user_profile(request: Request):
    """Get current user's account-level profile (display_name)."""
    user_id = _get_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    # Also grab email from JWT for convenience
    user = getattr(request.state, "user", {})
    email = (
        user.get("email", "") if isinstance(user, dict) else getattr(user, "email", "")
    )

    conn = await get_db_connection()
    try:
        row = await conn.fetchrow(
            "SELECT display_name FROM user_profiles WHERE user_id = $1",
            user_id,
        )
        return UserProfileResponse(
            success=True,
            display_name=row["display_name"] if row else None,
            email=email,
        )
    except Exception as e:
        logger.error(f"[user/profile] GET error: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch profile")
    finally:
        await conn.close()


@router.put("/user/profile", response_model=UserProfileResponse)
async def update_user_profile(body: UpdateProfileRequest, request: Request):
    """Update current user's display name."""
    user_id = _get_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    display_name = body.display_name.strip()[:100]  # Sanitize
    if not display_name:
        raise HTTPException(status_code=400, detail="display_name cannot be empty")

    user = getattr(request.state, "user", {})
    email = (
        user.get("email", "") if isinstance(user, dict) else getattr(user, "email", "")
    )

    conn = await get_db_connection()
    try:
        await conn.execute(
            """
            INSERT INTO user_profiles (user_id, display_name, updated_at)
            VALUES ($1, $2, NOW())
            ON CONFLICT (user_id)
            DO UPDATE SET display_name = $2, updated_at = NOW()
            """,
            user_id,
            display_name,
        )
        return UserProfileResponse(
            success=True,
            display_name=display_name,
            email=email,
        )
    except Exception as e:
        logger.error(f"[user/profile] PUT error: {e}")
        raise HTTPException(status_code=500, detail="Failed to update profile")
    finally:
        await conn.close()


# ── Favorites ────────────────────────────────────────


class FavoriteItem(BaseModel):
    panel_key: str
    label: str
    icon_key: Optional[str] = None
    sort_order: int = 0


class FavoritesResponse(BaseModel):
    items: List[FavoriteItem]


MAX_FAVORITES = 8


@router.get("/user/favorites", response_model=FavoritesResponse)
async def get_favorites(request: Request):
    user_id = _get_user_id(request)
    tenant_id = get_tenant_id(request)
    if not user_id or not tenant_id:
        raise HTTPException(status_code=401, detail="Unauthorized")

    conn = await get_db_connection()
    try:
        rows = await conn.fetch(
            """SELECT panel_key, label, icon_key, sort_order
               FROM user_favorites
               WHERE user_id = $1 AND tenant_id = $2
               ORDER BY sort_order ASC""",
            user_id,
            tenant_id,
        )
        return FavoritesResponse(items=[FavoriteItem(**dict(r)) for r in rows])
    finally:
        await conn.close()


@router.put("/user/favorites", response_model=FavoritesResponse)
async def put_favorites(request: Request, body: FavoritesResponse):
    user_id = _get_user_id(request)
    tenant_id = get_tenant_id(request)
    if not user_id or not tenant_id:
        raise HTTPException(status_code=401, detail="Unauthorized")
    if len(body.items) > MAX_FAVORITES:
        raise HTTPException(status_code=400, detail=f"Maksimal {MAX_FAVORITES} favorit")

    conn = await get_db_connection()
    try:
        async with conn.transaction():
            await conn.execute(
                "DELETE FROM user_favorites WHERE user_id = $1 AND tenant_id = $2",
                user_id,
                tenant_id,
            )
            for i, item in enumerate(body.items):
                await conn.execute(
                    """INSERT INTO user_favorites (user_id, tenant_id, panel_key, label, icon_key, sort_order)
                       VALUES ($1, $2, $3, $4, $5, $6)""",
                    user_id,
                    tenant_id,
                    item.panel_key,
                    item.label,
                    item.icon_key,
                    i,
                )
        return FavoritesResponse(items=body.items)
    finally:
        await conn.close()
