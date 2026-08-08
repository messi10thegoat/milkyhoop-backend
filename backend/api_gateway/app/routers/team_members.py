"""
Team Members Router - User/Team Management for Tenant
Manages user_tenant_roles and provides team member CRUD operations

Tables:
- user_tenant_roles: Links users to tenants with specific roles
- roles: Role definitions with hierarchy
- User: User profile data (name, email, avatar)
- user_permission_overrides: Per-user per-module access overrides
"""
import json
import os
import secrets
from asyncpg.exceptions import UniqueViolationError
import logging
import uuid
from typing import Optional, List
from fastapi import APIRouter, Request, Query, HTTPException
from pydantic import BaseModel
import asyncpg

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/team-members", tags=["team-members"])

# Access level → actions array (used by overrides endpoint)
_ACCESS_ACTIONS = {
    "full": ["C", "R", "U", "D", "V", "A", "P", "E"],
    "view": ["R", "E"],
    "none": [],
}


# === DATABASE POOL ===


async def get_pool() -> asyncpg.Pool:
    """Get singleton connection pool (Law 32)."""
    from ..services.db_pool import get_db_pool

    return await get_db_pool()


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
    module_overrides: dict = {}


class TeamMemberListResponse(BaseModel):
    success: bool = True
    data: List[TeamMemberResponse]
    total: int
    page: int
    per_page: int
    has_more: bool


class InviteMemberRequest(BaseModel):
    email: str
    role_id: str
    name: Optional[str] = None


class UpdateRoleRequest(BaseModel):
    role_id: str


class RoleListResponse(BaseModel):
    success: bool = True
    data: List[RoleResponse]


class UpdateOverridesRequest(BaseModel):
    module_overrides: dict  # e.g. {"INVOICE": "full", "REPORT": "none", "PAYROLL": "view"}


# === HELPERS ===


def _get_user_context(request: Request) -> dict:
    """Extract user context from request state"""
    if not hasattr(request.state, "user") or not request.state.user:
        raise HTTPException(status_code=401, detail="Authentication required")
    user = request.state.user
    tenant_id = user.get("tenant_id")
    if not tenant_id:
        raise HTTPException(
            status_code=401, detail="Invalid user context - missing tenant_id"
        )
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
    result = await conn.fetchval(
        """
        SELECT EXISTS (
            SELECT 1 FROM user_tenant_roles utr
            JOIN roles r ON r.id = utr.role_id
            WHERE utr.tenant_id = $1
            AND utr.user_id = $2
            AND r.code IN ('OWNER', 'MANAGER', 'FINANCE_MGR', 'ADMIN')
            AND r.is_active = TRUE
        )
    """,
        tenant_id,
        user_id,
    )
    return result


async def _get_user_role_hierarchy(conn, tenant_id: str, user_id: str) -> int:
    """Get the highest hierarchy level of user's roles (lower = more powerful)"""
    result = await conn.fetchval(
        """
        SELECT MIN(r.hierarchy_level)
        FROM user_tenant_roles utr
        JOIN roles r ON r.id = utr.role_id
        WHERE utr.tenant_id = $1 AND utr.user_id = $2
    """,
        tenant_id,
        user_id,
    )
    return result if result is not None else 999


async def _get_user_role_code(conn, user_id: str, tenant_id: str) -> Optional[str]:
    """Kode peran utama pengguna di tenant, atau None.

    DULU: query ini TIDAK memfilter status sama sekali, sehingga keanggotaan
    yang dinonaktifkan tetap menghasilkan peran untuk pemeriksaan otorisasi di
    baris 681/705 (caller_role / target_role). Sekarang memakai query kanonik
    yang menghormati status DAN roles.is_active.
    None = tidak boleh; pemanggil sudah memperlakukannya begitu.
    """
    from ..services.role_resolution import try_resolve_business_role

    return await try_resolve_business_role(conn, user_id, tenant_id)


# Reverse map: DB module -> access level string
def _actions_to_level(actions: list) -> str:
    if not actions or len(actions) == 0:
        return "none"
    if "C" in actions:
        return "full"
    return "view"


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

            query = f"""
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
            """
            params.extend([per_page, (page - 1) * per_page])

            rows = await conn.fetch(query, *params)

            # Fetch overrides for all members in one query
            user_ids = [str(row["user_id"]) for row in rows]
            override_rows = (
                await conn.fetch(
                    "SELECT user_id, module, actions FROM user_permission_overrides WHERE user_id = ANY($1) AND tenant_id = $2",
                    user_ids,
                    tenant_id,
                )
                if user_ids
                else []
            )

            # Build user_id -> {DB_MODULE: access_level} map
            user_overrides: dict = {}
            for orow in override_rows:
                uid = str(orow["user_id"])
                if uid not in user_overrides:
                    user_overrides[uid] = {}
                actions = list(orow["actions"]) if orow["actions"] else []
                user_overrides[uid][orow["module"]] = _actions_to_level(actions)

            members = [
                TeamMemberResponse(
                    id=str(row["id"]),
                    user_id=str(row["user_id"]),
                    email=row["email"],
                    name=row["name"],
                    fullname=row["fullname"],
                    avatar_url=row["avatar_url"],
                    role_id=str(row["role_id"]),
                    role_code=row["role_code"] or "",
                    role_name=row["role_name"] or "",
                    hierarchy_level=row["hierarchy_level"] or 0,
                    is_primary=row["is_primary"] or False,
                    assigned_at=row["assigned_at"].isoformat()
                    if row["assigned_at"]
                    else None,
                    assigned_by=str(row["assigned_by"]) if row["assigned_by"] else None,
                    module_overrides=user_overrides.get(str(row["user_id"]), {}),
                )
                for row in rows
            ]

            return TeamMemberListResponse(
                data=members,
                total=total or 0,
                page=page,
                per_page=per_page,
                has_more=(page * per_page) < (total or 0),
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing team members: {e}")
        raise HTTPException(
            status_code=500, detail=f"Failed to list team members: {str(e)}"
        )


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
                tenant_id,
            )

            roles = [
                RoleResponse(
                    id=str(row["id"]),
                    code=row["code"],
                    name=row["name"],
                    description=row["description"],
                    hierarchy_level=row["hierarchy_level"] or 0,
                    is_system=row["is_system"] or False,
                    is_active=row["is_active"],
                    approval_limit=row["approval_limit"] or 0,
                )
                for row in rows
            ]

            return RoleListResponse(data=roles)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing roles: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to list roles: {str(e)}")


# CATATAN URUTAN RUTE: ketiga endpoint /invitations HARUS berada SEBELUM
# @router.get("/{member_id}") — kalau tidak, "/invitations" akan tertangkap
# sebagai member_id dan tak pernah sampai ke sini.
@router.get("/invitations")
async def list_invitations(request: Request):
    """Undangan yang masih menunggu, dengan link-nya (email tidak dikirim)."""
    user = _get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        if not await _check_owner_or_manager(conn, user["tenant_id"], user["user_id"]):
            raise HTTPException(status_code=403, detail="Hanya Pemilik atau Manajer")
        rows = await conn.fetch(
            """SELECT ti.id, ti.email, ti.name, ti.status, ti.expires_at, ti.created_at,
                      ti.invite_token, r.code AS role_code, r.name AS role_name
               FROM team_invitations ti JOIN roles r ON r.id = ti.role_id
               WHERE ti.tenant_id = $1 AND ti.status = 'pending'
               ORDER BY ti.created_at DESC""",
            user["tenant_id"],
        )
    return {
        "success": True,
        "data": [
            {
                "id": str(r["id"]),
                "email": r["email"],
                "name": r["name"],
                "role_code": r["role_code"],
                "role_name": r["role_name"],
                "expires_at": r["expires_at"].isoformat(),
                "created_at": r["created_at"].isoformat(),
                "invite_link": _invite_link(r["invite_token"]),
            }
            for r in rows
        ],
    }


@router.delete("/invitations/{invitation_id}")
async def revoke_invitation(request: Request, invitation_id: str):
    """Batalkan undangan. Token lama langsung mati (410)."""
    _validate_uuid(invitation_id, "invitation ID")
    user = _get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        if not await _check_owner_or_manager(conn, user["tenant_id"], user["user_id"]):
            raise HTTPException(status_code=403, detail="Hanya Pemilik atau Manajer")
        row = await conn.fetchrow(
            """UPDATE team_invitations SET status = 'revoked', revoked_at = NOW()
               WHERE id = $1 AND tenant_id = $2 AND status = 'pending'
               RETURNING id, email""",
            invitation_id,
            user["tenant_id"],
        )
    if not row:
        raise HTTPException(
            status_code=404, detail="Undangan tidak ditemukan atau sudah tidak menunggu"
        )
    return {"success": True, "data": {"id": str(row["id"]), "email": row["email"]}}


@router.post("/invitations/{invitation_id}/resend")
async def resend_invitation(request: Request, invitation_id: str):
    """Terbitkan ulang undangan: token DIPUTAR, token lama MATI.

    KENAPA DIPUTAR, bukan menampilkan ulang token yang sama:
    alasan orang menekan "kirim ulang" biasanya karena link yang lama bermasalah
    — salah kirim, diteruskan ke orang lain, tercecer di riwayat chat. Kalau
    token lama tetap hidup, tombol ini TIDAK MEMPERBAIKI APA PUN; ia hanya
    menambah satu link yang sama-sama berlaku. Memutar token membuatnya benar-
    benar sebuah pemulihan, dan namanya jujur.
    Konsekuensi yang diuji di gate: token LAMA -> 410.
    """
    _validate_uuid(invitation_id, "invitation ID")
    user = _get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        if not await _check_owner_or_manager(conn, user["tenant_id"], user["user_id"]):
            raise HTTPException(status_code=403, detail="Hanya Pemilik atau Manajer")
        row = await conn.fetchrow(
            """UPDATE team_invitations
               SET invite_token = $3, expires_at = NOW() + INTERVAL '7 days'
               WHERE id = $1 AND tenant_id = $2 AND status = 'pending'
               RETURNING id, email, expires_at""",
            invitation_id,
            user["tenant_id"],
            secrets.token_urlsafe(32)[:64],
        )
        if not row:
            raise HTTPException(
                status_code=404,
                detail="Undangan tidak ditemukan atau sudah tidak menunggu",
            )
        token = await conn.fetchval(
            "SELECT invite_token FROM team_invitations WHERE id = $1", row["id"]
        )
    return {
        "success": True,
        "message": "Link undangan baru dibuat. Link yang lama sudah tidak berlaku.",
        "data": {
            "id": str(row["id"]),
            "email": row["email"],
            "invite_link": _invite_link(token),
            "expires_at": row["expires_at"].isoformat(),
            "email_sent": False,
        },
    }


@router.get("/{member_id}")
async def get_team_member(request: Request, member_id: str):
    """Get team member detail by user_tenant_role ID"""
    _validate_uuid(member_id, "member ID")

    try:
        user = _get_user_context(request)
        tenant_id = user["tenant_id"]

        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT
                    utr.id, utr.user_id, u.email, u.name, u.fullname,
                    u."avatarUrl" as avatar_url, utr.role_id,
                    r.code as role_code, r.name as role_name,
                    r.hierarchy_level, utr.is_primary, utr.assigned_at, utr.assigned_by
                FROM user_tenant_roles utr
                LEFT JOIN "User" u ON u.id = utr.user_id::text
                LEFT JOIN roles r ON r.id = utr.role_id
                WHERE utr.id = $1 AND utr.tenant_id = $2
            """,
                member_id,
                tenant_id,
            )

            if not row:
                raise HTTPException(status_code=404, detail="Team member not found")

            return {
                "success": True,
                "data": TeamMemberResponse(
                    id=str(row["id"]),
                    user_id=str(row["user_id"]),
                    email=row["email"],
                    name=row["name"],
                    fullname=row["fullname"],
                    avatar_url=row["avatar_url"],
                    role_id=str(row["role_id"]),
                    role_code=row["role_code"] or "",
                    role_name=row["role_name"] or "",
                    hierarchy_level=row["hierarchy_level"] or 0,
                    is_primary=row["is_primary"] or False,
                    assigned_at=row["assigned_at"].isoformat()
                    if row["assigned_at"]
                    else None,
                    assigned_by=str(row["assigned_by"]) if row["assigned_by"] else None,
                ),
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting team member: {e}")
        raise HTTPException(
            status_code=500, detail=f"Failed to get team member: {str(e)}"
        )


def _invite_link(token: str) -> str:
    base = os.getenv("APP_BASE_URL", "https://milkyhoop.com").rstrip("/")
    return f"{base}/invite/{token}"


async def _load_invitable_role(conn, role_id: str, tenant_id: str, current_user_id: str):
    """Peran + guard hierarki. Dipakai invite dan resend supaya tak bisa beda.

    AKAR BUG LAMA (400 Invalid role_id, sejak endpoint ini ditulis): query
    memakai `WHERE id = $1 AND tenant_id = $2` dengan tenant PEMANGGIL. Tapi
    [SQL] SELECT tenant_id, count(*) FROM roles GROUP BY 1 -> `__SYSTEM__ | 14`:
    ke-14 peran sistem hidup di '__SYSTEM__', tak satu pun di bawah tenant.
    Query itu TAK PERNAH BISA COCOK — untuk siapa pun, selamanya. Undangan tak
    pernah sekali pun berhasil.
    """
    row = await conn.fetchrow(
        """SELECT id, hierarchy_level, code, name, is_active FROM roles
           WHERE id = $1 AND tenant_id IN ('__SYSTEM__', $2)""",
        role_id,
        tenant_id,
    )
    if not row:
        raise HTTPException(status_code=400, detail="Peran tidak dikenal")
    if not row["is_active"]:
        raise HTTPException(status_code=400, detail="Peran sedang tidak aktif")
    if row["code"] == "OWNER":
        # OWNER = satu per tenant, NON_ASSIGNABLE. Gunakan Transfer Kepemilikan.
        raise HTTPException(
            status_code=403,
            detail="Peran Pemilik tidak dapat diundang. Gunakan Transfer Kepemilikan.",
        )
    user_hierarchy = await _get_user_role_hierarchy(conn, tenant_id, current_user_id)
    if row["hierarchy_level"] < user_hierarchy:
        raise HTTPException(
            status_code=403, detail="Tidak dapat memberi peran lebih tinggi dari Anda"
        )
    return row


@router.post("/invite")
async def invite_team_member(request: Request, data: InviteMemberRequest):
    """Undang anggota: TULIS UNDANGAN, JANGAN berikan keanggotaan.

    PERILAKU LAMA (diganti, bukan ditambal):
        1. INSERT "User" TANPA passwordHash  -> akun yang tak akan pernah bisa login
        2. INSERT user_tenant_roles langsung -> keanggotaan penuh TANPA persetujuan
    Nol baris team_invitations ditulis; jalur terima/tolak tak pernah dilalui.
    Yang menahan jalur itu selama ini justru galat 400 di atas — sehingga
    MEMPERBAIKI 400 SAJA berarti MENGAKTIFKAN pembuatan anggota hantu.

    Keanggotaan sekarang lahir HANYA di invite_public.accept_invite.
    Dua pagar di gate menjaga sifat itu: sesudah endpoint ini dipanggil,
    user_tenant_roles TIDAK bertambah dan NOL "User" tanpa passwordHash tercipta.

    EMAIL: tidak dikirim. RESEND_API_KEY tidak terpasang di lingkungan ini, dan
    email_service mengembalikan SUKSES saat kunci kosong (hanya mencatat
    warning) — jadi memanggilnya akan menghasilkan "terkirim" ke ruang hampa.
    Endpoint mengembalikan `invite_link` untuk disalin pemilik. Menaikkannya ke
    pengiriman email nanti = menambah satu pemanggilan; kontrak, skema, dan
    alurnya tidak berubah. Lihat BE-SIGNUP-EMAIL-NEVER-SENT-001.
    """
    user = _get_user_context(request)
    tenant_id = user["tenant_id"]
    current_user_id = user["user_id"]

    email = (data.email or "").strip().lower()
    if "@" not in email:
        raise HTTPException(status_code=400, detail="Alamat email tidak valid")

    pool = await get_pool()
    async with pool.acquire() as conn:
        if not await _check_owner_or_manager(conn, tenant_id, current_user_id):
            raise HTTPException(
                status_code=403,
                detail="Hanya Pemilik atau Manajer yang dapat mengundang anggota",
            )
        role_row = await _load_invitable_role(conn, data.role_id, tenant_id, current_user_id)

        already = await conn.fetchval(
            """SELECT 1 FROM user_tenant_roles utr
               JOIN "User" u ON u.id::uuid = utr.user_id
               WHERE lower(u.email) = $1 AND utr.tenant_id = $2""",
            email,
            tenant_id,
        )
        if already:
            raise HTTPException(
                status_code=409, detail="Orang ini sudah menjadi anggota tim"
            )

        token = secrets.token_urlsafe(32)[:64]
        try:
            inv = await conn.fetchrow(
                """INSERT INTO team_invitations
                       (tenant_id, email, name, role_id, module_overrides,
                        invite_token, expires_at, status, invited_by)
                   VALUES ($1, $2, $3, $4, $5::jsonb, $6, NOW() + INTERVAL '7 days',
                           'pending', $7)
                   RETURNING id, expires_at""",
                tenant_id,
                email,
                data.name,
                role_row["id"],
                json.dumps(getattr(data, "module_overrides", None) or {}),
                token,
                current_user_id,
            )
        except UniqueViolationError:
            # Indeks parsial (tenant_id, email) WHERE status='pending'.
            raise HTTPException(
                status_code=409,
                detail="Sudah ada undangan aktif untuk email ini. Batalkan dulu bila ingin mengganti perannya.",
            )

    return {
        "success": True,
        "message": f"Undangan untuk {email} dibuat. Salin dan kirimkan link di bawah.",
        "data": {
            "invitation_id": str(inv["id"]),
            "email": email,
            "role_code": role_row["code"],
            "role_name": role_row["name"],
            "invite_link": _invite_link(token),
            "expires_at": inv["expires_at"].isoformat(),
            "email_sent": False,
        },
    }


@router.patch("/{member_id}/role")
async def update_member_role(request: Request, member_id: str, data: UpdateRoleRequest):
    """Update team member role. Only OWNER/MANAGER can update."""
    _validate_uuid(member_id, "member ID")

    try:
        user = _get_user_context(request)
        tenant_id = user["tenant_id"]
        current_user_id = user["user_id"]

        pool = await get_pool()
        async with pool.acquire() as conn:
            has_permission = await _check_owner_or_manager(
                conn, tenant_id, current_user_id
            )
            if not has_permission:
                raise HTTPException(
                    status_code=403,
                    detail="Only Owner or Manager can update member roles",
                )

            member_row = await conn.fetchrow(
                "SELECT utr.id, utr.user_id, r.hierarchy_level as current_level, r.code as current_code FROM user_tenant_roles utr JOIN roles r ON r.id = utr.role_id WHERE utr.id = $1 AND utr.tenant_id = $2",
                member_id,
                tenant_id,
            )

            if not member_row:
                raise HTTPException(status_code=404, detail="Team member not found")

            new_role = await conn.fetchrow(
                "SELECT id, code, name, hierarchy_level, is_active FROM roles WHERE id = $1 AND tenant_id = $2",
                data.role_id,
                tenant_id,
            )

            if not new_role:
                raise HTTPException(status_code=400, detail="Invalid role_id")
            if not new_role["is_active"]:
                raise HTTPException(status_code=400, detail="Target role is not active")

            user_hierarchy = await _get_user_role_hierarchy(
                conn, tenant_id, current_user_id
            )

            if member_row["current_level"] < user_hierarchy:
                raise HTTPException(
                    status_code=403,
                    detail="Cannot modify a member with higher role than yours",
                )
            if new_role["hierarchy_level"] < user_hierarchy:
                raise HTTPException(
                    status_code=403, detail="Cannot assign role higher than your own"
                )

            if member_row["current_code"] == "OWNER" and new_role["code"] != "OWNER":
                owner_count = await conn.fetchval(
                    "SELECT COUNT(*) FROM user_tenant_roles utr JOIN roles r ON r.id = utr.role_id WHERE utr.tenant_id = $1 AND r.code = 'OWNER'",
                    tenant_id,
                )
                if owner_count <= 1:
                    raise HTTPException(
                        status_code=400,
                        detail="Cannot demote the last owner. Transfer ownership first.",
                    )

            await conn.execute(
                "UPDATE user_tenant_roles SET role_id = $1, assigned_at = NOW(), assigned_by = $2::uuid WHERE id = $3",
                data.role_id,
                current_user_id,
                member_id,
            )

            return {
                "success": True,
                "message": f"Successfully updated role to {new_role['name']}",
                "data": {
                    "id": member_id,
                    "user_id": str(member_row["user_id"]),
                    "new_role_code": new_role["code"],
                    "new_role_name": new_role["name"],
                },
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating member role: {e}")
        raise HTTPException(
            status_code=500, detail=f"Failed to update member role: {str(e)}"
        )


@router.delete("/{member_id}")
async def remove_team_member(request: Request, member_id: str):
    """Remove team member from tenant. Only OWNER/MANAGER can remove."""
    _validate_uuid(member_id, "member ID")

    try:
        user = _get_user_context(request)
        tenant_id = user["tenant_id"]
        current_user_id = user["user_id"]

        pool = await get_pool()
        async with pool.acquire() as conn:
            has_permission = await _check_owner_or_manager(
                conn, tenant_id, current_user_id
            )
            if not has_permission:
                raise HTTPException(
                    status_code=403,
                    detail="Only Owner or Manager can remove team members",
                )

            member_row = await conn.fetchrow(
                """
                SELECT utr.id, utr.user_id, r.hierarchy_level, r.code as role_code, u.email
                FROM user_tenant_roles utr
                JOIN roles r ON r.id = utr.role_id
                LEFT JOIN "User" u ON u.id = utr.user_id::text
                WHERE utr.id = $1 AND utr.tenant_id = $2
            """,
                member_id,
                tenant_id,
            )

            if not member_row:
                raise HTTPException(status_code=404, detail="Team member not found")

            if str(member_row["user_id"]) == current_user_id:
                raise HTTPException(
                    status_code=400,
                    detail="Cannot remove yourself. Use Leave Tenant instead.",
                )

            user_hierarchy = await _get_user_role_hierarchy(
                conn, tenant_id, current_user_id
            )
            if member_row["hierarchy_level"] < user_hierarchy:
                raise HTTPException(
                    status_code=403,
                    detail="Cannot remove a member with higher role than yours",
                )

            if member_row["role_code"] == "OWNER":
                owner_count = await conn.fetchval(
                    "SELECT COUNT(*) FROM user_tenant_roles utr JOIN roles r ON r.id = utr.role_id WHERE utr.tenant_id = $1 AND r.code = 'OWNER'",
                    tenant_id,
                )
                if owner_count <= 1:
                    raise HTTPException(
                        status_code=400,
                        detail="Cannot remove the last owner. Transfer ownership first.",
                    )

            await conn.execute("DELETE FROM user_tenant_roles WHERE id = $1", member_id)

            return {
                "success": True,
                "message": f"Successfully removed {member_row['email'] or 'member'} from team",
                "data": {
                    "id": member_id,
                    "user_id": str(member_row["user_id"]),
                    "email": member_row["email"],
                },
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error removing team member: {e}")
        raise HTTPException(
            status_code=500, detail=f"Failed to remove team member: {str(e)}"
        )


# =====================================================
# MODULE PERMISSION OVERRIDES (Kelola Akses)
# =====================================================


async def _set_member_status(request: Request, member_id: str, new_status: str, verb: str):
    """Inti bersama deactivate/reactivate. Satu jalur, satu set guard.

    CATATAN CACHE: `user_tenant_roles.status` TIDAK di-cache di mana pun.
    PolicyEngine membacanya lewat query langsung di `get_user_context` yang
    dipanggil PER REQUEST; `_permission_cache` hanya menyimpan role_permissions
    (kunci role_id) dan overrides (kunci user:tenant) — keduanya tak bergantung
    pada status. Karena itu perubahan di sini berlaku pada request BERIKUTNYA,
    tanpa invalidasi dan tanpa restart gateway.
    """
    _validate_uuid(member_id, "member ID")

    user = _get_user_context(request)
    tenant_id = user["tenant_id"]
    current_user_id = user["user_id"]

    pool = await get_pool()
    async with pool.acquire() as conn:
        if not await _check_owner_or_manager(conn, tenant_id, current_user_id):
            raise HTTPException(
                status_code=403,
                detail=f"Hanya Pemilik atau Manajer yang dapat {verb} anggota tim",
            )

        member_row = await conn.fetchrow(
            """
            SELECT utr.id, utr.user_id, utr.status, r.code AS role_code,
                   r.hierarchy_level, u.email
            FROM user_tenant_roles utr
            JOIN roles r ON r.id = utr.role_id
            LEFT JOIN "User" u ON u.id = utr.user_id::text
            WHERE utr.id = $1 AND utr.tenant_id = $2
            """,
            member_id,
            tenant_id,
        )
        if not member_row:
            raise HTTPException(status_code=404, detail="Anggota tim tidak ditemukan")

        # OWNER: larangan MUTLAK, bukan "kecuali dia satu-satunya".
        # Aturan bersyarat harus MENGHITUNG untuk memutuskan, dan hitungan itu
        # bisa salah (race, roles.is_active, status). Larangan tanpa syarat tak
        # punya syarat yang bisa salah. Pergantian pemilik punya jalurnya
        # sendiri: POST /transfer-ownership.
        if member_row["role_code"] == "OWNER":
            raise HTTPException(
                status_code=403,
                detail=(
                    "Pemilik bisnis tidak dapat dinonaktifkan. "
                    "Gunakan Transfer Kepemilikan bila ingin mengganti pemilik."
                ),
            )

        if str(member_row["user_id"]) == current_user_id:
            raise HTTPException(
                status_code=403,
                detail=f"Anda tidak dapat {verb} akses Anda sendiri.",
            )

        member_hierarchy = member_row["hierarchy_level"]
        user_hierarchy = await _get_user_role_hierarchy(conn, tenant_id, current_user_id)
        if member_hierarchy < user_hierarchy:
            raise HTTPException(
                status_code=403,
                detail=f"Tidak dapat {verb} anggota dengan peran lebih tinggi dari Anda",
            )

        await conn.execute(
            "UPDATE user_tenant_roles SET status = $1 WHERE id = $2 AND tenant_id = $3",
            new_status,
            member_id,
            tenant_id,
        )

    return {
        "success": True,
        "data": {
            "member_id": member_id,
            "email": member_row["email"],
            "status": new_status,
            "previous_status": member_row["status"],
        },
    }


@router.patch("/{member_id}/deactivate")
async def deactivate_team_member(request: Request, member_id: str):
    """Cabut akses anggota tim. Berlaku pada request BERIKUTNYA milik anggota
    itu — termasuk sesi yang sedang berjalan, bukan hanya login berikutnya."""
    return await _set_member_status(request, member_id, "SUSPENDED", "menonaktifkan")


@router.patch("/{member_id}/reactivate")
async def reactivate_team_member(request: Request, member_id: str):
    """Pulihkan akses anggota tim yang sebelumnya dinonaktifkan."""
    return await _set_member_status(request, member_id, "ACTIVE", "mengaktifkan")


@router.patch("/{member_id}/overrides")
async def update_member_overrides(
    member_id: str, body: UpdateOverridesRequest, request: Request
):
    """Update per-module permission overrides. Only OWNER can call this."""
    _validate_uuid(member_id, "member ID")
    user = _get_user_context(request)
    tenant_id = user["tenant_id"]
    current_user_id = user["user_id"]

    pool = await get_pool()
    async with pool.acquire() as conn:
        # Auth: only OWNER can modify overrides
        caller_role = await _get_user_role_code(conn, current_user_id, tenant_id)
        if caller_role != "OWNER":
            raise HTTPException(
                status_code=403, detail="Hanya Owner yang bisa mengubah akses"
            )

        # Get target member
        member_row = await conn.fetchrow(
            "SELECT id, user_id FROM user_tenant_roles WHERE id = $1 AND tenant_id = $2",
            member_id,
            tenant_id,
        )
        if not member_row:
            raise HTTPException(status_code=404, detail="Member not found")

        target_user_id = str(member_row["user_id"])

        # Cannot override own permissions
        if target_user_id == current_user_id:
            raise HTTPException(
                status_code=400, detail="Tidak bisa mengubah akses sendiri"
            )

        # Cannot override OWNER's permissions
        target_role = await _get_user_role_code(conn, target_user_id, tenant_id)
        if target_role == "OWNER":
            raise HTTPException(
                status_code=400, detail="Tidak bisa mengubah akses Owner"
            )

        # Atomic write
        async with conn.transaction():
            await conn.execute(
                "DELETE FROM user_permission_overrides WHERE user_id = $1 AND tenant_id = $2",
                target_user_id,
                tenant_id,
            )

            for module_key, access_level in body.module_overrides.items():
                if not access_level or access_level == "default":
                    continue  # No override = inherit from role

                actions = _ACCESS_ACTIONS.get(access_level, [])
                # module_key IS the DB module name directly (1:1 mapping)
                await conn.execute(
                    """INSERT INTO user_permission_overrides
                       (id, user_id, tenant_id, module, actions, source, created_at, updated_at)
                       VALUES (gen_random_uuid(), $1, $2, $3, $4, 'manual', NOW(), NOW())""",
                    target_user_id,
                    tenant_id,
                    module_key,
                    actions,
                )

        # Invalidate permission cache
        try:
            from ..services.policy_engine_client import get_policy_engine

            engine = get_policy_engine()
            engine.invalidate_user_cache(target_user_id, tenant_id)
        except Exception:
            pass  # Cache miss is fine

        return {"success": True, "message": "Akses berhasil diperbarui"}


# =====================================================
# PERMISSIONS ROUTER (mounted at /api/permissions)
# =====================================================

permissions_router = APIRouter(prefix="/api/permissions", tags=["permissions"])


@permissions_router.get("/me")
async def get_my_permissions(request: Request):
    """Get effective permissions for the current user (role + overrides merged)."""
    user_data = getattr(request.state, "user", {})
    user_id = user_data.get("user_id")
    tenant_id = user_data.get("tenant_id")
    if not user_id or not tenant_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        from ..services.policy_engine_client import get_policy_engine

        engine = get_policy_engine()
        result = await engine.get_effective_permissions(user_id, tenant_id)
        return {"success": True, **result}
    except HTTPException:
        # 409/403 dari PolicyEngine adalah JAWABAN, bukan kecelakaan.
        raise
    except RuntimeError as e:
        # Dipersempit: HANYA "engine belum ter-inisialisasi", bukan semua
        # RuntimeError. Dulu `except RuntimeError` menangkap apa pun lalu
        # membalasnya dengan success:True + izin kosong.
        if "not initialized" not in str(e):
            logger.error(f"Error getting permissions (RuntimeError): {e}")
            raise HTTPException(status_code=500, detail="Failed to get permissions")

        # Fallback memakai resolusi KANONIK yang sama (menghormati status +
        # roles.is_active), dan TIDAK lagi mengembalikan izin kosong sebagai
        # keberhasilan: izin diambil sungguhan dari role_permissions.
        pool = await get_pool()
        async with pool.acquire() as conn:
            role_code = await resolve_business_role(conn, user_id, tenant_id)
            perm_rows = await conn.fetch(
                """
                SELECT rp.module, rp.actions
                FROM role_permissions rp
                JOIN roles r ON r.id = rp.role_id
                WHERE r.code = $1 AND r.is_active = TRUE
            """,
                role_code,
            )
            effective = {
                row["module"]: {
                    "actions": [a.strip() for a in row["actions"]],
                    "source": "role",
                }
                for row in perm_rows
            }
            return {
                "success": True,
                "role_code": role_code,
                "effective_permissions": effective,
            }
    except Exception as e:
        logger.error(f"Error getting permissions: {e}")
        raise HTTPException(status_code=500, detail="Failed to get permissions")
