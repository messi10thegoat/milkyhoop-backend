"""
Team Members Router - User/Team Management for Tenant
Manages user_tenant_roles and provides team member CRUD operations

Tables:
- user_tenant_roles: Links users to tenants with specific roles
- roles: Role definitions with hierarchy
- User: User profile data (name, email, avatar)
"""
import logging
import uuid
from typing import Optional, List
from fastapi import APIRouter, Request, Query, HTTPException
from pydantic import BaseModel
from datetime import datetime
import asyncpg

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/team-members", tags=["team-members"])


# === DATABASE POOL ===
_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
    """Get or create database connection pool"""
    global _pool
    if _pool is None:
        logger.info("Creating team_members database connection pool...")
        _pool = await asyncpg.create_pool(
            host="postgres",
            port=5432,
            user="postgres",
            password="Proyek771977",
            database="milkydb",
            min_size=2,
            max_size=10,
            command_timeout=30,
        )
        logger.info("Team members database connection pool created")
    return _pool


# === SCHEMAS ===

class RoleResponse(BaseModel):
    id: str
    code: str
    name: str
    description: Optional[str] = None
    hierarchy_level: int = 0
    is_system: bool = False
    is_active: bool = True
    approval_limit: int = 0


class TeamMemberResponse(BaseModel):
    id: str
    user_id: str
    email: Optional[str] = None
    name: Optional[str] = None
    fullname: Optional[str] = None
    avatar_url: Optional[str] = None
    role_id: str
    role_code: str
    role_name: str
    hierarchy_level: int = 0
    is_primary: bool = False
    assigned_at: Optional[str] = None
    assigned_by: Optional[str] = None

class TeamMemberListResponse(BaseModel):
    success: bool = True
    data: List[TeamMemberResponse]
    total: int
    page: int
    per_page: int
    has_more: bool


class InviteMemberRequest(BaseModel):
    email: str  # Email address
    role_id: str
    name: Optional[str] = None


class UpdateRoleRequest(BaseModel):
    role_id: str


class RoleListResponse(BaseModel):
    success: bool = True
    data: List[RoleResponse]


# === HELPERS ===

def _get_user_context(request: Request) -> dict:
    """Extract user context from request state"""
    if not hasattr(request.state, "user") or not request.state.user:
        raise HTTPException(status_code=401, detail="Authentication required")
    user = request.state.user
    tenant_id = user.get("tenant_id")
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Invalid user context - missing tenant_id")
    return user


def _validate_uuid(value: str, field_name: str = "ID") -> str:
    """Validate that a string is a valid UUID format"""
    try:
        uuid.UUID(value)
        return value
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid {field_name} format")


async def _check_owner_or_manager(conn, tenant_id: str, user_id: str) -> bool:
    """Check if user has OWNER or MANAGER role in tenant"""
    result = await conn.fetchval("""
        SELECT EXISTS (
            SELECT 1 FROM user_tenant_roles utr
            JOIN roles r ON r.id = utr.role_id
            WHERE utr.tenant_id = $1
            AND utr.user_id = $2
            AND r.code IN ('OWNER', 'MANAGER', 'FINANCE_MGR', 'ADMIN')
            AND r.is_active = TRUE
        )
    """, tenant_id, user_id)
    return result

async def _get_user_role_hierarchy(conn, tenant_id: str, user_id: str) -> int:
    """Get the highest hierarchy level of user's roles (lower = more powerful)"""
    result = await conn.fetchval("""
        SELECT MIN(r.hierarchy_level)
        FROM user_tenant_roles utr
        JOIN roles r ON r.id = utr.role_id
        WHERE utr.tenant_id = $1 AND utr.user_id = $2
    """, tenant_id, user_id)
    return result if result is not None else 999


# === ENDPOINTS ===

@router.get("")
async def list_team_members(
    request: Request,
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    search: Optional[str] = Query(default=None),
    role_id: Optional[str] = Query(default=None),
):
    """List all team members in tenant with their roles."""
    try:
        user = _get_user_context(request)
        tenant_id = user["tenant_id"]

        pool = await get_pool()
        async with pool.acquire() as conn:
            params = [tenant_id]
            param_idx = 2

            where_clause = "WHERE utr.tenant_id = $1"

            if search:
                where_clause += f" AND (u.email ILIKE ${param_idx} OR u.name ILIKE ${param_idx} OR u.fullname ILIKE ${param_idx})"
                params.append(f"%{search}%")
                param_idx += 1

            if role_id:
                where_clause += f" AND utr.role_id = ${param_idx}"
                params.append(role_id)
                param_idx += 1

            count_query = f'SELECT COUNT(*) FROM user_tenant_roles utr LEFT JOIN "User" u ON u.id = utr.user_id::text {where_clause}'
            total = await conn.fetchval(count_query, *params)

            query = f'''
                SELECT
                    utr.id, utr.user_id, u.email, u.name, u.fullname,
                    u."avatarUrl" as avatar_url, utr.role_id,
                    r.code as role_code, r.name as role_name,
                    r.hierarchy_level, utr.is_primary, utr.assigned_at, utr.assigned_by
                FROM user_tenant_roles utr
                LEFT JOIN "User" u ON u.id = utr.user_id::text
                LEFT JOIN roles r ON r.id = utr.role_id
                {where_clause}
                ORDER BY r.hierarchy_level ASC, u.name ASC
                LIMIT ${param_idx} OFFSET ${param_idx + 1}
            '''
            params.extend([per_page, (page - 1) * per_page])

            rows = await conn.fetch(query, *params)

            members = [
                TeamMemberResponse(
                    id=str(row["id"]), user_id=str(row["user_id"]),
                    email=row["email"], name=row["name"], fullname=row["fullname"],
                    avatar_url=row["avatar_url"], role_id=str(row["role_id"]),
                    role_code=row["role_code"] or "", role_name=row["role_name"] or "",
                    hierarchy_level=row["hierarchy_level"] or 0,
                    is_primary=row["is_primary"] or False,
                    assigned_at=row["assigned_at"].isoformat() if row["assigned_at"] else None,
                    assigned_by=str(row["assigned_by"]) if row["assigned_by"] else None,
                ) for row in rows
            ]

            return TeamMemberListResponse(
                data=members, total=total or 0, page=page,
                per_page=per_page, has_more=(page * per_page) < (total or 0)
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing team members: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to list team members: {str(e)}")

@router.get("/roles/list", response_model=RoleListResponse)
async def list_roles(request: Request):
    """List all available roles for the tenant"""
    try:
        user = _get_user_context(request)
        tenant_id = user["tenant_id"]

        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, code, name, description, hierarchy_level, is_system, is_active, approval_limit FROM roles WHERE (tenant_id = $1 OR tenant_id = '__SYSTEM__') AND is_active = TRUE ORDER BY hierarchy_level ASC, name ASC",
                tenant_id
            )

            roles = [
                RoleResponse(
                    id=str(row["id"]), code=row["code"], name=row["name"],
                    description=row["description"], hierarchy_level=row["hierarchy_level"] or 0,
                    is_system=row["is_system"] or False, is_active=row["is_active"],
                    approval_limit=row["approval_limit"] or 0,
                ) for row in rows
            ]

            return RoleListResponse(data=roles)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing roles: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to list roles: {str(e)}")


@router.get("/{member_id}")
async def get_team_member(request: Request, member_id: str):
    """Get team member detail by user_tenant_role ID"""
    # Validate UUID format first
    _validate_uuid(member_id, "member ID")

    try:
        user = _get_user_context(request)
        tenant_id = user["tenant_id"]

        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow('''
                SELECT
                    utr.id, utr.user_id, u.email, u.name, u.fullname,
                    u."avatarUrl" as avatar_url, utr.role_id,
                    r.code as role_code, r.name as role_name,
                    r.hierarchy_level, utr.is_primary, utr.assigned_at, utr.assigned_by
                FROM user_tenant_roles utr
                LEFT JOIN "User" u ON u.id = utr.user_id::text
                LEFT JOIN roles r ON r.id = utr.role_id
                WHERE utr.id = $1 AND utr.tenant_id = $2
            ''', member_id, tenant_id)

            if not row:
                raise HTTPException(status_code=404, detail="Team member not found")

            return {
                "success": True,
                "data": TeamMemberResponse(
                    id=str(row["id"]), user_id=str(row["user_id"]),
                    email=row["email"], name=row["name"], fullname=row["fullname"],
                    avatar_url=row["avatar_url"], role_id=str(row["role_id"]),
                    role_code=row["role_code"] or "", role_name=row["role_name"] or "",
                    hierarchy_level=row["hierarchy_level"] or 0,
                    is_primary=row["is_primary"] or False,
                    assigned_at=row["assigned_at"].isoformat() if row["assigned_at"] else None,
                    assigned_by=str(row["assigned_by"]) if row["assigned_by"] else None,
                )
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting team member: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get team member: {str(e)}")

@router.post("/invite")
async def invite_team_member(request: Request, data: InviteMemberRequest):
    """Invite a new team member. Only OWNER/MANAGER can invite."""
    try:
        user = _get_user_context(request)
        tenant_id = user["tenant_id"]
        current_user_id = user["user_id"]

        pool = await get_pool()
        async with pool.acquire() as conn:
            has_permission = await _check_owner_or_manager(conn, tenant_id, current_user_id)
            if not has_permission:
                raise HTTPException(status_code=403, detail="Only Owner or Manager can invite team members")

            role_row = await conn.fetchrow(
                "SELECT id, hierarchy_level, code, name, is_active FROM roles WHERE id = $1 AND tenant_id = $2",
                data.role_id, tenant_id
            )

            if not role_row:
                raise HTTPException(status_code=400, detail="Invalid role_id")
            if not role_row["is_active"]:
                raise HTTPException(status_code=400, detail="Role is not active")

            user_hierarchy = await _get_user_role_hierarchy(conn, tenant_id, current_user_id)
            if role_row["hierarchy_level"] < user_hierarchy:
                raise HTTPException(status_code=403, detail="Cannot assign role higher than your own")

            user_row = await conn.fetchrow('SELECT id FROM "User" WHERE email = $1', data.email)

            if user_row:
                target_user_id = user_row["id"]
            else:
                from uuid import uuid4
                new_user_id = str(uuid4())
                await conn.execute(
                    'INSERT INTO "User" (id, email, name, "createdAt", "updatedAt", "tenantId") VALUES ($1, $2, $3, NOW(), NOW(), $4)',
                    new_user_id, data.email, data.name or data.email.split("@")[0], tenant_id
                )
                target_user_id = new_user_id

            existing = await conn.fetchval(
                "SELECT id FROM user_tenant_roles WHERE user_id = $1::uuid AND tenant_id = $2",
                target_user_id, tenant_id
            )

            if existing:
                raise HTTPException(status_code=400, detail="User is already a member of this tenant")

            new_role_id = await conn.fetchval(
                "INSERT INTO user_tenant_roles (user_id, tenant_id, role_id, assigned_by) VALUES ($1::uuid, $2, $3, $4::uuid) RETURNING id",
                target_user_id, tenant_id, data.role_id, current_user_id
            )

            return {
                "success": True,
                "message": f"Successfully invited {data.email} as {role_row['name']}",
                "data": {
                    "id": str(new_role_id), "user_id": str(target_user_id),
                    "email": data.email, "role_code": role_row["code"], "role_name": role_row["name"]
                }
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error inviting team member: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to invite team member: {str(e)}")

@router.patch("/{member_id}/role")
async def update_member_role(request: Request, member_id: str, data: UpdateRoleRequest):
    """Update team member role. Only OWNER/MANAGER can update."""
    # Validate UUID format first
    _validate_uuid(member_id, "member ID")

    try:
        user = _get_user_context(request)
        tenant_id = user["tenant_id"]
        current_user_id = user["user_id"]

        pool = await get_pool()
        async with pool.acquire() as conn:
            has_permission = await _check_owner_or_manager(conn, tenant_id, current_user_id)
            if not has_permission:
                raise HTTPException(status_code=403, detail="Only Owner or Manager can update member roles")

            member_row = await conn.fetchrow(
                "SELECT utr.id, utr.user_id, r.hierarchy_level as current_level, r.code as current_code FROM user_tenant_roles utr JOIN roles r ON r.id = utr.role_id WHERE utr.id = $1 AND utr.tenant_id = $2",
                member_id, tenant_id
            )

            if not member_row:
                raise HTTPException(status_code=404, detail="Team member not found")

            new_role = await conn.fetchrow(
                "SELECT id, code, name, hierarchy_level, is_active FROM roles WHERE id = $1 AND tenant_id = $2",
                data.role_id, tenant_id
            )

            if not new_role:
                raise HTTPException(status_code=400, detail="Invalid role_id")
            if not new_role["is_active"]:
                raise HTTPException(status_code=400, detail="Target role is not active")

            user_hierarchy = await _get_user_role_hierarchy(conn, tenant_id, current_user_id)

            if member_row["current_level"] < user_hierarchy:
                raise HTTPException(status_code=403, detail="Cannot modify a member with higher role than yours")
            if new_role["hierarchy_level"] < user_hierarchy:
                raise HTTPException(status_code=403, detail="Cannot assign role higher than your own")

            if member_row["current_code"] == "OWNER" and new_role["code"] != "OWNER":
                owner_count = await conn.fetchval(
                    "SELECT COUNT(*) FROM user_tenant_roles utr JOIN roles r ON r.id = utr.role_id WHERE utr.tenant_id = $1 AND r.code = 'OWNER'",
                    tenant_id
                )
                if owner_count <= 1:
                    raise HTTPException(status_code=400, detail="Cannot demote the last owner. Transfer ownership first.")

            await conn.execute(
                "UPDATE user_tenant_roles SET role_id = $1, assigned_at = NOW(), assigned_by = $2::uuid WHERE id = $3",
                data.role_id, current_user_id, member_id
            )

            return {
                "success": True,
                "message": f"Successfully updated role to {new_role['name']}",
                "data": {
                    "id": member_id, "user_id": str(member_row["user_id"]),
                    "new_role_code": new_role["code"], "new_role_name": new_role["name"]
                }
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating member role: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to update member role: {str(e)}")

@router.delete("/{member_id}")
async def remove_team_member(request: Request, member_id: str):
    """Remove team member from tenant. Only OWNER/MANAGER can remove."""
    # Validate UUID format first
    _validate_uuid(member_id, "member ID")

    try:
        user = _get_user_context(request)
        tenant_id = user["tenant_id"]
        current_user_id = user["user_id"]

        pool = await get_pool()
        async with pool.acquire() as conn:
            has_permission = await _check_owner_or_manager(conn, tenant_id, current_user_id)
            if not has_permission:
                raise HTTPException(status_code=403, detail="Only Owner or Manager can remove team members")

            member_row = await conn.fetchrow('''
                SELECT utr.id, utr.user_id, r.hierarchy_level, r.code as role_code, u.email
                FROM user_tenant_roles utr
                JOIN roles r ON r.id = utr.role_id
                LEFT JOIN "User" u ON u.id = utr.user_id::text
                WHERE utr.id = $1 AND utr.tenant_id = $2
            ''', member_id, tenant_id)

            if not member_row:
                raise HTTPException(status_code=404, detail="Team member not found")

            if str(member_row["user_id"]) == current_user_id:
                raise HTTPException(status_code=400, detail="Cannot remove yourself. Use Leave Tenant instead.")

            user_hierarchy = await _get_user_role_hierarchy(conn, tenant_id, current_user_id)
            if member_row["hierarchy_level"] < user_hierarchy:
                raise HTTPException(status_code=403, detail="Cannot remove a member with higher role than yours")

            if member_row["role_code"] == "OWNER":
                owner_count = await conn.fetchval(
                    "SELECT COUNT(*) FROM user_tenant_roles utr JOIN roles r ON r.id = utr.role_id WHERE utr.tenant_id = $1 AND r.code = 'OWNER'",
                    tenant_id
                )
                if owner_count <= 1:
                    raise HTTPException(status_code=400, detail="Cannot remove the last owner. Transfer ownership first.")

            await conn.execute("DELETE FROM user_tenant_roles WHERE id = $1", member_id)

            return {
                "success": True,
                "message": f"Successfully removed {member_row['email'] or 'member'} from team",
                "data": {"id": member_id, "user_id": str(member_row["user_id"]), "email": member_row["email"]}
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error removing team member: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to remove team member: {str(e)}")
