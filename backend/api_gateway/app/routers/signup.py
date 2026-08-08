"""
Signup Router — New User Registration Flow
=============================================
Anti-enumeration: unified /register endpoint always returns same response.
Flow: register → verify-code/verify-link → complete-setup
"""
import logging
import uuid
import bcrypt
import jwt
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, status, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, EmailStr

from ..services.db_pool import get_db_pool
from fastapi.responses import JSONResponse
from ..services.email_service import (
    email_delivery_configured,
    EmailDeliveryUnavailable,
    send_verification_email,
    send_login_suggestion_email,
    FRONTEND_URL,
)
from ..services.onboarding_service import create_tenant_and_user

logger = logging.getLogger(__name__)
router = APIRouter()

JWT_SECRET = os.getenv("JWT_SECRET", "")
MAX_VERIFICATION_ATTEMPTS = 5
CODE_EXPIRY_MINUTES = 15
SETUP_TOKEN_EXPIRY_MINUTES = 30


# =====================================================
# REQUEST/RESPONSE MODELS
# =====================================================

class SignupRegisterRequest(BaseModel):
    email: str

class VerifyCodeRequest(BaseModel):
    email: str
    code: str

class ResendCodeRequest(BaseModel):
    email: str

class CompleteSetupRequest(BaseModel):
    password: str
    business_name: str
    browser_id: Optional[str] = None
    device_fingerprint: Optional[str] = None


# =====================================================
# HELPERS
# =====================================================

def generate_verification_code() -> str:
    """Generate 6-digit verification code."""
    import secrets
    return str(secrets.randbelow(900000) + 100000)


def hash_code(code: str) -> str:
    """Hash verification code with bcrypt."""
    return bcrypt.hashpw(code.encode('utf-8'), bcrypt.gensalt(rounds=10)).decode('utf-8')


def verify_code(code: str, hashed: str) -> bool:
    """Verify code against bcrypt hash."""
    return bcrypt.checkpw(code.encode('utf-8'), hashed.encode('utf-8'))


def create_setup_token(email: str, registration_id: str) -> str:
    """Create short-lived JWT for setup step."""
    now = datetime.now(timezone.utc)
    payload = {
        "email": email,
        "registration_id": registration_id,
        "purpose": "signup_setup",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=SETUP_TOKEN_EXPIRY_MINUTES)).timestamp()),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def decode_setup_token(token: str) -> dict:
    """Decode and validate setup token."""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        if payload.get("purpose") != "signup_setup":
            raise ValueError("Invalid token purpose")
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token sudah expired. Silakan daftar ulang.")
    except (jwt.InvalidTokenError, ValueError) as e:
        raise HTTPException(status_code=401, detail="Token tidak valid.")


# =====================================================
# ENDPOINTS
# =====================================================

# Kanal kontak nyata. SATU tempat, sengaja dibuat konstanta supaya penggantinya
# tak perlu mencari-cari string di dalam pesan.
#
# JANGAN diganti dengan alur "minta undangan" berbentuk formulir: alur itu harus
# memberi tahu pemilik lewat email — yaitu hal yang justru sedang rusak.
# Hasilnya cuma formulir yang menulis ke basis data dan tak ada yang membacanya.
# Kanal yang dipakai harus kanal yang SUDAH dibaca manusia hari ini.
KONTAK_UNDANGAN = os.getenv("SIGNUP_CONTACT_CHANNEL", "").strip()

MSG_EMAIL_DOWN = (
    "Pendaftaran mandiri sedang tidak tersedia karena layanan email kami belum "
    "aktif. Hubungi kami untuk mendapatkan undangan langsung"
    + (f": {KONTAK_UNDANGAN}." if KONTAK_UNDANGAN else ".")
)


def _email_precondition():
    """Prasyarat pengiriman email — diperiksa SEBELUM menyentuh basis data.

    KENAPA DI DEPAN, BUKAN SAAT MENGIRIM
    ------------------------------------
    `send_verification_email` dipanggil SESUDAH baris pending_registrations
    ditulis DAN di luar blok koneksi — jadi kegagalan kirim meninggalkan
    BARIS YATIM setiap kali pendaftaran dicoba. Memeriksa di depan membuat
    kegagalan tak menyentuh basis data sama sekali; itu lebih kuat daripada
    rollback, karena tak ada yang perlu dibatalkan.

    Bentuk responsnya SENGAJA {success, message}, bukan {detail} bawaan
    HTTPException: frontend yang TAYANG membaca `data.message` dan jatuh ke
    "Terjadi kesalahan." bila hanya ada `detail`. Dengan bentuk ini pesan
    jujurnya tampil apa adanya TANPA perlu deploy frontend.

    Anti-enumerasi tetap utuh: pemeriksaan ini berjalan sebelum pencarian
    email, jadi jawabannya identik untuk email yang ada maupun tidak.
    """
    if not email_delivery_configured():
        logger.error(
            "PENDAFTARAN DITOLAK: RESEND_API_KEY tidak terpasang. "
            "Tak seorang pun dapat mendaftar mandiri sampai ini dipasang."
        )
        return JSONResponse(
            status_code=503,
            content={"success": False, "message": MSG_EMAIL_DOWN},
        )
    return None


@router.post("/register")
async def signup_register(request: SignupRegisterRequest, http_request: Request):
    """
    Unified register endpoint — anti-enumeration.
    Always returns same response regardless of email existence.
    """
    blocked = _email_precondition()
    if blocked:
        return blocked

    email = request.email.lower().strip()

    try:
        pool = await get_db_pool()

        async with pool.acquire() as conn:
            # Check if email already exists in users table
            user_exists = await conn.fetchval(
                'SELECT EXISTS(SELECT 1 FROM "User" WHERE email = $1)',
                email
            )

            if user_exists:
                # Send login suggestion email (background, don't block)
                try:
                    await send_login_suggestion_email(email)
                except Exception as e:
                    logger.warning(f"Failed to send login suggestion: {e}")

                # Return SAME response as new user
                return {
                    "success": True,
                    "message": "Cek email Anda"
                }

            # Check for existing pending registration
            existing = await conn.fetchrow(
                """
                SELECT id, expires_at, status FROM pending_registrations
                WHERE email = $1 AND status = 'pending'
                ORDER BY created_at DESC LIMIT 1
                """,
                email
            )

            code = generate_verification_code()
            code_hash = hash_code(code)
            magic_token = str(uuid.uuid4())
            expires_at = datetime.now(timezone.utc) + timedelta(minutes=CODE_EXPIRY_MINUTES)

            if existing and existing['expires_at'] > datetime.now(timezone.utc):
                # Update existing pending registration
                await conn.execute(
                    """
                    UPDATE pending_registrations
                    SET verification_code = $1, magic_token = $2,
                        expires_at = $3, attempt_count = 0,
                        updated_at = now()
                    WHERE id = $4
                    """,
                    code_hash, magic_token, expires_at, existing['id']
                )
            else:
                # Create new pending registration
                await conn.execute(
                    """
                    INSERT INTO pending_registrations (
                        email, verification_code, magic_token,
                        expires_at
                    ) VALUES ($1, $2, $3, $4)
                    """,
                    email, code_hash, magic_token, expires_at
                )

        # Send verification email
        magic_link = f"{FRONTEND_URL}/api/auth/signup/verify-link/{magic_token}"
        try:
            await send_verification_email(email, code, magic_link)
        except EmailDeliveryUnavailable as e:
            # Baris pending_registrations SUDAH ditulis di atas. Kalau email tak
            # jadi terkirim, baris itu YATIM: pengguna tak punya kode, tapi
            # indeks menganggap ada pendaftaran tertunda untuk emailnya.
            # Karena itu dihapus di sini — hasil akhirnya sama dengan rollback:
            # gagal kirim = tak ada jejak.
            logger.error(f"PENDAFTARAN DIBATALKAN, email gagal terkirim: {e}")
            try:
                async with pool.acquire() as c2:
                    await c2.execute(
                        "DELETE FROM pending_registrations WHERE email = $1 AND status = 'pending'",
                        email,
                    )
            except Exception as cleanup_err:
                logger.error(f"Gagal membersihkan pendaftaran tertunda: {cleanup_err}")
            return JSONResponse(
                status_code=503,
                content={"success": False, "message": MSG_EMAIL_DOWN},
            )

        return {
            "success": True,
            "message": "Cek email Anda"
        }

    except Exception as e:
        # CATATAN: blok ini mengembalikan success:True demi anti-enumerasi —
        # jadi ia MENELAN galat apa pun menjadi "Cek email Anda". Itu sebabnya
        # kegagalan email ditangani di atas, SEBELUM sampai ke sini, dan
        # prasyarat kunci diperiksa SEBELUM `try` dimulai.
        logger.error(f"Signup register error: {e}")
        # Still return same response to prevent information leakage
        return {
            "success": True,
            "message": "Cek email Anda"
        }


@router.post("/verify-code")
async def signup_verify_code(request: VerifyCodeRequest):
    """
    Verify 6-digit code from email.
    Returns setup_token JWT on success.
    """
    email = request.email.lower().strip()
    code = request.code.strip()

    pool = await get_db_pool()

    async with pool.acquire() as conn:
        # Get pending registration
        reg = await conn.fetchrow(
            """
            SELECT id, verification_code, expires_at, attempt_count, status
            FROM pending_registrations
            WHERE email = $1 AND status = 'pending'
            ORDER BY created_at DESC LIMIT 1
            """,
            email
        )

        if not reg:
            raise HTTPException(
                status_code=400,
                detail="Tidak ada pendaftaran yang tertunda untuk email ini."
            )

        # Check expiry
        if reg['expires_at'] < datetime.now(timezone.utc):
            raise HTTPException(
                status_code=400,
                detail="Kode sudah expired. Silakan minta kode baru."
            )

        # Check attempt count
        if reg['attempt_count'] >= MAX_VERIFICATION_ATTEMPTS:
            raise HTTPException(
                status_code=429,
                detail="Terlalu banyak percobaan. Silakan minta kode baru."
            )

        # Verify code
        if not verify_code(code, reg['verification_code']):
            # Increment attempt count
            await conn.execute(
                """
                UPDATE pending_registrations
                SET attempt_count = attempt_count + 1, updated_at = now()
                WHERE id = $1
                """,
                reg['id']
            )
            remaining = MAX_VERIFICATION_ATTEMPTS - reg['attempt_count'] - 1
            raise HTTPException(
                status_code=400,
                detail=f"Kode salah. {remaining} percobaan tersisa."
            )

        # Success! Update status to verified
        await conn.execute(
            """
            UPDATE pending_registrations
            SET status = 'verified', verified_at = now(),
                attempt_count = 0, updated_at = now()
            WHERE id = $1
            """,
            reg['id']
        )

    # Generate setup token
    setup_token = create_setup_token(email, str(reg['id']))

    return {
        "success": True,
        "setup_token": setup_token,
        "message": "Email terverifikasi. Silakan lengkapi pendaftaran."
    }


@router.get("/verify-link/{token}")
async def signup_verify_link(token: str):
    """
    Verify magic link from email.
    Redirects to frontend signup page with setup_token.
    """
    pool = await get_db_pool()

    async with pool.acquire() as conn:
        reg = await conn.fetchrow(
            """
            SELECT id, email, expires_at, status
            FROM pending_registrations
            WHERE magic_token = $1
            """,
            token
        )

        if not reg:
            return RedirectResponse(
                url=f"{FRONTEND_URL}/signup?error=invalid",
                status_code=302
            )

        if reg['status'] != 'pending':
            # Already verified or completed — redirect to signup with token anyway
            if reg['status'] == 'verified':
                setup_token = create_setup_token(reg['email'], str(reg['id']))
                return RedirectResponse(
                    url=f"{FRONTEND_URL}/signup?token={setup_token}",
                    status_code=302
                )
            return RedirectResponse(
                url=f"{FRONTEND_URL}/signup?error=already_used",
                status_code=302
            )

        if reg['expires_at'] < datetime.now(timezone.utc):
            return RedirectResponse(
                url=f"{FRONTEND_URL}/signup?error=expired&email={reg['email']}",
                status_code=302
            )

        # Verify!
        await conn.execute(
            """
            UPDATE pending_registrations
            SET status = 'verified', verified_at = now(), updated_at = now()
            WHERE id = $1
            """,
            reg['id']
        )

    setup_token = create_setup_token(reg['email'], str(reg['id']))

    return RedirectResponse(
        url=f"{FRONTEND_URL}/signup?token={setup_token}",
        status_code=302
    )


@router.post("/complete-setup")
async def signup_complete_setup(
    request: CompleteSetupRequest,
    http_request: Request,
):
    """
    Complete signup: create tenant + user + CoA.
    Requires setup_token in Authorization header.
    """
    # Extract setup token
    auth_header = http_request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Setup token diperlukan.")

    setup_token = auth_header.replace("Bearer ", "")
    token_data = decode_setup_token(setup_token)

    email = token_data["email"]
    registration_id = token_data["registration_id"]

    # Validate password
    if len(request.password) < 8:
        raise HTTPException(
            status_code=400,
            detail="Password minimal 8 karakter."
        )

    # Validate business name
    if not request.business_name or len(request.business_name.strip()) < 2:
        raise HTTPException(
            status_code=400,
            detail="Nama bisnis minimal 2 karakter."
        )

    # Verify registration is in 'verified' status
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        reg = await conn.fetchrow(
            """
            SELECT id, status FROM pending_registrations
            WHERE id = $1::uuid AND email = $2
            """,
            registration_id, email
        )

        if not reg or reg['status'] not in ('verified',):
            raise HTTPException(
                status_code=400,
                detail="Sesi pendaftaran tidak valid. Silakan mulai dari awal."
            )

        # Check if email already taken (race condition guard)
        user_exists = await conn.fetchval(
            'SELECT EXISTS(SELECT 1 FROM "User" WHERE email = $1)',
            email
        )
        if user_exists:
            raise HTTPException(
                status_code=409,
                detail="Email sudah terdaftar. Silakan login."
            )

    try:
        # Create tenant + user + CoA atomically
        result = await create_tenant_and_user(
            email=email,
            password=request.password,
            business_name=request.business_name.strip(),
            browser_id=request.browser_id,
            device_fingerprint=request.device_fingerprint,
        )

        # Mark registration as completed
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE pending_registrations
                SET status = 'completed', updated_at = now()
                WHERE id = $1::uuid
                """,
                registration_id
            )

        return {
            "success": True,
            "message": "Pendaftaran berhasil!",
            "data": {
                "user_id": result["user_id"],
                "email": result["email"],
                "name": result["name"],
                "role": result["role"],
                "tenant_id": result["tenant_id"],
                "access_token": result["access_token"],
                "refresh_token": result["refresh_token"],
                "device_id": result["device_id"],
                "device_type": result["device_type"],
            }
        }

    except Exception as e:
        logger.error(f"Complete setup error: {e}")
        raise HTTPException(
            status_code=500,
            detail="Gagal menyelesaikan pendaftaran. Silakan coba lagi."
        )


@router.post("/resend-code")
async def signup_resend_code(request: ResendCodeRequest):
    """
    Resend verification code.
    Anti-enumeration: always returns same response.
    """
    blocked = _email_precondition()
    if blocked:
        return blocked

    email = request.email.lower().strip()

    try:
        pool = await get_db_pool()

        async with pool.acquire() as conn:
            # Get pending registration
            reg = await conn.fetchrow(
                """
                SELECT id FROM pending_registrations
                WHERE email = $1 AND status IN ('pending', 'verified')
                ORDER BY created_at DESC LIMIT 1
                """,
                email
            )

            if reg:
                code = generate_verification_code()
                code_hash = hash_code(code)
                magic_token = str(uuid.uuid4())
                expires_at = datetime.now(timezone.utc) + timedelta(minutes=CODE_EXPIRY_MINUTES)

                await conn.execute(
                    """
                    UPDATE pending_registrations
                    SET verification_code = $1, magic_token = $2,
                        expires_at = $3, attempt_count = 0,
                        status = 'pending', updated_at = now()
                    WHERE id = $4
                    """,
                    code_hash, magic_token, expires_at, reg['id']
                )

                magic_link = f"{FRONTEND_URL}/api/auth/signup/verify-link/{magic_token}"
                await send_verification_email(email, code, magic_link)

    except Exception as e:
        logger.error(f"Resend code error: {e}")

    # Always return same response
    return {
        "success": True,
        "message": "Cek email Anda"
    }
