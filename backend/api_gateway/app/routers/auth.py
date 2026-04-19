import logging
import uuid
import jwt
from typing import Dict, Any, Optional
from fastapi import APIRouter, HTTPException, status, Request
from fastapi.responses import Response
from pydantic import BaseModel
from backend.api_gateway.app.services.auth_instance import auth_client
from backend.api_gateway.app.services.audit_logger import log_auth_event, AuditEventType
from backend.api_gateway.app.services.device_service import DeviceService
from backend.api_gateway.app.services.session_manager import session_manager
import os
import asyncpg
from datetime import datetime, timedelta
from backend.api_gateway.libs.milkyhoop_prisma import Prisma

logger = logging.getLogger(__name__)
router = APIRouter()

# Prisma client for device management
prisma = Prisma()


def get_client_ip(request: Request) -> str:
    """Get client IP address from request"""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# Initialize auth client

# =====================================================
# REQUEST/RESPONSE MODELS
# =====================================================


class RegisterRequest(BaseModel):
    email: str
    password: str
    name: Optional[str] = None
    username: Optional[str] = None


class LoginRequest(BaseModel):
    email: str
    password: str
    browser_id: Optional[str] = None  # Browser profile ID for device tracking
    device_fingerprint: Optional[str] = None  # Browser fingerprint


class ValidateTokenRequest(BaseModel):
    access_token: str


class AuthResponse(BaseModel):
    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None


# =====================================================
# DB POOL FOR TENANT QUERIES
# =====================================================


async def get_pool() -> asyncpg.Pool:
    """Get singleton connection pool (Law 32)."""
    from ..services.db_pool import get_db_pool

    return await get_db_pool()


class SwitchTenantRequest(BaseModel):
    tenant_id: str


# =====================================================
# AUTHENTICATION ENDPOINTS
# =====================================================


@router.post("/register", response_model=AuthResponse)
async def register_user(request: RegisterRequest, http_request: Request):
    """User registration endpoint"""
    try:
        logger.info(f"Registration request for email: {request.email}")

        # Connect to auth service

        # Call registration service
        result = await auth_client.register_user(
            email=request.email,
            password=request.password,
            name=request.name,
            username=request.username,
        )

        if result["success"]:
            # Log successful registration
            await log_auth_event(
                event_type=AuditEventType.REGISTER,
                user_id=result["user_id"],
                ip_address=http_request.client.host if http_request.client else None,
                user_agent=http_request.headers.get("user-agent"),
                success=True,
                metadata={"email": request.email},
            )

            return AuthResponse(
                success=True,
                message="Registration successful",
                data={
                    "user_id": result["user_id"],
                    "email": request.email,
                    "access_token": result["access_token"],
                    "refresh_token": result["refresh_token"],
                },
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=result["message"]
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Registration error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Registration failed due to server error",
        )
    finally:
        await auth_client.disconnect()


@router.post("/login", response_model=AuthResponse)
async def login_user(request: LoginRequest, http_request: Request):
    """
    User login endpoint with device registration for single session enforcement.

    Mobile web login will:
    1. Authenticate user via auth_service
    2. Register device (kicks existing mobile sessions + cascade kicks web sessions)
    3. Return tokens + device_id for WebSocket connection
    """
    try:
        logger.info(f"Login request for email: {request.email}")

        # Generate device_id BEFORE calling auth_service
        # This ensures device_id is embedded in JWT
        device_id = str(uuid.uuid4())
        device_type = "mobile"  # Mobile web = primary device

        # Call login service with device claims
        result = await auth_client.login_user(
            email=request.email,
            password=request.password,
            device_id=device_id,
            device_type=device_type,
        )

        if result["success"]:
            # ===== DEVICE REGISTRATION (BLOCKING) =====
            # Register device FIRST to send WebSocket force_logout to existing sessions
            # Pass the same device_id so DB record ID matches JWT/Redis/WebSocket
            try:
                await prisma.connect()
                device_service = DeviceService(prisma)
                db_device_id = await device_service.register_device(
                    user_id=result["user_id"],
                    tenant_id=result["tenant_id"],
                    device_type=device_type,
                    browser_id=request.browser_id or str(uuid.uuid4()),
                    device_fingerprint=request.device_fingerprint,
                    user_agent=http_request.headers.get("User-Agent"),
                    refresh_token_hash=DeviceService.hash_refresh_token(
                        result["refresh_token"]
                    ),
                    ip_address=get_client_ip(http_request),
                    device_id=device_id,  # Pass same device_id for consistent ID
                )
                logger.info(
                    f"✅ Device registered in DB: {db_device_id[:8]}... (should match JWT device_id)"
                )
            except Exception as e:
                logger.error(f"Device registration failed (BLOCKING): {e}")
                # Continue even if device registration fails - session enforcement via Redis still works
            finally:
                await prisma.disconnect()

            # ===== ATOMIC SESSION ENFORCEMENT =====
            # Set mobile session + cascade kill web session (no race condition)
            session_manager.activate_mobile_device(
                user_id=result["user_id"], device_id=device_id
            )
            logger.info(
                f"✅ Session activated: user={result['user_id'][:8]}..., device={device_id[:8]}..., type={device_type}"
            )

            # Log successful login
            await log_auth_event(
                event_type=AuditEventType.LOGIN,
                user_id=result["user_id"],
                ip_address=http_request.client.host if http_request.client else None,
                user_agent=http_request.headers.get("user-agent"),
                success=True,
                metadata={
                    "email": result["email"],
                    "tenant_id": result.get("tenant_id"),
                    "device_id": device_id,
                    "device_type": device_type,
                },
            )

            # --- Resolve tenant: last_active > primary ---
            raw_tenant_id = result["tenant_id"]
            resolved_tenant_id = raw_tenant_id
            business_role_code = "OWNER"

            try:
                _tpool = await get_pool()
                async with _tpool.acquire() as conn:
                    await conn.execute("SET LOCAL app.tenant_id = 'SYSTEM'")
                    user_row = await conn.fetchrow(
                        'SELECT last_active_tenant_id FROM "User" WHERE id = $1',
                        result["user_id"],
                    )
                    last_active = (
                        user_row["last_active_tenant_id"] if user_row else None
                    )

                    if last_active and last_active != raw_tenant_id:
                        has_access = await conn.fetchval(
                            "SELECT EXISTS(SELECT 1 FROM user_tenant_roles "
                            "WHERE user_id = $1 AND tenant_id = $2 AND status = 'active')",
                            result["user_id"],
                            last_active,
                        )
                        if has_access:
                            tenant_ok = await conn.fetchval(
                                'SELECT EXISTS(SELECT 1 FROM "Tenant" WHERE id = $1 AND suspended_at IS NULL)',
                                last_active,
                            )
                            if tenant_ok:
                                resolved_tenant_id = last_active
                            else:
                                await conn.execute(
                                    'UPDATE "User" SET last_active_tenant_id = NULL WHERE id = $1',
                                    result["user_id"],
                                )
                        else:
                            await conn.execute(
                                'UPDATE "User" SET last_active_tenant_id = NULL WHERE id = $1',
                                result["user_id"],
                            )

                    # Resolve business role for resolved tenant
                    role_row = await conn.fetchrow(
                        "SELECT r.code FROM user_tenant_roles utr "
                        "JOIN roles r ON r.id = utr.role_id "
                        "WHERE utr.user_id = $1 AND utr.tenant_id = $2 AND utr.status = 'active'",
                        result["user_id"],
                        resolved_tenant_id,
                    )
                    if role_row:
                        business_role_code = role_row["code"]
                    elif resolved_tenant_id == raw_tenant_id:
                        business_role_code = "OWNER"
                    else:
                        business_role_code = "VIEWER"
            except Exception as e:
                logger.warning(f"[login] Failed to resolve last_active_tenant: {e}")

            # Re-generate tokens if tenant changed
            if resolved_tenant_id != raw_tenant_id:
                _js = os.getenv(
                    "JWT_SECRET",
                    "bb599073be39674d540ba07d77967282d4fa26247f6d17d8a60b093002d70d40",
                )
                now = datetime.utcnow()
                ap = {
                    "user_id": result["user_id"],
                    "tenant_id": resolved_tenant_id,
                    "role": result["role"],
                    "email": result["email"],
                    "username": result["name"] or result["email"],
                    "token_type": "access",
                    "device_id": device_id,
                    "device_type": device_type,
                    "iat": now,
                    "exp": now + timedelta(days=7),
                    "nbf": now,
                }
                rp = {
                    "user_id": result["user_id"],
                    "session_id": result["user_id"],
                    "tenant_id": resolved_tenant_id,
                    "token_type": "refresh",
                    "device_id": device_id,
                    "device_type": device_type,
                    "iat": now,
                    "exp": now + timedelta(days=30),
                    "nbf": now,
                }
                result["access_token"] = jwt.encode(ap, _js, algorithm="HS256")
                result["refresh_token"] = jwt.encode(rp, _js, algorithm="HS256")

            return AuthResponse(
                success=True,
                message="Login successful",
                data={
                    "user_id": result["user_id"],
                    "email": result["email"],
                    "name": result["name"],
                    "role": result["role"],
                    "tenant_id": resolved_tenant_id,
                    "access_token": result["access_token"],
                    "refresh_token": result["refresh_token"],
                    "device_id": device_id,
                    "device_type": device_type,
                    "business_role_code": business_role_code,
                },
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail=result["message"]
            )

    except HTTPException as http_exc:
        # Log failed login (HTTP exceptions like 401)
        await log_auth_event(
            event_type=AuditEventType.FAILED_LOGIN,
            ip_address=http_request.client.host if http_request.client else None,
            user_agent=http_request.headers.get("user-agent"),
            success=False,
            error_message=str(http_exc.detail),
            metadata={"email": request.email},
        )
        raise
    except Exception as e:
        # Log failed login (server errors)
        logger.error(f"Login error: {e}")
        await log_auth_event(
            event_type=AuditEventType.FAILED_LOGIN,
            ip_address=http_request.client.host if http_request.client else None,
            user_agent=http_request.headers.get("user-agent"),
            success=False,
            error_message=str(e),
            metadata={"email": request.email},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Login failed due to server error",
        )
    finally:
        await auth_client.disconnect()


@router.post("/validate", response_model=AuthResponse)
async def validate_token(request: ValidateTokenRequest):
    """Token validation endpoint"""
    try:
        logger.info("Token validation request")

        # Connect to auth service

        # Call token validation service
        result = await auth_client.validate_token(request.access_token)

        if result["valid"]:
            return AuthResponse(
                success=True,
                message="Token is valid",
                data={
                    "user_id": result["user_id"],
                    "tenant_id": result["tenant_id"],
                    "role": result["role"],
                    "expires_at": result["expires_at"],
                },
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail=result["message"]
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Token validation error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Token validation failed due to server error",
        )
    finally:
        await auth_client.disconnect()


@router.get("/profile/{user_id}", response_model=AuthResponse)
async def get_user_profile(user_id: str):
    """Get user profile endpoint"""
    try:
        logger.info(f"Get profile request for user: {user_id}")

        # Connect to auth service

        # Call profile service
        result = await auth_client.get_user_profile(user_id)

        if result["success"]:
            return AuthResponse(
                success=True,
                message="Profile retrieved successfully",
                data={
                    "user_id": result["user_id"],
                    "email": result["email"],
                    "name": result["name"],
                    "username": result["username"],
                    "role": result["role"],
                },
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=result["message"]
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get profile error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Get profile failed due to server error",
        )
    finally:
        await auth_client.disconnect()


@router.get("/health")
async def auth_health_check():
    """Auth service health check"""
    try:
        await auth_client.disconnect()
        return {"status": "healthy", "service": "auth"}
    except Exception as e:
        logger.error(f"Auth health check failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Auth service unavailable",
        )


@router.get("/verify", status_code=204)
async def verify_session(request: Request):
    """
    Lightweight session verification endpoint (204 No Content).

    Used by frontend visibilitychange listener to check if session is still valid.
    Auth middleware handles the actual validation:
    - Validates JWT signature & expiration
    - Checks device_id in JWT against Redis session authority
    - Returns 401 SESSION_REPLACED if session was taken over

    If request reaches this endpoint, session is valid.
    Returns 204 (no body) for minimal overhead.
    """
    logger.debug(
        f"Session verify ping from user: {getattr(request.state, 'user', {}).get('user_id', 'unknown')[:8]}..."
    )
    return Response(status_code=204)


# =====================================================
# MULTI-TENANT ENDPOINTS
# =====================================================

_JWT_SECRET = os.getenv(
    "JWT_SECRET", "bb599073be39674d540ba07d77967282d4fa26247f6d17d8a60b093002d70d40"
)


@router.get("/tenants")
async def list_user_tenants(request: Request):
    user_data = getattr(request.state, "user", {})
    user_id = user_data.get("user_id")
    tenant_id = user_data.get("tenant_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("SET LOCAL app.tenant_id = 'SYSTEM'")
        user_row = await conn.fetchrow(
            'SELECT "tenantId" FROM "User" WHERE id = $1', user_id
        )
        primary_tenant_id = user_row["tenantId"] if user_row else None
        utr_rows = await conn.fetch(
            "SELECT utr.tenant_id, r.code as role_code "
            "FROM user_tenant_roles utr JOIN roles r ON r.id = utr.role_id "
            "WHERE utr.user_id = $1 AND utr.status = 'active'",
            user_id,
        )
        tenant_roles = {row["tenant_id"]: row["role_code"] for row in utr_rows}
        if primary_tenant_id and primary_tenant_id not in tenant_roles:
            tenant_roles[primary_tenant_id] = "OWNER"
        tenant_ids = list(tenant_roles.keys())
        if not tenant_ids:
            return {"tenants": [], "current_tenant_id": tenant_id}
        tenant_rows = await conn.fetch(
            'SELECT id, display_name, logo_url FROM "Tenant" '
            "WHERE id = ANY($1) AND suspended_at IS NULL",
            tenant_ids,
        )
        tenants = []
        for t in tenant_rows:
            tenants.append(
                {
                    "tenant_id": t["id"],
                    "display_name": t["display_name"],
                    "logo_url": t["logo_url"],
                    "role_code": tenant_roles.get(t["id"], "VIEWER"),
                    "is_active": t["id"] == tenant_id,
                }
            )
        tenants.sort(key=lambda x: (not x["is_active"], x["display_name"]))
        return {"tenants": tenants, "current_tenant_id": tenant_id}


@router.post("/switch-tenant")
async def switch_tenant(request: Request, body: SwitchTenantRequest):
    user_data = getattr(request.state, "user", {})
    user_id = user_data.get("user_id")
    user_email = user_data.get("email")
    target_tenant_id = body.tenant_id
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("SET LOCAL app.tenant_id = 'SYSTEM'")
        user_row = await conn.fetchrow(
            'SELECT "tenantId", name, role FROM "User" WHERE id = $1', user_id
        )
        if not user_row:
            raise HTTPException(status_code=404, detail="User not found")
        primary_tenant_id = user_row["tenantId"]
        user_name = user_row["name"] or user_email or ""
        subscription_role = user_row["role"] or "FREE"

        tenant_row = await conn.fetchrow(
            'SELECT id FROM "Tenant" WHERE id = $1 AND suspended_at IS NULL',
            target_tenant_id,
        )
        if not tenant_row:
            raise HTTPException(status_code=404, detail="Tenant not found or suspended")

        role_code = None
        if target_tenant_id == primary_tenant_id:
            role_code = "OWNER"
        else:
            utr_row = await conn.fetchrow(
                "SELECT r.code as role_code FROM user_tenant_roles utr "
                "JOIN roles r ON r.id = utr.role_id "
                "WHERE utr.user_id = $1 AND utr.tenant_id = $2 AND utr.status = 'active'",
                user_id,
                target_tenant_id,
            )
            if utr_row:
                role_code = utr_row["role_code"]

        if not role_code:
            raise HTTPException(status_code=403, detail="No access to this tenant")

        await conn.execute(
            'UPDATE "User" SET last_active_tenant_id = $1 WHERE id = $2',
            target_tenant_id,
            user_id,
        )

    now = datetime.utcnow()
    access_payload = {
        "user_id": user_id,
        "tenant_id": target_tenant_id,
        "role": subscription_role,
        "email": user_email,
        "username": user_name,
        "token_type": "access",
        "iat": now,
        "exp": now + timedelta(days=7),
        "nbf": now,
    }
    refresh_payload = {
        "user_id": user_id,
        "session_id": user_id,
        "tenant_id": target_tenant_id,
        "token_type": "refresh",
        "iat": now,
        "exp": now + timedelta(days=30),
        "nbf": now,
    }
    access_token = jwt.encode(access_payload, _JWT_SECRET, algorithm="HS256")
    refresh_token = jwt.encode(refresh_payload, _JWT_SECRET, algorithm="HS256")

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "tenant_id": target_tenant_id,
        "role_code": role_code,
    }


# =====================================================
# WEEK 2 DAY 4: TOKEN REFRESH & SESSION MANAGEMENT
# =====================================================


class RefreshTokenRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: Optional[str] = None
    logout_all_devices: bool = False


class SessionResponse(BaseModel):
    session_id: str
    device: Optional[str] = "Unknown"
    ip_address: Optional[str] = None
    created_at: Optional[str] = None
    last_active: Optional[str] = None


@router.post("/refresh", response_model=AuthResponse)
async def refresh_access_token(data: RefreshTokenRequest, http_request: Request):
    """
    Refresh access token using refresh token

    Request:
        - refresh_token: Valid refresh token

    Response:
        - success: Boolean
        - access_token: New JWT access token
        - refresh_token: New refresh token
        - expires_at: Token expiration timestamp

    Security:
        - Checks session authority before allowing refresh
        - Prevents zombie sessions from refreshing tokens
    """
    try:
        logger.info("Token refresh request received")

        # ===== SESSION AUTHORITY CHECK (KRITIS) =====
        # Decode refresh token to get device info (unsafe decode, signature verified by auth_service)
        try:
            decoded = jwt.decode(
                data.refresh_token, options={"verify_signature": False}
            )
            user_id = decoded.get("user_id")
            device_id = decoded.get("device_id")
            device_type = decoded.get("device_type")

            # If token has device claims, verify session is still valid
            if device_id and device_type and user_id:
                if not session_manager.is_session_valid(
                    user_id, device_type, device_id
                ):
                    logger.warning(
                        f"🚫 Refresh blocked: session replaced for user {user_id[:8]}..., type={device_type}"
                    )
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Session telah digantikan di perangkat lain",
                    )
        except jwt.DecodeError:
            logger.warning("Could not decode refresh token for session check")
            # Continue - let auth_service validate the token

        # Call auth service
        result = await auth_client.refresh_token(data.refresh_token)

        if result.get("success"):
            logger.info("Token refreshed successfully")

            # Log successful token refresh
            await log_auth_event(
                event_type=AuditEventType.TOKEN_REFRESH,
                user_id=result.get("user_id"),
                ip_address=http_request.client.host if http_request.client else None,
                user_agent=http_request.headers.get("user-agent"),
                success=True,
            )

            return AuthResponse(
                success=True,
                message="Token refreshed successfully",
                data={
                    "access_token": result["access_token"],
                    "refresh_token": result["refresh_token"],
                    "expires_at": result.get("expires_at"),
                },
            )
        else:
            error_msg = result.get("error", "Token refresh failed")
            logger.warning(f"Token refresh failed: {error_msg}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail=error_msg
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in refresh endpoint: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Token refresh error: {str(e)}",
        )


@router.get("/sessions")
async def list_user_sessions(user_id: str):
    """
    List all active sessions for authenticated user

    Query Parameters:
        - user_id: User ID (from JWT token in production)

    Response:
        - success: Boolean
        - sessions: List of active sessions
        - total: Total session count
    """
    try:
        logger.info(f"Listing sessions for user: {user_id}")

        # Call auth service
        result = await auth_client.list_active_sessions(user_id)

        if result.get("success"):
            logger.info(f"Found {result.get('total', 0)} active sessions")
            return {
                "success": True,
                "data": {"sessions": result["sessions"], "total": result["total"]},
            }
        else:
            error_msg = result.get("error", "Failed to list sessions")
            logger.warning(f"List sessions failed: {error_msg}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=error_msg
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in list sessions endpoint: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"List sessions error: {str(e)}",
        )


@router.delete("/sessions/{session_id}")
async def revoke_user_session(session_id: str, user_id: str, http_request: Request):
    """
    Revoke a specific user session (logout from device)

    Path Parameters:
        - session_id: Session ID to revoke

    Query Parameters:
        - user_id: User ID (from JWT token in production)

    Response:
        - success: Boolean
        - message: Success message
    """
    try:
        logger.info(f"Revoking session {session_id} for user {user_id}")

        # Call auth service
        result = await auth_client.revoke_session(session_id, user_id)

        if result.get("success"):
            logger.info(f"Session {session_id} revoked successfully")

            # Log session revocation
            await log_auth_event(
                event_type=AuditEventType.SESSION_REVOKED,
                user_id=user_id,
                ip_address=http_request.client.host if http_request.client else None,
                user_agent=http_request.headers.get("user-agent"),
                success=True,
                metadata={"session_id": session_id},
            )

            return {
                "success": True,
                "message": result.get("message", "Session revoked successfully"),
            }
        else:
            error_msg = result.get("error", "Failed to revoke session")
            logger.warning(f"Revoke session failed: {error_msg}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=error_msg
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in revoke session endpoint: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Revoke session error: {str(e)}",
        )


@router.post("/logout", response_model=AuthResponse)
async def logout_user(data: LogoutRequest, user_id: str, http_request: Request):
    """
    Logout user - revoke refresh token(s) + session

    Query Parameters:
        - user_id: User ID (from JWT token in production)

    Request Body:
        - refresh_token: Specific token to revoke (optional)
        - logout_all_devices: If true, logout from all devices

    Response:
        - success: Boolean
        - message: Success message
        - revoked_tokens: Number of tokens revoked
    """
    try:
        logger.info(f"Logout request for user: {user_id}")

        # Get device type from request state (set by auth middleware)
        device_type = getattr(http_request.state, "user", {}).get("device_type", "web")

        # ===== SESSION REVOCATION =====
        if data.logout_all_devices or device_type == "mobile":
            # CASCADE: Mobile logout or logout_all kills all sessions
            session_manager.revoke_all(user_id)
            logger.info(f"✅ All sessions revoked for user {user_id[:8]}...")
        else:
            # Desktop logout only kills desktop session
            session_manager.revoke_device(user_id, "web")
            logger.info(f"✅ Web session revoked for user {user_id[:8]}...")

        # Call auth service to revoke refresh tokens
        result = await auth_client.logout(
            user_id=user_id,
            refresh_token=data.refresh_token,
            logout_all_devices=data.logout_all_devices,
        )

        if result.get("success"):
            logger.info(f"User {user_id} logged out successfully")

            # Log logout event
            await log_auth_event(
                event_type=AuditEventType.LOGOUT,
                user_id=user_id,
                ip_address=http_request.client.host if http_request.client else None,
                user_agent=http_request.headers.get("user-agent"),
                success=True,
                metadata={
                    "logout_all_devices": data.logout_all_devices,
                    "device_type": device_type,
                    "revoked_tokens": result.get("revoked_tokens", 0),
                },
            )

            return AuthResponse(
                success=True,
                message=result.get("message", "Logged out successfully"),
                data={"revoked_tokens": result.get("revoked_tokens", 0)},
            )
        else:
            error_msg = result.get("error", "Logout failed")
            logger.warning(f"Logout failed: {error_msg}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=error_msg
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in logout endpoint: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Logout error: {str(e)}",
        )
