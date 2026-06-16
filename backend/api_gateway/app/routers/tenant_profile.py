"""
Tenant Profile router - GET/PUT tenant business profile (Profil Bisnis).
Includes logo upload/delete endpoints.
"""
import logging
import os
import time  # FIX_LOGO_CACHEBUST 2026-06-16
import glob
from typing import Optional
from pathlib import Path
from fastapi import APIRouter, Request, HTTPException, UploadFile, File
from pydantic import BaseModel
import asyncpg
from ..config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

LOGO_DIR = Path(__file__).parent.parent / "static" / "logos"
ALLOWED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp"}
MAX_LOGO_SIZE = 5 * 1024 * 1024  # 5MB


async def get_db_connection():
    db_config = settings.get_db_config()
    return await asyncpg.connect(**db_config)


def _get_tenant_id(request: Request) -> str:
    user = getattr(request.state, "user", None)
    if user:
        if isinstance(user, dict):
            return user.get("tenant_id", "")
        return getattr(user, "tenant_id", "")
    return request.headers.get("X-Tenant-ID", "")


def _build_profile_dict(row) -> dict:
    """Build profile response dict from DB row."""
    return {
        "display_name": row["display_name"],
        "address": row["address"],
        "phone": row["phone"],
        "tax_id": row["tax_id"],
        "alias": row["alias"],
        "status": row["status"],
        "timezone": row["timezone"],
        "currency": row["currency"],
        "logo_url": row["logo_url"],
    }


PROFILE_SELECT = """
    SELECT display_name, address, phone, tax_id,
           alias, status, timezone, currency, logo_url
    FROM "Tenant"
    WHERE id = $1
"""


# ── Models ──────────────────────────────────────────


class TenantProfileResponse(BaseModel):
    success: bool
    data: Optional[dict] = None


class UpdateTenantProfileRequest(BaseModel):
    display_name: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    tax_id: Optional[str] = None


# ── Endpoints ───────────────────────────────────────


@router.get("/profile", response_model=TenantProfileResponse)
async def get_tenant_profile(request: Request):
    """Get current tenant's business profile."""
    tenant_id = _get_tenant_id(request)
    if not tenant_id:
        raise HTTPException(status_code=401, detail="No tenant context")

    conn = await get_db_connection()
    try:
        row = await conn.fetchrow(PROFILE_SELECT, tenant_id)
        if not row:
            raise HTTPException(status_code=404, detail="Tenant not found")
        return TenantProfileResponse(success=True, data=_build_profile_dict(row))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[tenant/profile] GET error: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch tenant profile")
    finally:
        await conn.close()


@router.put("/profile", response_model=TenantProfileResponse)
@router.patch("/profile", response_model=TenantProfileResponse)
async def update_tenant_profile(body: UpdateTenantProfileRequest, request: Request):
    """Update tenant business profile fields."""
    tenant_id = _get_tenant_id(request)
    if not tenant_id:
        raise HTTPException(status_code=401, detail="No tenant context")

    # Build SET clause dynamically for partial updates
    updates = {}
    if body.display_name is not None:
        name = body.display_name.strip()[:200]
        if not name:
            raise HTTPException(status_code=400, detail="display_name cannot be empty")
        updates["display_name"] = name
    if body.address is not None:
        updates["address"] = body.address.strip()[:500] or None
    if body.phone is not None:
        updates["phone"] = body.phone.strip()[:50] or None
    if body.tax_id is not None:
        updates["tax_id"] = body.tax_id.strip()[:50] or None

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    # Build parameterized query
    set_parts = []
    params = []
    for i, (col, val) in enumerate(updates.items(), start=1):
        set_parts.append(f"{col} = ${i}")
        params.append(val)
    params.append(tenant_id)
    set_clause = ", ".join(set_parts)
    tenant_param = f"${len(params)}"

    conn = await get_db_connection()
    try:
        await conn.execute(
            f'UPDATE "Tenant" SET {set_clause}, updated_at = NOW() WHERE id = {tenant_param}',
            *params,
        )
        row = await conn.fetchrow(PROFILE_SELECT, tenant_id)
        return TenantProfileResponse(success=True, data=_build_profile_dict(row))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[tenant/profile] PUT error: {e}")
        raise HTTPException(status_code=500, detail="Failed to update tenant profile")
    finally:
        await conn.close()


@router.post("/profile/logo", response_model=TenantProfileResponse)
async def upload_tenant_logo(request: Request, file: UploadFile = File(...)):
    """Upload tenant logo image. Replaces existing logo."""
    tenant_id = _get_tenant_id(request)
    if not tenant_id:
        raise HTTPException(status_code=401, detail="No tenant context")

    # Validate content type
    if file.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=400, detail="Only PNG, JPEG, or WebP images allowed"
        )

    # Read and validate size
    contents = await file.read()
    if len(contents) > MAX_LOGO_SIZE:
        raise HTTPException(status_code=400, detail="File too large (max 5MB)")
    if len(contents) == 0:
        raise HTTPException(status_code=400, detail="Empty file")

    # Determine extension
    ext_map = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
    ext = ext_map.get(file.content_type, "png")
    filename = f"{tenant_id}-{int(time.time())}.{ext}"  # FIX_LOGO_CACHEBUST 2026-06-16 versioned key busts UI img + PDF base64

    # Ensure logo directory exists
    LOGO_DIR.mkdir(parents=True, exist_ok=True)

    # Remove any previous logo for this tenant (fixed-name AND prior versioned)  # FIX_LOGO_CACHEBUST 2026-06-16
    for old in glob.glob(str(LOGO_DIR / f"{tenant_id}.*")) + glob.glob(
        str(LOGO_DIR / f"{tenant_id}-*")
    ):
        try:
            os.remove(old)
        except OSError:
            pass

    # Save file
    logo_path = LOGO_DIR / filename
    with open(logo_path, "wb") as f:
        f.write(contents)

    # Update DB
    conn = await get_db_connection()
    try:
        await conn.execute(
            'UPDATE "Tenant" SET logo_url = $1, updated_at = NOW() WHERE id = $2',
            filename,
            tenant_id,
        )
        row = await conn.fetchrow(PROFILE_SELECT, tenant_id)
        return TenantProfileResponse(success=True, data=_build_profile_dict(row))
    except Exception as e:
        logger.error(f"[tenant/profile/logo] POST error: {e}")
        raise HTTPException(status_code=500, detail="Failed to save logo")
    finally:
        await conn.close()


@router.delete("/profile/logo", response_model=TenantProfileResponse)
async def delete_tenant_logo(request: Request):
    """Delete tenant logo."""
    tenant_id = _get_tenant_id(request)
    if not tenant_id:
        raise HTTPException(status_code=401, detail="No tenant context")

    # Remove logo files (fixed-name AND versioned)  # FIX_LOGO_CACHEBUST 2026-06-16
    for old in glob.glob(str(LOGO_DIR / f"{tenant_id}.*")) + glob.glob(
        str(LOGO_DIR / f"{tenant_id}-*")
    ):
        try:
            os.remove(old)
        except OSError:
            pass

    conn = await get_db_connection()
    try:
        await conn.execute(
            'UPDATE "Tenant" SET logo_url = NULL, updated_at = NOW() WHERE id = $1',
            tenant_id,
        )
        row = await conn.fetchrow(PROFILE_SELECT, tenant_id)
        return TenantProfileResponse(success=True, data=_build_profile_dict(row))
    except Exception as e:
        logger.error(f"[tenant/profile/logo] DELETE error: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete logo")
    finally:
        await conn.close()


@router.get("/profile/logo/{filename}")
async def serve_tenant_logo(filename: str):
    """Serve a tenant logo file."""
    import re

    if not re.match(r"^[a-zA-Z0-9_-]+\.\w+$", filename):
        raise HTTPException(status_code=400, detail="Invalid filename")

    logo_path = LOGO_DIR / filename
    if not logo_path.exists():
        raise HTTPException(status_code=404, detail="Logo not found")

    from fastapi.responses import FileResponse

    ext = logo_path.suffix.lstrip(".")
    media = f"image/{ext}" if ext != "jpg" else "image/jpeg"
    return FileResponse(
        str(logo_path),
        media_type=media,
        headers={
            "Cache-Control": "no-cache, must-revalidate"
        },  # FIX_LOGO_CACHEBUST 2026-06-16
    )
