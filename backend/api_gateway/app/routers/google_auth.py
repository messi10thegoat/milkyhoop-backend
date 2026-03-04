"""
Google OAuth2 Login/Signup Endpoint
====================================
POST /api/auth/google

Receives a Google ID token from the frontend (Google Identity Services SDK),
verifies it, and either logs in or creates a new tenant+user.

Response format matches existing /api/auth/login for frontend compatibility.
"""
import os
import uuid
import secrets
import logging
from typing import Dict, Any, Optional

from fastapi import APIRouter, HTTPException, status, Request
from pydantic import BaseModel
from google.oauth2 import id_token as google_id_token
from google.auth.transport import requests as google_requests

from backend.api_gateway.app.services.auth_instance import auth_client
from backend.api_gateway.app.services.audit_logger import log_auth_event, AuditEventType
from backend.api_gateway.app.services.device_service import DeviceService
from backend.api_gateway.app.services.session_manager import session_manager
from backend.api_gateway.app.services.db_pool import get_db_pool
from backend.api_gateway.libs.milkyhoop_prisma import Prisma

logger = logging.getLogger(__name__)
router = APIRouter()

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")


# =====================================================
# REQUEST/RESPONSE MODELS
# =====================================================

class GoogleAuthRequest(BaseModel):
    credential: str  # Google ID token from frontend


class AuthResponse(BaseModel):
    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None


# =====================================================
# HELPERS
# =====================================================

def get_client_ip(request: Request) -> str:
    """Get client IP address from request."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def generate_secure_password(length: int = 32) -> str:
    """Generate a cryptographically secure random password."""
    return secrets.token_urlsafe(length)


# =====================================================
# ENDPOINT
# =====================================================

@router.post("", response_model=AuthResponse)
async def google_auth(request: GoogleAuthRequest, http_request: Request):
    """
    Google OAuth2 login/signup endpoint.

    1. Verify Google ID token
    2. Extract email, name, picture
    3. If user exists -> login (generate tokens, register device, activate session)
    4. If user doesn't exist -> create tenant+user via onboarding, then return tokens
    """
    if not GOOGLE_CLIENT_ID:
        logger.error("GOOGLE_CLIENT_ID not configured")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Google OAuth not configured on server",
        )

    # ----- Step 1: Verify the Google ID token -----
    try:
        idinfo = google_id_token.verify_oauth2_token(
            request.credential,
            google_requests.Request(),
            GOOGLE_CLIENT_ID,
        )
    except ValueError as e:
        logger.warning(f"Google ID token verification failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Google credential",
        )

    # ----- Step 2: Extract user info from token payload -----
    email = idinfo.get("email")
    name = idinfo.get("name", email.split("@")[0] if email else "User")
    picture = idinfo.get("picture")
    email_verified = idinfo.get("email_verified", False)

    if not email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Google account has no email address",
        )

    if not email_verified:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Google account email is not verified",
        )

    logger.info(f"Google auth request for email: {email}")

    # ----- Step 3: Check if user exists -----
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        user_row = await conn.fetchrow(
            'SELECT id, email, name, role, "tenantId" FROM "User" WHERE email = $1',
            email,
        )

    if user_row:
        # ===== EXISTING USER: LOGIN FLOW =====
        return await _login_existing_user(user_row, http_request)
    else:
        # ===== NEW USER: SIGNUP FLOW =====
        return await _signup_new_user(email, name, http_request)


async def _login_existing_user(user_row, http_request: Request) -> AuthResponse:
    """
    Login flow for existing user -- mirrors auth.py /login endpoint.
    Generates tokens, registers device, activates session.
    """
    user_id = str(user_row["id"])
    email = user_row["email"]
    user_name = user_row["name"]
    role = user_row["role"]
    tenant_id = user_row["tenantId"]
    device_id = str(uuid.uuid4())
    device_type = "mobile"

    # Generate JWT tokens with device claims (same as login endpoint)
    try:
        token_response = await auth_client._generate_tokens_locally(
            user_id=user_id,
            tenant_id=tenant_id,
            email=email,
            role=role,
            username=email.split("@")[0],
            device_info="Google OAuth - Mobile Web",
            device_id=device_id,
            device_type=device_type,
        )
    except Exception as e:
        logger.error(f"Token generation failed for Google login: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate authentication tokens",
        )

    # Register device in DB (same pattern as auth.py login)
    try:
        prisma = Prisma()
        await prisma.connect()
        device_service = DeviceService(prisma)
        await device_service.register_device(
            user_id=user_id,
            tenant_id=tenant_id,
            device_type=device_type,
            browser_id=str(uuid.uuid4()),
            device_fingerprint=None,
            user_agent=http_request.headers.get("User-Agent"),
            refresh_token_hash=DeviceService.hash_refresh_token(
                token_response.refresh_token
            ),
            ip_address=get_client_ip(http_request),
            device_id=device_id,
        )
        logger.info(f"Device registered for Google login: {device_id[:8]}...")
    except Exception as e:
        logger.error(f"Device registration failed (non-blocking): {e}")
    finally:
        try:
            await prisma.disconnect()
        except Exception:
            pass

    # Activate session in Redis (same as auth.py login)
    session_manager.activate_mobile_device(user_id=user_id, device_id=device_id)
    logger.info(
        f"Session activated (Google login): user={user_id[:8]}..., device={device_id[:8]}..."
    )

    # Audit log
    await log_auth_event(
        event_type=AuditEventType.LOGIN,
        user_id=user_id,
        ip_address=http_request.client.host if http_request.client else None,
        user_agent=http_request.headers.get("user-agent"),
        success=True,
        metadata={
            "email": email,
            "tenant_id": tenant_id,
            "device_id": device_id,
            "device_type": device_type,
            "auth_method": "google_oauth",
        },
    )

    return AuthResponse(
        success=True,
        message="Login successful",
        data={
            "user_id": user_id,
            "email": email,
            "name": user_name,
            "role": role,
            "tenant_id": tenant_id,
            "access_token": token_response.access_token,
            "refresh_token": token_response.refresh_token,
            "device_id": device_id,
            "device_type": device_type,
        },
    )


async def _signup_new_user(email: str, name: str, http_request: Request) -> AuthResponse:
    """
    Signup flow for new user -- uses create_tenant_and_user from onboarding_service.
    """
    random_password = generate_secure_password()

    try:
        from backend.api_gateway.app.services.onboarding_service import (
            create_tenant_and_user,
        )

        result = await create_tenant_and_user(
            email=email,
            password=random_password,
            business_name=name,
        )
    except Exception as e:
        logger.error(f"Google signup - create_tenant_and_user failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create account",
        )

    logger.info(
        f"Google signup complete: tenant={result['tenant_id']}, user={result['user_id'][:8]}..."
    )

    # Audit log
    await log_auth_event(
        event_type=AuditEventType.REGISTER,
        user_id=result["user_id"],
        ip_address=http_request.client.host if http_request.client else None,
        user_agent=http_request.headers.get("user-agent"),
        success=True,
        metadata={
            "email": email,
            "tenant_id": result["tenant_id"],
            "auth_method": "google_oauth",
        },
    )

    return AuthResponse(
        success=True,
        message="Account created successfully",
        data={
            "user_id": result["user_id"],
            "email": result["email"],
            "name": result["name"],
            "role": result["role"],
            "tenant_id": result["tenant_id"],
            "access_token": result["access_token"],
            "refresh_token": result["refresh_token"],
            "device_id": result["device_id"],
            "device_type": result["device_type"],
        },
    )
