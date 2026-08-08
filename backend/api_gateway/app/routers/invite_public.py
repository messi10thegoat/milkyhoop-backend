"""
Public Invite Router — Token-based team invitation accept/decline.
No JWT required — authentication is via invite_token.

Law 32: Uses shared pool from services.db_pool (NOT per-router pool).
"""
import logging
import uuid as uuid_module
from datetime import datetime, timezone

import bcrypt
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..services.db_pool import get_db_pool

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/invite", tags=["invite-public"])


# --- Schemas ---


class AcceptExistingUser(BaseModel):
    """Mode A: existing user provides credentials."""

    email: str
    password: str


class AcceptNewUser(BaseModel):
    """Mode B: new user sets up account."""

    name: str
    password: str
    password_confirm: str


# --- Helpers ---


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode(
        "utf-8"
    )


def _verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))


def _safe_set_tenant(tenant_id: str) -> str:
    """Build SET LOCAL statement with sanitized tenant_id."""
    safe = tenant_id.replace("'", "''")
    return f"SET LOCAL app.tenant_id = '{safe}'"


async def _generate_jwt_tokens(
    user_id: str, tenant_id: str, email: str, role: str
) -> dict:
    """Generate JWT tokens using the auth_client singleton.

    NOTE: JWT 'role' field must be Prisma plan-tier enum (FREE/USER/OWNER/ADMIN),
    NOT team role code (BENDAHARA etc). RBACMiddleware checks this field.
    Team RBAC is handled by PermissionMiddleware via user_tenant_roles.
    """
    from backend.api_gateway.app.services.auth_instance import auth_client

    device_id = str(uuid_module.uuid4())
    result = await auth_client._generate_tokens_locally(
        user_id=user_id,
        tenant_id=tenant_id,
        email=email,
        role="ADMIN",  # Prisma plan-tier, NOT team role. All users get ADMIN tier.
        device_id=device_id,
        device_type="web",
    )
    return {
        "access_token": result.access_token,
        "refresh_token": result.refresh_token,
        "device_id": device_id,
    }


# --- Endpoints ---


_GONE_MESSAGES = {
    "expired": "Undangan ini sudah kedaluwarsa. Minta pemilik bisnis mengirim undangan baru.",
    "revoked": "Undangan ini sudah dibatalkan. Minta pemilik bisnis mengirim undangan baru.",
    "accepted": "Undangan ini sudah diterima. Silakan masuk dengan akun Anda.",
    "declined": "Undangan ini sudah ditolak. Minta pemilik bisnis mengirim undangan baru.",
}


def _gone(status: str) -> dict:
    return {
        "error_code": f"INVITE_{status.upper()}",
        "message": _GONE_MESSAGES.get(status, _GONE_MESSAGES["expired"]),
    }


@router.get("/{token}")
async def validate_invite(token: str):
    """Validate invite token and return invitation info. Public — no JWT."""
    try:
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """SELECT ti.id, ti.email, ti.name, ti.status, ti.expires_at, ti.tenant_id,
                       r.name as role_name, r.description as role_description, r.code as role_code,
                       ti.invited_by
                FROM team_invitations ti JOIN roles r ON r.id = ti.role_id
                WHERE ti.invite_token = $1""",
                token,
            )

            if not row:
                # 404: undangan seperti ini TIDAK ADA. Kemungkinan besar link
                # salah salin/terpotong. Dibedakan dari 410 supaya penerima tahu
                # harus meminta LINK yang benar, bukan undangan baru.
                raise HTTPException(
                    status_code=404,
                    detail={
                        "error_code": "INVITE_NOT_FOUND",
                        "message": (
                            "Undangan tidak ditemukan. Link ini mungkin sudah "
                            "diperbarui — mintalah link terbaru dari pemilik bisnis, "
                            "atau pastikan link yang Anda buka lengkap."
                        ),
                    },
                )

            status = row["status"]
            now = datetime.now(timezone.utc)

            # Lazy expiration
            if status == "pending" and row["expires_at"] < now:
                await conn.execute(
                    "UPDATE team_invitations SET status = 'expired' WHERE id = $1 AND status = 'pending'",
                    row["id"],
                )
                try:
                    await conn.execute(
                        """INSERT INTO audit_logs (id, "userId", "eventType", entity_type, entity_id, tenant_id, metadata, success, "createdAt")
                        VALUES ($1, $2, 'TEAM_INVITE_EXPIRED', 'team_invitation', $3, $4, $5, true, NOW())""",
                        str(uuid_module.uuid4()),
                        row["invited_by"],
                        str(row["id"]),
                        row["tenant_id"],
                        f'{{"email": "{row["email"]}", "role_code": "{row["role_code"]}"}}',
                    )
                except Exception:
                    pass
                raise HTTPException(status_code=410, detail=_gone("expired"))

            # 410 = undangannya NYATA tapi sudah tak berlaku. Pesannya DIBEDAKAN
            # per sebab: yang kedaluwarsa/dicabut perlu undangan BARU, yang sudah
            # diterima cukup login. Satu kode dengan satu pesan generik akan
            # membuat ketiganya terasa seperti kesalahan yang sama.
            if status in ("accepted", "declined", "revoked", "expired"):
                raise HTTPException(status_code=410, detail=_gone(status))

            await conn.execute(_safe_set_tenant(row["tenant_id"]))

            inviter_name = await conn.fetchval(
                'SELECT COALESCE(name, fullname, email) FROM "User" WHERE id = $1',
                row["invited_by"],
            )
            tenant_row = await conn.fetchrow(
                'SELECT COALESCE(display_name, alias, id) as name FROM "Tenant" WHERE id = $1',
                row["tenant_id"],
            )
            user_exists = await conn.fetchval(
                'SELECT EXISTS(SELECT 1 FROM "User" WHERE email = $1 AND "passwordHash" IS NOT NULL)',
                row["email"],
            )

            return {
                "valid": True,
                "invitation": {
                    "tenant_name": tenant_row["name"]
                    if tenant_row
                    else row["tenant_id"],
                    "tenant_logo": None,
                    "role_name": row["role_name"],
                    "role_description": row["role_description"] or "",
                    "inviter_name": inviter_name or "Admin",
                    "email": row["email"],
                    "name": row["name"],
                    "expires_at": row["expires_at"].isoformat(),
                },
                "user_exists": bool(user_exists),
            }

    except HTTPException:
        # 404/410 di atas adalah JAWABAN, bukan kecelakaan. Tanpa baris ini
        # `except Exception` di bawah menelannya dan mengubah jawaban yang
        # BENAR menjadi 500 — persis yang terjadi di jalan pertama gate:
        # token yang dicabut membalas 500, bukan 410. Kelas cacat yang sama
        # sudah ditemukan di get_user_context; ini kemunculan keduanya.
        raise
    except Exception as e:
        logger.error(f"Error validating invite token: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/{token}/accept")
async def accept_invite(token: str, request: Request):
    """Accept invitation. Mode A: email+password (existing). Mode B: name+password+password_confirm (new)."""
    try:
        data = await request.json()
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """SELECT ti.id, ti.email, ti.name, ti.status, ti.expires_at, ti.tenant_id,
                           ti.role_id, ti.module_overrides, ti.invited_by,
                           r.code as role_code, r.name as role_name
                    FROM team_invitations ti JOIN roles r ON r.id = ti.role_id
                    WHERE ti.invite_token = $1 FOR UPDATE""",
                    token,
                )

                if not row:
                    raise HTTPException(status_code=400, detail="Token tidak valid")

                now = datetime.now(timezone.utc)
                if row["status"] != "pending":
                    raise HTTPException(
                        status_code=400, detail="Undangan sudah tidak berlaku"
                    )
                if row["expires_at"] < now:
                    await conn.execute(
                        "UPDATE team_invitations SET status = 'expired' WHERE id = $1",
                        row["id"],
                    )
                    raise HTTPException(
                        status_code=400, detail="Undangan sudah kedaluwarsa"
                    )

                tenant_id = row["tenant_id"]
                await conn.execute(_safe_set_tenant(tenant_id))

                is_existing_user = (
                    "email" in data
                    and "password" in data
                    and "password_confirm" not in data
                )

                if is_existing_user:
                    if data["email"].lower() != row["email"].lower():
                        raise HTTPException(
                            status_code=400, detail="Email tidak sesuai dengan undangan"
                        )

                    user_row = await conn.fetchrow(
                        'SELECT id, "passwordHash" FROM "User" WHERE email = $1',
                        row["email"],
                    )
                    if not user_row or not user_row["passwordHash"]:
                        raise HTTPException(
                            status_code=401,
                            detail="Akun tidak ditemukan. Silakan buat akun baru.",
                        )
                    if not _verify_password(data["password"], user_row["passwordHash"]):
                        raise HTTPException(status_code=401, detail="Password salah")

                    user_id = user_row["id"]

                else:
                    password = data.get("password", "")
                    password_confirm = data.get("password_confirm", "")
                    name = data.get("name", row["name"] or row["email"].split("@")[0])

                    if len(password) < 8:
                        raise HTTPException(
                            status_code=400, detail="Password minimal 8 karakter"
                        )
                    if password != password_confirm:
                        raise HTTPException(
                            status_code=400, detail="Password tidak cocok"
                        )

                    existing_user = await conn.fetchrow(
                        'SELECT id FROM "User" WHERE email = $1', row["email"]
                    )

                    if existing_user:
                        user_id = existing_user["id"]
                        await conn.execute(
                            """UPDATE "User" SET "passwordHash" = $1, name = COALESCE(NULLIF(name, ''), $2),
                            "isVerified" = true, "updatedAt" = NOW() WHERE id = $3""",
                            _hash_password(password),
                            name,
                            user_id,
                        )
                    else:
                        # User.role = 'ADMIN'::"Role" is Prisma plan-tier enum, NOT team role
                        user_id = str(uuid_module.uuid4())
                        await conn.execute(
                            """INSERT INTO "User" (id, email, name, "passwordHash", "isVerified", role, "tenantId", "createdAt", "updatedAt")
                            VALUES ($1, $2, $3, $4, true, 'ADMIN'::"Role", $5, NOW(), NOW())""",
                            user_id,
                            row["email"],
                            name,
                            _hash_password(password),
                            tenant_id,
                        )

                existing_membership = await conn.fetchval(
                    "SELECT id FROM user_tenant_roles WHERE user_id = $1::uuid AND tenant_id = $2",
                    user_id,
                    tenant_id,
                )
                if existing_membership:
                    raise HTTPException(
                        status_code=409, detail="Anda sudah menjadi anggota tim ini"
                    )

                is_external = row["role_code"] == "COLLABORATOR"
                await conn.execute(
                    """INSERT INTO user_tenant_roles (user_id, tenant_id, role_id, assigned_by, is_external, status)
                    VALUES ($1::uuid, $2, $3, $4::uuid, $5, 'ACTIVE')""",
                    user_id,
                    tenant_id,
                    row["role_id"],
                    row["invited_by"],
                    is_external,
                )

                if row["module_overrides"]:
                    import json

                    overrides = (
                        json.loads(row["module_overrides"])
                        if isinstance(row["module_overrides"], str)
                        else row["module_overrides"]
                    )
                    MODULE_MAP = {
                        "penjualan": "SALES",
                        "pembelian": "PURCHASE",
                        "kasbank": "BANKING",
                        "persediaan": "INVENTORY",
                        "akuntansi": "ACCOUNTING",
                        "laporan": "REPORTS",
                        "penggajian": "PAYROLL",
                        "pengaturan": "SETTINGS",
                    }
                    LEVEL_ACTIONS = {
                        "full": ["C", "R", "U", "D", "V", "A", "P", "E"],
                        "view": ["R"],
                        "none": [],
                    }
                    for key, level in overrides.items():
                        module = MODULE_MAP.get(key)
                        if not module:
                            continue
                        actions = LEVEL_ACTIONS.get(level, [])
                        await conn.execute(
                            """INSERT INTO user_permission_overrides (user_id, tenant_id, module, actions, source)
                            VALUES ($1, $2, $3, $4::char[], 'invite')
                            ON CONFLICT (user_id, tenant_id, module) DO UPDATE SET actions = $4::char[], updated_at = NOW()""",
                            user_id,
                            tenant_id,
                            module,
                            actions,
                        )

                await conn.execute(
                    "UPDATE team_invitations SET status = 'accepted', accepted_at = NOW() WHERE id = $1",
                    row["id"],
                )

                try:
                    mode = "login" if is_existing_user else "signup"
                    await conn.execute(
                        """INSERT INTO audit_logs (id, "userId", "eventType", entity_type, entity_id, tenant_id, metadata, success, "createdAt")
                        VALUES ($1, $2, 'TEAM_INVITE_ACCEPTED', 'team_invitation', $3, $4, $5, true, NOW())""",
                        str(uuid_module.uuid4()),
                        user_id,
                        str(row["id"]),
                        tenant_id,
                        f'{{"email": "{row["email"]}", "role_code": "{row["role_code"]}", "mode": "{mode}"}}',
                    )
                except Exception:
                    pass

        tokens = await _generate_jwt_tokens(
            user_id=user_id,
            tenant_id=tenant_id,
            email=row["email"],
            role=row["role_code"],
        )

        return {
            "success": True,
            "message": f"Berhasil bergabung ke tim sebagai {row['role_name']}",
            "data": {
                "access_token": tokens["access_token"],
                "refresh_token": tokens["refresh_token"],
                "device_id": tokens["device_id"],
                "tenant_id": tenant_id,
                "role": row["role_code"],
            },
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error accepting invite: {e}")
        raise HTTPException(status_code=500, detail="Gagal menerima undangan")


@router.post("/{token}/decline")
async def decline_invite(token: str):
    """Decline an invitation."""
    try:
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """SELECT id, email, tenant_id, invited_by, status
                    FROM team_invitations WHERE invite_token = $1 FOR UPDATE""",
                    token,
                )

                if not row:
                    raise HTTPException(status_code=400, detail="Token tidak valid")
                if row["status"] != "pending":
                    raise HTTPException(
                        status_code=400, detail="Undangan sudah tidak berlaku"
                    )

                await conn.execute(
                    "UPDATE team_invitations SET status = 'declined', declined_at = NOW() WHERE id = $1",
                    row["id"],
                )

                try:
                    await conn.execute(
                        """INSERT INTO audit_logs (id, "userId", "eventType", entity_type, entity_id, tenant_id, metadata, success, "createdAt")
                        VALUES ($1, $2, 'TEAM_INVITE_DECLINED', 'team_invitation', $3, $4, $5, true, NOW())""",
                        str(uuid_module.uuid4()),
                        row["invited_by"],
                        str(row["id"]),
                        row["tenant_id"],
                        f'{{"email": "{row["email"]}"}}',
                    )
                except Exception:
                    pass

        return {"success": True, "message": "Undangan ditolak"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error declining invite: {e}")
        raise HTTPException(status_code=500, detail="Gagal menolak undangan")
