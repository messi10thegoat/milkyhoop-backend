"""
MFA (Multi-Factor Authentication) API Endpoints
ISO 27001:2022 - A.8.5 Secure Authentication

Endpoints:
- POST /api/auth/mfa/setup - Generate TOTP secret and QR code
- POST /api/auth/mfa/verify - Verify TOTP code and enable MFA
- POST /api/auth/mfa/validate - Validate TOTP during login
- POST /api/auth/mfa/disable - Disable MFA (requires password)
- GET  /api/auth/mfa/status - Get MFA status for user
- POST /api/auth/mfa/backup-codes - Generate new backup codes
"""

import logging
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

# Import Prisma client
from backend.api_gateway.libs.milkyhoop_prisma import Prisma

# Import MFA utilities (will be copied from auth_service)
import pyotp
import qrcode
import qrcode.image.svg
import base64
import io
import secrets
import hashlib

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth/mfa", tags=["mfa"])

# Prisma client instance
prisma = Prisma()


# ============================================
# Request/Response Models
# ============================================


class MFASetupRequest(BaseModel):
    """Request to setup MFA"""

    pass  # User ID comes from auth token


class MFASetupResponse(BaseModel):
    """Response with QR code and secret for MFA setup"""

    success: bool
    message: str
    qr_code_base64: Optional[str] = None
    manual_entry_key: Optional[str] = None
    backup_codes: Optional[list] = None


class MFAVerifyRequest(BaseModel):
    """Request to verify TOTP code"""

    code: str = Field(..., min_length=6, max_length=6, description="6-digit TOTP code")


class MFAVerifyResponse(BaseModel):
    """Response for TOTP verification"""

    success: bool
    message: str
    mfa_enabled: Optional[bool] = None


class MFAValidateRequest(BaseModel):
    """Request to validate TOTP during login"""

    user_id: str
    code: str = Field(..., min_length=6, max_length=6)


class MFADisableRequest(BaseModel):
    """Request to disable MFA"""

    password: str = Field(
        ..., min_length=1, description="Current password for confirmation"
    )


class MFAStatusResponse(BaseModel):
    """Response with MFA status"""

    mfa_enabled: bool
    mfa_verified: bool
    backup_codes_remaining: int
    enabled_at: Optional[str] = None


class BackupCodesResponse(BaseModel):
    """Response with new backup codes"""

    success: bool
    message: str
    codes: Optional[list] = None
    warning: Optional[str] = None


# ============================================
# Helper Functions
# ============================================


def generate_totp_secret() -> str:
    """Generate a new TOTP secret"""
    return pyotp.random_base32()


def generate_qr_code(email: str, secret: str) -> str:
    """Generate QR code as base64 PNG"""
    totp = pyotp.TOTP(secret)
    uri = totp.provisioning_uri(name=email, issuer_name="MilkyHoop")

    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(uri)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)

    return (
        f"data:image/png;base64,{base64.b64encode(buffer.getvalue()).decode('utf-8')}"
    )


def verify_totp_code(secret: str, code: str) -> bool:
    """Verify a TOTP code with 1 window tolerance"""
    if not secret or not code:
        return False
    code = code.replace(" ", "").replace("-", "")
    if not code.isdigit() or len(code) != 6:
        return False
    totp = pyotp.TOTP(secret)
    return totp.verify(code, valid_window=1)


def generate_backup_codes(count: int = 10) -> tuple:
    """Generate backup codes, return (plain_codes, hashed_codes)"""
    codes = []
    hashes = []
    for _ in range(count):
        code = secrets.token_hex(4).upper()
        formatted = f"{code[:4]}-{code[4:]}"
        codes.append(formatted)
        hashes.append(hashlib.sha256(code.encode()).hexdigest())
    return codes, hashes


def format_secret_for_display(secret: str) -> str:
    """Format secret for manual entry (groups of 4)"""
    return " ".join([secret[i : i + 4] for i in range(0, len(secret), 4)])


async def log_mfa_action(user_id: str, action: str, success: bool, request: Request):
    """Log MFA action for audit trail"""
    try:
        await prisma.mfaauditlog.create(
            data={
                "userId": user_id,
                "action": action,
                "success": success,
                "ipAddress": request.client.host if request.client else None,
                "userAgent": request.headers.get("user-agent", "")[:500],
            }
        )
    except Exception as e:
        logger.error(f"Failed to log MFA action: {e}")


# ============================================
# API Endpoints
# ============================================


@router.post("/setup", response_model=MFASetupResponse)
async def setup_mfa(request: Request):
    """
    Setup MFA for authenticated user

    Returns QR code and backup codes. User must verify with TOTP code
    before MFA is actually enabled.
    """
    # Get user from request state (set by auth middleware)
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    user_id = user.get("user_id")
    email = user.get("email", "user@milkyhoop.com")

    try:
        # Check if MFA already enabled
        security = await prisma.usersecurity.find_unique(where={"userId": user_id})

        if security and security.twoFactorEnabled:
            return MFASetupResponse(
                success=False,
                message="MFA is already enabled. Disable it first to reconfigure.",
            )

        # Generate new TOTP secret
        secret = generate_totp_secret()

        # Generate QR code
        qr_base64 = generate_qr_code(email, secret)

        # Generate backup codes
        plain_codes, hashed_codes = generate_backup_codes()

        # Store secret (not yet verified/enabled)
        if security:
            await prisma.usersecurity.update(
                where={"userId": user_id},
                data={
                    "totpSecret": secret,
                    "totpVerified": False,
                    "mfaBackupCodes": hashed_codes,
                },
            )
        else:
            await prisma.usersecurity.create(
                data={
                    "userId": user_id,
                    "totpSecret": secret,
                    "totpVerified": False,
                    "mfaBackupCodes": hashed_codes,
                }
            )

        await log_mfa_action(user_id, "setup", True, request)

        logger.info(f"MFA setup initiated for user: {user_id[:8]}...")

        return MFASetupResponse(
            success=True,
            message="Scan the QR code with your authenticator app, then verify with a code",
            qr_code_base64=qr_base64,
            manual_entry_key=format_secret_for_display(secret),
            backup_codes=plain_codes,
        )

    except Exception as e:
        logger.error(f"MFA setup error: {e}")
        await log_mfa_action(user_id, "setup", False, request)
        raise HTTPException(status_code=500, detail="Failed to setup MFA")


@router.post("/verify", response_model=MFAVerifyResponse)
async def verify_mfa_setup(body: MFAVerifyRequest, request: Request):
    """
    Verify TOTP code to complete MFA setup

    After successful verification, MFA will be enabled for the account.
    """
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    user_id = user.get("user_id")

    try:
        security = await prisma.usersecurity.find_unique(where={"userId": user_id})

        if not security or not security.totpSecret:
            raise HTTPException(
                status_code=400, detail="MFA not setup. Call /setup first."
            )

        if security.twoFactorEnabled:
            return MFAVerifyResponse(
                success=True, message="MFA is already enabled", mfa_enabled=True
            )

        # Verify the code
        if not verify_totp_code(security.totpSecret, body.code):
            await log_mfa_action(user_id, "verify", False, request)
            return MFAVerifyResponse(
                success=False, message="Invalid code. Please try again."
            )

        # Enable MFA
        await prisma.usersecurity.update(
            where={"userId": user_id},
            data={
                "twoFactorEnabled": True,
                "totpVerified": True,
                "mfaEnabledAt": datetime.utcnow(),
                "lastMfaVerification": datetime.utcnow(),
            },
        )

        await log_mfa_action(user_id, "verify", True, request)

        logger.info(f"MFA enabled for user: {user_id[:8]}...")

        return MFAVerifyResponse(
            success=True, message="MFA has been enabled successfully", mfa_enabled=True
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"MFA verify error: {e}")
        raise HTTPException(status_code=500, detail="Failed to verify MFA")


@router.post("/validate", response_model=MFAVerifyResponse)
async def validate_mfa_code(body: MFAValidateRequest, request: Request):
    """
    Validate TOTP code during login

    Called after successful password authentication when MFA is enabled.
    """
    try:
        security = await prisma.usersecurity.find_unique(where={"userId": body.user_id})

        if not security or not security.twoFactorEnabled:
            return MFAVerifyResponse(
                success=True, message="MFA not enabled for this account"
            )

        # Try TOTP code first
        if verify_totp_code(security.totpSecret, body.code):
            # Update last verification
            await prisma.usersecurity.update(
                where={"userId": body.user_id},
                data={"lastMfaVerification": datetime.utcnow()},
            )
            await log_mfa_action(body.user_id, "validate", True, request)
            return MFAVerifyResponse(success=True, message="MFA validated")

        # Try backup code
        backup_codes = security.mfaBackupCodes or []
        code_normalized = body.code.replace("-", "").replace(" ", "").upper()
        code_hash = hashlib.sha256(code_normalized.encode()).hexdigest()

        for idx, stored_hash in enumerate(backup_codes):
            if stored_hash and code_hash == stored_hash:
                # Mark backup code as used
                backup_codes[idx] = None
                await prisma.usersecurity.update(
                    where={"userId": body.user_id},
                    data={
                        "mfaBackupCodes": backup_codes,
                        "lastMfaVerification": datetime.utcnow(),
                    },
                )
                await log_mfa_action(body.user_id, "backup_used", True, request)
                logger.warning(f"Backup code used for user: {body.user_id[:8]}...")
                return MFAVerifyResponse(
                    success=True, message="MFA validated (backup code used)"
                )

        await log_mfa_action(body.user_id, "validate", False, request)
        return MFAVerifyResponse(success=False, message="Invalid MFA code")

    except Exception as e:
        logger.error(f"MFA validate error: {e}")
        raise HTTPException(status_code=500, detail="MFA validation failed")


@router.post("/disable", response_model=MFAVerifyResponse)
async def disable_mfa(body: MFADisableRequest, request: Request):
    """
    Disable MFA for authenticated user

    Requires current password for security confirmation.
    """
    from app.utils.password_handler import PasswordHandler

    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    user_id = user.get("user_id")

    try:
        # Get user with password
        db_user = await prisma.user.find_unique(where={"id": user_id})
        if not db_user or not db_user.passwordHash:
            raise HTTPException(status_code=400, detail="User not found")

        # Verify password
        if not PasswordHandler.verify_password(body.password, db_user.passwordHash):
            await log_mfa_action(user_id, "disable", False, request)
            return MFAVerifyResponse(success=False, message="Invalid password")

        # Disable MFA
        await prisma.usersecurity.update(
            where={"userId": user_id},
            data={
                "twoFactorEnabled": False,
                "totpVerified": False,
                "totpSecret": None,
                "mfaBackupCodes": [],
                "mfaEnabledAt": None,
            },
        )

        await log_mfa_action(user_id, "disable", True, request)

        logger.info(f"MFA disabled for user: {user_id[:8]}...")

        return MFAVerifyResponse(
            success=True, message="MFA has been disabled", mfa_enabled=False
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"MFA disable error: {e}")
        raise HTTPException(status_code=500, detail="Failed to disable MFA")


@router.get("/status", response_model=MFAStatusResponse)
async def get_mfa_status(request: Request):
    """
    Get MFA status for authenticated user
    """
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    user_id = user.get("user_id")

    try:
        security = await prisma.usersecurity.find_unique(where={"userId": user_id})

        if not security:
            return MFAStatusResponse(
                mfa_enabled=False, mfa_verified=False, backup_codes_remaining=0
            )

        # Count remaining backup codes
        backup_remaining = sum(
            1 for c in (security.mfaBackupCodes or []) if c is not None
        )

        return MFAStatusResponse(
            mfa_enabled=security.twoFactorEnabled or False,
            mfa_verified=security.totpVerified or False,
            backup_codes_remaining=backup_remaining,
            enabled_at=security.mfaEnabledAt.isoformat()
            if security.mfaEnabledAt
            else None,
        )

    except Exception as e:
        logger.error(f"MFA status error: {e}")
        raise HTTPException(status_code=500, detail="Failed to get MFA status")


@router.post("/backup-codes", response_model=BackupCodesResponse)
async def regenerate_backup_codes(request: Request):
    """
    Generate new backup codes

    Warning: This invalidates all existing backup codes.
    """
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    user_id = user.get("user_id")

    try:
        security = await prisma.usersecurity.find_unique(where={"userId": user_id})

        if not security or not security.twoFactorEnabled:
            return BackupCodesResponse(
                success=False, message="MFA must be enabled first"
            )

        # Generate new backup codes
        plain_codes, hashed_codes = generate_backup_codes()

        await prisma.usersecurity.update(
            where={"userId": user_id}, data={"mfaBackupCodes": hashed_codes}
        )

        await log_mfa_action(user_id, "backup_regenerate", True, request)

        logger.info(f"Backup codes regenerated for user: {user_id[:8]}...")

        return BackupCodesResponse(
            success=True,
            message="New backup codes generated",
            codes=plain_codes,
            warning="Save these codes securely. They will not be shown again.",
        )

    except Exception as e:
        logger.error(f"Backup codes error: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate backup codes")
