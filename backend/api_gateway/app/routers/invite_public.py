"""
Public Invite Router — Token-based team invitation accept/decline.
No JWT required — authentication is via invite_token.

Law 32: Uses shared pool from services.db_pool (NOT per-router pool).
"""
import logging
import uuid as uuid_module
import secrets
from datetime import datetime, timedelta, timezone

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


# --- Kode verifikasi email untuk AKUN BARU dari undangan (V311, 26 Sep 2026) ---
# Token undangan membuktikan UNDANGAN (pengundang pun memegangnya lewat
# invite_link), bukan kepemilikan email. Membuat akun untuk email itu kini
# menuntut kode 6 digit yang dikirim KE email undangan.
KODE_BERLAKU = timedelta(minutes=15)
KODE_JEDA = timedelta(seconds=60)
KODE_JENDELA = timedelta(hours=1)
KODE_MAKS_PER_JENDELA = 5
KODE_MAKS_SALAH = 5


def _galat_kode(kode: str, pesan: str, status_code: int = 400):
    return HTTPException(status_code=status_code, detail={"code": kode, "message": pesan})


async def _periksa_kode_undangan(pool, token: str, kode: str) -> None:
    """Transaksi TERSENDIRI: kode salah menaikkan hitungan dan TETAP ter-commit
    (di dalam transaksi utama, raise akan membatalkan kenaikan itu)."""
    salah = False
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """SELECT id, email, status, expires_at, verify_code_hash,
                          verify_code_expires_at, verify_attempts
                   FROM team_invitations WHERE invite_token = $1 FOR UPDATE""",
                token,
            )
            if not row or row["status"] != "pending":
                return  # transaksi utama memberi jawaban yang semestinya
            ada_akun = await conn.fetchval(
                'SELECT 1 FROM "User" WHERE lower(email) = lower($1)', row["email"]
            )
            if ada_akun:
                return  # transaksi utama menjawab 409 ACCOUNT_EXISTS
            now = datetime.now(timezone.utc)
            if not row["verify_code_hash"]:
                raise _galat_kode("CODE_REQUIRED", "Minta kode verifikasi ke email undangan terlebih dahulu.")
            if (row["verify_attempts"] or 0) >= KODE_MAKS_SALAH:
                raise _galat_kode("CODE_LOCKED", "Terlalu banyak kode salah. Minta kode baru.")
            if row["verify_code_expires_at"] is None or row["verify_code_expires_at"] < now:
                raise _galat_kode("CODE_EXPIRED", "Kode sudah kedaluwarsa. Minta kode baru.")
            if not kode or not bcrypt.checkpw(kode.encode("utf-8"), row["verify_code_hash"].encode("utf-8")):
                await conn.execute(
                    "UPDATE team_invitations SET verify_attempts = verify_attempts + 1 WHERE id = $1",
                    row["id"],
                )
                salah = True
    if salah:
        raise _galat_kode("CODE_INVALID", "Kode verifikasi salah.")


def _safe_set_tenant(tenant_id: str) -> str:
    """Build SET LOCAL statement with sanitized tenant_id."""
    safe = tenant_id.replace("'", "''")
    return f"SET LOCAL app.tenant_id = '{safe}'"


async def _generate_jwt_tokens(
    user_id: str, tenant_id: str, email: str, role: str
) -> dict:
    """Generate JWT tokens using the auth_client singleton.

    NOTE: JWT 'role' field must be Prisma plan-tier enum (FREE/USER/OWNER/ADMIN),
    NOT team role code (BENDAHARA etc). (RBACMiddleware yang dulu membacanya dilepas
    25 Sep 2026, #50; policy_engine_client masih memakainya utk visibility cadangan.)
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
                # Best-effort audit, DELIBERATELY decoupled from the expiry above: the
                # UPDATE is authoritative and must persist even if this INSERT fails, so
                # it is intentionally NOT wrapped in a transaction with it (hence the
                # try/except: pass). Do NOT "fix" this into one conn.transaction() -- e.g.
                # during a missing-transaction sweep -- that would let a failed audit roll
                # back a real expiry, which is the opposite of what we want.
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


@router.post("/{token}/request-code")
async def request_invite_code(token: str):
    """Kirim kode 6 digit ke email UNDANGAN untuk membuat akun baru (V311).

    Kode TIDAK PERNAH dikembalikan di respons. Batas: 1 kali / 60 detik dan
    maksimal 5 kali / jam per token (token tak bisa dipakai menyepam email).
    """
    from ..services.email_service import EmailDeliveryUnavailable, send_invite_code_email

    try:
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """SELECT id, email, status, expires_at, verify_sent_at,
                              verify_sent_window_at, verify_sent_count
                       FROM team_invitations WHERE invite_token = $1 FOR UPDATE""",
                    token,
                )
                if not row:
                    raise HTTPException(status_code=400, detail="Token tidak valid")
                now = datetime.now(timezone.utc)
                if row["status"] != "pending":
                    raise HTTPException(status_code=400, detail="Undangan sudah tidak berlaku")
                if row["expires_at"] < now:
                    raise HTTPException(status_code=400, detail="Undangan sudah kedaluwarsa")
                ada_akun = await conn.fetchval(
                    'SELECT 1 FROM "User" WHERE lower(email) = lower($1)', row["email"]
                )
                if ada_akun:
                    raise _galat_kode(
                        "ACCOUNT_EXISTS",
                        "Email ini sudah punya akun MilkyHoop. Masuk dengan email dan sandi akun Anda untuk menerima undangan.",
                        409,
                    )
                if row["verify_sent_at"] and now - row["verify_sent_at"] < KODE_JEDA:
                    raise _galat_kode("CODE_TOO_SOON", "Tunggu sebentar sebelum meminta kode lagi.", 429)
                jendela = row["verify_sent_window_at"]
                hitung = row["verify_sent_count"] or 0
                if jendela is None or now - jendela >= KODE_JENDELA:
                    jendela, hitung = now, 0
                if hitung >= KODE_MAKS_PER_JENDELA:
                    raise _galat_kode("CODE_TOO_MANY", "Terlalu banyak permintaan kode. Coba lagi nanti.", 429)
                kode = f"{secrets.randbelow(10**6):06d}"
                await conn.execute(
                    """UPDATE team_invitations
                       SET verify_code_hash = $2, verify_code_expires_at = $3, verify_attempts = 0,
                           verify_sent_at = $4, verify_sent_window_at = $5, verify_sent_count = $6
                       WHERE id = $1""",
                    row["id"],
                    bcrypt.hashpw(kode.encode("utf-8"), bcrypt.gensalt(rounds=10)).decode("utf-8"),
                    now + KODE_BERLAKU,
                    now,
                    jendela,
                    hitung + 1,
                )
                # Kirim DI DALAM transaksi: gagal kirim -> batal, jatah tak terpakai.
                await send_invite_code_email(row["email"], kode)
        return {"success": True, "sent": True}
    except HTTPException:
        raise
    except EmailDeliveryUnavailable:
        raise HTTPException(status_code=503, detail="Email tidak dapat dikirim saat ini")
    except Exception as e:
        logger.error(f"Error request invite code: {type(e).__name__}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/{token}/accept")
async def accept_invite(token: str, request: Request):
    """Accept invitation. Mode A: email+password (existing). Mode B: name+password+password_confirm (new)."""
    try:
        data = await request.json()
        pool = await get_db_pool()
        # V311: AKUN BARU (mode B) wajib kode yang dikirim ke email undangan.
        # Dicek di transaksi TERSENDIRI supaya hitungan kode salah ter-commit.
        if not ("email" in data and "password" in data and "password_confirm" not in data):
            await _periksa_kode_undangan(pool, token, str(data.get("code") or "").strip())
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
                        # PENGAMBILALIHAN AKUN DITUTUP (26 Sep 2026, audit WRITE_EXEMPT).
                        # Dulu: mode B atas email yang SUDAH punya akun MENIMPA
                        # passwordHash-nya. Pengundang (siapa pun yang mendaftar
                        # sendiri = OWNER tenantnya) menerima invite_link di respons
                        # POST /api/team-members/invite -> bisa mengundang email
                        # korban, menerima dengan sandi pilihannya, lalu masuk
                        # sebagai korban ke SEMUA tenant korban. Token undangan
                        # membuktikan UNDANGAN, bukan kepemilikan email.
                        # Akun lama WAJIB mode A (email + sandi lamanya).
                        raise HTTPException(
                            status_code=409,
                            detail={
                                "code": "ACCOUNT_EXISTS",
                                "message": "Email ini sudah punya akun MilkyHoop. "
                                "Masuk dengan email dan sandi akun Anda untuk menerima undangan.",
                            },
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
                    "UPDATE team_invitations SET status = 'accepted', accepted_at = NOW(), verify_code_hash = NULL WHERE id = $1",
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
