"""Pay Groups Router — CRUD + access management for pay groups."""
import logging
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Optional
from ..services.db_pool import get_db_pool

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/pay-groups", tags=["pay-groups"])


class PayGroupCreate(BaseModel):
    name: str
    description: Optional[str] = None


class PayGroupUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None


class PayGroupAccessUpdate(BaseModel):
    pay_group_ids: list[str]


@router.get("")
async def list_pay_groups(request: Request):
    """List all active pay groups for tenant."""
    user = request.state.user
    tenant_id = user.get("tenant_id")

    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "SELECT set_config('app.tenant_id', $1, true)", str(tenant_id)
        )
        rows = await conn.fetch(
            """SELECT pg.id, pg.name, pg.description, pg.is_default, pg.is_active,
                      COUNT(e.id) as employee_count
               FROM pay_groups pg
               LEFT JOIN employees e ON e.pay_group_id = pg.id AND e.is_active = true
               WHERE pg.tenant_id = $1 AND pg.is_active = true
               GROUP BY pg.id
               ORDER BY pg.is_default DESC, pg.name""",
            tenant_id,
        )
        return {
            "data": [
                {
                    "id": str(r["id"]),
                    "name": r["name"],
                    "description": r["description"],
                    "is_default": r["is_default"],
                    "employee_count": r["employee_count"],
                }
                for r in rows
            ]
        }


@router.post("")
async def create_pay_group(request: Request, data: PayGroupCreate):
    """Create a new pay group. OWNER/ADMIN only."""
    user = request.state.user
    tenant_id = user.get("tenant_id")

    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "SELECT set_config('app.tenant_id', $1, true)", str(tenant_id)
        )
        try:
            row = await conn.fetchrow(
                """INSERT INTO pay_groups (tenant_id, name, description)
                   VALUES ($1, $2, $3) RETURNING id, name, description""",
                tenant_id,
                data.name.strip(),
                data.description,
            )
        except Exception as e:
            if "unique" in str(e).lower():
                raise HTTPException(400, f"Grup gaji '{data.name}' sudah ada")
            raise
        return {"success": True, "data": {"id": str(row["id"]), "name": row["name"]}}


@router.put("/{group_id}")
async def update_pay_group(request: Request, group_id: str, data: PayGroupUpdate):
    """Update pay group name/description."""
    user = request.state.user
    tenant_id = user.get("tenant_id")

    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "SELECT set_config('app.tenant_id', $1, true)", str(tenant_id)
        )

        existing = await conn.fetchrow(
            "SELECT is_default FROM pay_groups WHERE id = $1 AND tenant_id = $2",
            group_id,
            tenant_id,
        )
        if not existing:
            raise HTTPException(404, "Grup gaji tidak ditemukan")

        updates = []
        params = [group_id, tenant_id]
        if data.name is not None:
            params.append(data.name.strip())
            updates.append(f"name = ${len(params)}")
        if data.description is not None:
            params.append(data.description)
            updates.append(f"description = ${len(params)}")
        updates.append("updated_at = NOW()")

        await conn.execute(
            f"UPDATE pay_groups SET {', '.join(updates)} WHERE id = $1 AND tenant_id = $2",
            *params,
        )
        return {"success": True}


@router.delete("/{group_id}")
async def delete_pay_group(request: Request, group_id: str):
    """Deactivate pay group. Cannot delete default group or group with employees."""
    user = request.state.user
    tenant_id = user.get("tenant_id")

    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "SELECT set_config('app.tenant_id', $1, true)", str(tenant_id)
        )

        row = await conn.fetchrow(
            "SELECT is_default FROM pay_groups WHERE id = $1 AND tenant_id = $2",
            group_id,
            tenant_id,
        )
        if not row:
            raise HTTPException(404, "Grup gaji tidak ditemukan")
        if row["is_default"]:
            raise HTTPException(400, "Tidak bisa menghapus grup default")

        emp_count = await conn.fetchval(
            "SELECT COUNT(*) FROM employees WHERE pay_group_id = $1 AND is_active = true",
            group_id,
        )
        if emp_count > 0:
            raise HTTPException(
                400, f"Grup masih memiliki {emp_count} karyawan aktif. Pindahkan dulu."
            )

        await conn.execute(
            "UPDATE pay_groups SET is_active = false, updated_at = NOW() WHERE id = $1",
            group_id,
        )
        return {"success": True}


# --- Access Management ---


@router.get("/access/{member_id}")
async def get_user_pay_group_access(request: Request, member_id: str):
    """Get which pay groups a user can access."""
    user = request.state.user
    tenant_id = user.get("tenant_id")

    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "SELECT set_config('app.tenant_id', $1, true)", str(tenant_id)
        )

        # Get user_id from member_id
        member = await conn.fetchrow(
            "SELECT user_id FROM user_tenant_roles WHERE id = $1 AND tenant_id = $2",
            member_id,
            tenant_id,
        )
        if not member:
            raise HTTPException(404, "Member tidak ditemukan")

        rows = await conn.fetch(
            """SELECT pay_group_id FROM user_pay_group_access
               WHERE user_id = $1 AND tenant_id = $2 AND revoked_at IS NULL""",
            str(member["user_id"]),
            tenant_id,
        )
        return {"data": [str(r["pay_group_id"]) for r in rows]}


@router.put("/access/{member_id}")
async def set_user_pay_group_access(
    request: Request, member_id: str, data: PayGroupAccessUpdate
):
    """Set which pay groups a user can access. Replaces all existing access."""
    user = request.state.user
    tenant_id = user.get("tenant_id")
    grantor_id = user.get("user_id")

    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "SELECT set_config('app.tenant_id', $1, true)", str(tenant_id)
        )

        member = await conn.fetchrow(
            "SELECT user_id FROM user_tenant_roles WHERE id = $1 AND tenant_id = $2",
            member_id,
            tenant_id,
        )
        if not member:
            raise HTTPException(404, "Member tidak ditemukan")

        target_user_id = str(member["user_id"])

        # Revoke all existing access
        await conn.execute(
            """UPDATE user_pay_group_access SET revoked_at = NOW()
               WHERE user_id = $1 AND tenant_id = $2 AND revoked_at IS NULL""",
            target_user_id,
            tenant_id,
        )

        # Grant new access
        for pg_id in data.pay_group_ids:
            await conn.execute(
                """INSERT INTO user_pay_group_access (user_id, tenant_id, pay_group_id, granted_by)
                   VALUES ($1, $2, $3, $4)
                   ON CONFLICT (user_id, tenant_id, pay_group_id)
                   DO UPDATE SET revoked_at = NULL, granted_by = $4, created_at = NOW()""",
                target_user_id,
                tenant_id,
                pg_id,
                str(grantor_id),
            )

        return {"success": True}
