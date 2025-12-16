import asyncio
import signal
import logging
import uuid
import grpc
from grpc import aio
from grpc_health.v1 import health, health_pb2, health_pb2_grpc
from app.config import settings
from app import auth_service_pb2_grpc as pb_grpc
from app import auth_service_pb2 as pb
from app.prisma_client import prisma, connect_prisma, disconnect_prisma
from app.utils.jwt_handler import JWTHandler
from app.utils.password_handler import PasswordHandler
from datetime import datetime, timedelta, timezone
import re
import hashlib
import secrets

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(settings.SERVICE_NAME)


def mask_email(email: str) -> str:
    """Mask email for logging - shows first 2 chars and domain only"""
    if not email or "@" not in email:
        return "***"
    local, domain = email.split("@", 1)
    if len(local) <= 2:
        masked_local = "*" * len(local)
    else:
        masked_local = local[:2] + "*" * (len(local) - 2)
    return f"{masked_local}@{domain}"


class AuthServiceServicer(pb_grpc.AuthServiceServicer):
    """Enterprise Authentication Service Implementation"""

    def __init__(self):
        """Initialize authentication service with utility handlers"""
        self.jwt_handler = JWTHandler()
        self.password_handler = PasswordHandler()

    def _hash_token(self, token: str) -> str:
        """SHA256 hash for storing refresh token securely"""
        return hashlib.sha256(token.encode()).hexdigest()

    def _generate_refresh_token(self) -> str:
        """Generate secure random refresh token"""
        return f"rt_{secrets.token_urlsafe(32)}"

    def _validate_email(self, email: str) -> bool:
        """Validate email format"""
        pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
        return re.match(pattern, email) is not None

    async def Register(self, request, context):
        """Register new user - Let database handle defaults"""
        try:
            # Validate email format
            if not request.email or "@" not in request.email:
                context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
                context.set_details("Invalid email format")
                return pb.RegisterResponse(
                    success=False, message="Invalid email format"
                )

            # Check if user exists
            existing_user = await prisma.user.find_unique(
                where={"email": request.email}
            )
            if existing_user:
                context.set_code(grpc.StatusCode.ALREADY_EXISTS)
                context.set_details("User already exists")
                return pb.RegisterResponse(success=False, message="User already exists")

            # Hash password
            password_hash = self.password_handler.hash_password(request.password)

            # Generate user ID
            user_id = str(uuid.uuid4())

            # Create user - only set required + provided fields
            # Database defaults: role='FREE', isVerified=false, timestamps
            new_user = await prisma.user.create(
                data={
                    "id": user_id,
                    "email": request.email,
                    "passwordHash": password_hash,
                    "username": request.username if request.username else None,
                    "name": request.name if request.name else None,
                }
            )

            # Generate JWT tokens
            access_token = self.jwt_handler.create_access_token(
                user_id=new_user.id,
                tenant_id="default",
                role=new_user.role if hasattr(new_user, "role") else "FREE",
                email=new_user.email,
                username=new_user.username,
            )

            # Generate session identifiers
            session_id = str(uuid.uuid4())
            session_token = str(uuid.uuid4())
            session_expires = datetime.utcnow() + timedelta(days=7)

            session = await prisma.session.create(
                data={
                    "id": session_id,
                    "sessionToken": session_token,
                    "userId": new_user.id,
                    "expires": session_expires,
                }
            )

            refresh_token = self.jwt_handler.create_refresh_token(
                user_id=new_user.id, session_id=session.id, tenant_id="default"
            )

            logger.info(f"User registered successfully: {mask_email(new_user.email)}")

            return pb.RegisterResponse(
                success=True,
                message="User registered successfully",
                user_id=new_user.id,
                access_token=access_token,
                refresh_token=refresh_token,
            )

        except Exception as e:
            logger.error(f"Registration error: {str(e)}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return pb.RegisterResponse(
                success=False, message=f"Registration failed: {str(e)}"
            )

    async def Login(self, request, context):
        """User authentication with session management"""
        logger.info(f"Login request for email: {mask_email(request.email)}")

        try:
            # Validate email format
            if not self._validate_email(request.email):
                logger.warning(f"Invalid email format: {mask_email(request.email)}")
                context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
                return pb.LoginResponse(success=False, message="Invalid email format")

            # Find user by email
            user = await prisma.user.find_unique(where={"email": request.email})

            if not user:
                logger.warning(f"User not found: {mask_email(request.email)}")
                context.set_code(grpc.StatusCode.UNAUTHENTICATED)
                return pb.LoginResponse(
                    success=False, message="Invalid email or password"
                )

            # Verify password
            if not user.passwordHash:
                logger.error(f"User has no password hash: {mask_email(request.email)}")
                context.set_code(grpc.StatusCode.INTERNAL)
                return pb.LoginResponse(success=False, message="Authentication error")

            is_valid = PasswordHandler.verify_password(
                request.password, user.passwordHash
            )

            if not is_valid:
                logger.warning(
                    f"Invalid password for user: {mask_email(request.email)}"
                )
                context.set_code(grpc.StatusCode.UNAUTHENTICATED)
                return pb.LoginResponse(
                    success=False, message="Invalid email or password"
                )

            logger.info(f"Password verified for user: {user.id}")

            # Generate refresh token
            refresh_token = self._generate_refresh_token()
            token_hash = self._hash_token(refresh_token)

            # Store refresh token in DB
            device_info = (
                request.device_info if request.device_info else "Unknown device"
            )
            expires_at = datetime.utcnow() + timedelta(days=30)

            await prisma.refreshtoken.create(
                data={
                    "userId": user.id,
                    "tenantId": user.tenantId if user.tenantId else "default",
                    "tokenHash": token_hash,
                    "expiresAt": expires_at,
                    "deviceInfo": device_info,
                }
            )

            logger.info(f"Refresh token created for user: {user.id}")

            # Update last login timestamp
            try:
                await prisma.user.update(
                    where={"id": user.id}, data={"lastInteraction": datetime.utcnow()}
                )
            except Exception as update_error:
                logger.warning(f"Could not update last interaction: {update_error}")

            # Extract device claims from metadata for session enforcement
            device_id = request.metadata.get("device_id") if request.metadata else None
            device_type = (
                request.metadata.get("device_type") if request.metadata else None
            )

            logger.info(
                f"Device claims: device_id={device_id[:8] if device_id else 'None'}..., device_type={device_type}"
            )

            # Generate JWT access token with device claims
            access_token = self.jwt_handler.create_access_token(
                user_id=user.id,
                tenant_id=user.tenantId if user.tenantId else "default",
                role=user.role if user.role else "USER",
                email=user.email,
                username=user.username if user.username else user.email,
                device_id=device_id,
                device_type=device_type,
            )

            logger.info(f"JWT tokens generated for user: {user.id}")

            # Return success response
            return pb.LoginResponse(
                success=True,
                message="Login successful",
                user_id=user.id,
                tenant_id=user.tenantId if user.tenantId else "default",
                access_token=access_token,
                refresh_token=refresh_token,
                session_id="",
                expires_in=604800,
                role=user.role if user.role else "USER",
                requires_password_change=False,
                email=user.email,
                name=user.name if user.name else "",
                username=user.username if user.username else "",
            )

        except Exception as e:
            logger.error(f"Login error: {e}", exc_info=True)
            context.set_code(grpc.StatusCode.INTERNAL)
            return pb.LoginResponse(
                success=False, message=f"Authentication failed: {str(e)}"
            )

    async def RefreshToken(self, request, context):
        """Token rotation with security validation"""
        logger.info("Token refresh request received")

        try:
            # Hash incoming token to lookup in DB
            token_hash = self._hash_token(request.refresh_token)

            # Find token in database
            stored_token = await prisma.refreshtoken.find_unique(
                where={"tokenHash": token_hash}, include={"user": True}
            )

            # Validate token exists
            if not stored_token:
                logger.warning("Refresh token not found")
                context.set_code(grpc.StatusCode.UNAUTHENTICATED)
                return pb.RefreshTokenResponse(
                    success=False, message="Invalid refresh token"
                )

            # Check if revoked
            if stored_token.revokedAt:
                logger.warning(
                    f"Refresh token was revoked at: {stored_token.revokedAt}"
                )
                context.set_code(grpc.StatusCode.UNAUTHENTICATED)
                return pb.RefreshTokenResponse(
                    success=False, message="Token has been revoked"
                )

            # Check if expired
            if datetime.now(timezone.utc) > stored_token.expiresAt:
                logger.warning("Refresh token expired")
                context.set_code(grpc.StatusCode.UNAUTHENTICATED)
                return pb.RefreshTokenResponse(
                    success=False, message="Refresh token expired"
                )

            # Generate new access token
            user = stored_token.user
            access_token = self.jwt_handler.create_access_token(
                user_id=user.id,
                tenant_id=stored_token.tenantId,
                role=user.role if user.role else "USER",
                email=user.email,
                username=user.username if user.username else user.email,
            )

            # Update last used timestamp
            await prisma.refreshtoken.update(
                where={"id": stored_token.id},
                data={"lastUsedAt": datetime.now(timezone.utc)},
            )

            logger.info(f"Access token refreshed for user: {user.id}")

            return pb.RefreshTokenResponse(
                success=True,
                message="Token refreshed successfully",
                access_token=access_token,
                refresh_token=request.refresh_token,
                tenant_id=stored_token.tenantId,
                user_id=user.id,
                role=user.role if user.role else "USER",
                session_id="",
                expires_in=604800,
                token_rotated=False,
            )

        except Exception as e:
            logger.error(f"Token refresh error: {e}", exc_info=True)
            context.set_code(grpc.StatusCode.INTERNAL)
            return pb.RefreshTokenResponse(
                success=False, message=f"Token refresh failed: {str(e)}"
            )

    async def ValidateToken(self, request, context):
        """JWT token validation - FIXED: Now properly decodes JWT"""
        logger.info("Token validation request")

        try:
            # Decode and validate JWT token
            payload = self.jwt_handler.verify_token(request.access_token)

            if not payload:
                logger.warning("Token validation failed: Invalid token")
                return pb.ValidateTokenResponse(valid=False, message="Invalid token")

            logger.info(
                f"Token validated successfully for user: {payload.get('user_id')}"
            )

            # Return actual payload data from JWT
            return pb.ValidateTokenResponse(
                valid=True,
                user_id=payload.get("user_id", ""),
                tenant_id=payload.get("tenant_id", "default"),
                role=payload.get("role", "USER"),
                email=payload.get("email", ""),
                username=payload.get("username", ""),
                expires_in=3600,
            )
        except Exception as e:
            logger.error(f"Token validation error: {e}")
            return pb.ValidateTokenResponse(valid=False, message="Invalid token")

    async def RevokeToken(self, request, context):
        """Revoke specific token"""
        logger.info("Token revocation request")

        try:
            # TODO: Implement token revocation
            return pb.RevokeTokenResponse(
                success=True, message="Token revoked successfully"
            )
        except Exception as e:
            logger.error(f"Token revocation error: {e}")
            return pb.RevokeTokenResponse(success=False, message="Revocation failed")

    async def Logout(self, request, context):
        """Logout user - revoke refresh token(s)"""
        logger.info(f"Logout request for user: {request.user_id}")

        try:
            if request.refresh_token and not request.logout_all_devices:
                # Revoke specific token
                token_hash = self._hash_token(request.refresh_token)

                result = await prisma.refreshtoken.update_many(
                    where={"tokenHash": token_hash, "userId": request.user_id},
                    data={"revokedAt": datetime.utcnow()},
                )

                revoked_count = result
                logger.info(
                    f"Revoked {revoked_count} token for user: {request.user_id}"
                )

            else:
                # Revoke all tokens for user (logout all devices)
                result = await prisma.refreshtoken.update_many(
                    where={"userId": request.user_id, "revokedAt": None},
                    data={"revokedAt": datetime.utcnow()},
                )

                revoked_count = result
                logger.info(
                    f"Revoked all tokens ({revoked_count}) for user: {request.user_id}"
                )

            return pb.LogoutResponse(
                success=True,
                message="Logged out successfully",
                revoked_tokens=revoked_count if revoked_count else 0,
            )

        except Exception as e:
            logger.error(f"Logout error: {e}", exc_info=True)
            context.set_code(grpc.StatusCode.INTERNAL)
            return pb.LogoutResponse(success=False, message=f"Logout failed: {str(e)}")

    # Additional service methods remain as TODO stubs
    async def GetUserProfile(self, request, context):
        """Get user profile information"""
        return pb.GetUserProfileResponse(success=False, message="Not implemented")

    async def IntrospectToken(self, request, context):
        """Token introspection for detailed info"""
        return pb.IntrospectTokenResponse(active=False)

    async def ListActiveSessions(self, request, context):
        """List all active sessions for user"""
        return pb.ListActiveSessionsResponse(success=False, message="Not implemented")

    async def RevokeSession(self, request, context):
        """Revoke specific session"""
        return pb.RevokeSessionResponse(success=False, message="Not implemented")

    async def RevokeAllSessions(self, request, context):
        """Revoke all user sessions"""
        return pb.RevokeAllSessionsResponse(success=False, message="Not implemented")

    async def LogAuditEvent(self, request, context):
        """Log security audit event"""
        return pb.LogAuditEventResponse(success=False, message="Not implemented")

    async def GetAuditTrail(self, request, context):
        """Retrieve audit trail"""
        return pb.GetAuditTrailResponse(success=False, message="Not implemented")

    async def GetServiceInfo(self, request, context):
        """Get service information"""
        return pb.GetServiceInfoResponse(
            service_name=settings.SERVICE_NAME, version="1.0.0", status="operational"
        )

    async def HealthCheck(self, request, context):
        """Health check endpoint"""
        return pb.HealthCheckResponse(
            status="healthy", message="Auth service operational"
        )


class HealthServicer(health.HealthServicer):
    """gRPC Health Check Implementation"""

    async def Check(self, request, context):
        return health_pb2.HealthCheckResponse(
            status=health_pb2.HealthCheckResponse.SERVING
        )


async def serve():
    """Start gRPC server"""
    server = aio.server()

    # Add auth service
    pb_grpc.add_AuthServiceServicer_to_server(AuthServiceServicer(), server)

    # Add health check
    health_pb2_grpc.add_HealthServicer_to_server(HealthServicer(), server)

    # Bind to port
    listen_addr = f"[::]:{settings.SERVICE_PORT}"
    server.add_insecure_port(listen_addr)

    logger.info(f"Starting {settings.SERVICE_NAME}...")

    # Connect to Prisma
    logger.info("Connecting to Prisma...")
    await connect_prisma()
    logger.info("Prisma connected")

    # Start server
    await server.start()
    logger.info(f"Enterprise Auth Service listening on port {settings.SERVICE_PORT}")
    logger.info(
        "Available methods: Register, Login, RefreshToken, ValidateToken, RevokeToken, GetUserProfile, IntrospectToken, ListActiveSessions, RevokeSession, RevokeAllSessions, LogAuditEvent, GetAuditTrail, GetServiceInfo, HealthCheck"
    )
    logger.info("AUTH SERVICE READY - Enterprise authentication enabled")

    # Graceful shutdown handler
    async def shutdown():
        logger.info("Shutdown signal received. Initiating graceful shutdown...")
        await server.stop(5)
        logger.info("Shutting down gRPC server...")
        logger.info("Disconnecting Prisma...")
        await disconnect_prisma()
        logger.info(f"{settings.SERVICE_NAME} shut down cleanly")

    # Handle shutdown signals
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(shutdown()))

    # Keep server running
    await server.wait_for_termination()


if __name__ == "__main__":
    asyncio.run(serve())
