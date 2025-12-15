import grpc
import asyncio
from typing import Dict, Any, Optional
import os
import logging

from milkyhoop_protos import auth_service_pb2_grpc, auth_service_pb2 as auth_pb2

logger = logging.getLogger(__name__)

# TLS Configuration from environment
GRPC_TLS_ENABLED = os.getenv("GRPC_TLS_ENABLED", "false").lower() == "true"
GRPC_TLS_CERT_PATH = os.getenv("GRPC_TLS_CERT_PATH", "/etc/ssl/certs/grpc-ca.crt")


def _get_grpc_channel_options():
    """Get gRPC channel options for keepalive"""
    return [
        ("grpc.keepalive_time_ms", 10000),
        ("grpc.keepalive_timeout_ms", 5000),
        ("grpc.keepalive_permit_without_calls", True),
        ("grpc.http2.max_pings_without_data", 0),
    ]


def _create_channel(target: str) -> grpc.aio.Channel:
    """Create gRPC channel with TLS if enabled"""
    options = _get_grpc_channel_options()

    if GRPC_TLS_ENABLED:
        try:
            # Load TLS credentials
            with open(GRPC_TLS_CERT_PATH, "rb") as f:
                trusted_certs = f.read()
            credentials = grpc.ssl_channel_credentials(root_certificates=trusted_certs)
            logger.info(f"Creating secure gRPC channel to {target}")
            return grpc.aio.secure_channel(target, credentials, options=options)
        except FileNotFoundError:
            logger.error(
                f"TLS cert not found at {GRPC_TLS_CERT_PATH}, falling back to insecure"
            )
        except Exception as e:
            logger.error(
                f"Failed to load TLS credentials: {e}, falling back to insecure"
            )

    # Fallback to insecure channel (development only)
    if os.getenv("ENVIRONMENT", "development") == "production":
        logger.warning(
            "Using insecure gRPC channel in production - this is a security risk!"
        )
    return grpc.aio.insecure_channel(target, options=options)


class AuthClient:
    def __init__(
        self,
        host: str = "milkyhoop-dev-auth_service-1",
        port: int = 8013,
        timeout: float = 60.0,
    ):
        self.target = f"{host}:{port}"
        self.timeout = timeout
        self.channel: Optional[grpc.aio.Channel] = None
        self.stub: Optional[auth_service_pb2_grpc.AuthServiceStub] = None
        self._connect_lock = asyncio.Lock()

    async def connect(self):
        """Connect to auth service with persistent channel"""
        async with self._connect_lock:
            if self.channel is None or self.stub is None:
                self.channel = _create_channel(self.target)
                self.stub = auth_service_pb2_grpc.AuthServiceStub(self.channel)
                logger.info(f"Connected to Auth gRPC service at {self.target}")

    async def ensure_connected(self):
        """Ensure connection is established"""
        if self.channel is None or self.stub is None:
            await self.connect()

    async def close(self):
        """Close connection"""
        if self.channel:
            await self.channel.close()
            self.channel = None
            self.stub = None

    async def register_user(
        self, email: str, password: str, name: str, username: str
    ) -> Dict[str, Any]:
        """Register new user - returns ALL fields including tokens"""
        try:
            await self.ensure_connected()

            request = auth_pb2.RegisterRequest(
                email=email, password=password, name=name, username=username
            )

            response = await self.stub.Register(request)

            return {
                "success": response.success,
                "message": response.message,
                "user_id": response.user_id if response.success else None,
                "access_token": response.access_token if response.success else None,
                "refresh_token": response.refresh_token if response.success else None,
            }

        except Exception as e:
            # Log full error for debugging, but sanitize response
            logger.error(f"Registration error: {str(e)}")
            return {
                "success": False,
                "message": "Registration failed. Please try again later.",
                "user_id": None,
                "access_token": None,
                "refresh_token": None,
            }

    async def login_user(self, email: str, password: str) -> Dict[str, Any]:
        """Login user - returns tokens"""
        try:
            await self.ensure_connected()

            request = auth_pb2.LoginRequest(email=email, password=password)

            response = await self.stub.Login(request)

            return {
                "success": response.success if hasattr(response, "success") else True,
                "message": response.message
                if hasattr(response, "message")
                else "Login successful",
                "access_token": response.access_token
                if hasattr(response, "access_token")
                else None,
                "refresh_token": response.refresh_token
                if hasattr(response, "refresh_token")
                else None,
                "user_id": response.user_id if hasattr(response, "user_id") else None,
                "email": response.email if hasattr(response, "email") else None,
                "name": response.name if hasattr(response, "name") else None,
                "role": response.role if hasattr(response, "role") else None,
                "tenant_id": response.tenant_id
                if hasattr(response, "tenant_id")
                else None,
            }

        except Exception as e:
            # Log full error for debugging, but sanitize response to prevent info disclosure
            logger.error(f"Login error: {str(e)}")
            return {
                "success": False,
                "message": "Invalid credentials or service temporarily unavailable.",
                "access_token": None,
                "refresh_token": None,
                "user_id": None,
            }

    async def disconnect(self):
        """Disconnect from auth service - alias for close()"""
        await self.close()

    async def validate_token(self, token: str) -> Dict[str, Any]:
        """Validate JWT token"""
        try:
            await self.ensure_connected()

            request = auth_pb2.ValidateTokenRequest(access_token=token)
            response = await self.stub.ValidateToken(request)

            return {
                "valid": response.valid if hasattr(response, "valid") else False,
                "user_id": response.user_id if hasattr(response, "user_id") else None,
                "tenant_id": response.tenant_id
                if hasattr(response, "tenant_id")
                else None,
                "role": response.role if hasattr(response, "role") else None,
                "email": response.email if hasattr(response, "email") else None,
                "username": response.username
                if hasattr(response, "username")
                else None,
                "message": response.message if hasattr(response, "message") else None,
            }

        except Exception as e:
            # Log full error for debugging, but sanitize response
            logger.error(f"Token validation error: {str(e)}")
            return {
                "valid": False,
                "message": "Token validation failed.",
                "user_id": None,
            }

    async def get_user_profile(self, user_id: str) -> Dict[str, Any]:
        """Get user profile"""
        try:
            await self.ensure_connected()

            request = auth_pb2.UserInfoRequest(user_id=user_id)
            response = await self.stub.GetUserInfo(request)

            return {
                "success": True,
                "user_id": response.user_id if hasattr(response, "user_id") else None,
                "email": response.email if hasattr(response, "email") else None,
                "name": response.name if hasattr(response, "name") else None,
            }

        except Exception as e:
            # Log full error for debugging, but sanitize response
            logger.error(f"Profile error: {str(e)}")
            return {
                "success": False,
                "message": "Failed to retrieve profile.",
                "user_id": None,
                "email": None,
            }

    async def refresh_token(self, refresh_token: str) -> Dict[str, Any]:
        """
        Refresh access token using refresh token

        Args:
            refresh_token: Valid refresh token

        Returns:
            Dict containing new access_token and refresh_token
        """
        try:
            await self.ensure_connected()

            request = auth_pb2.RefreshTokenRequest(refresh_token=refresh_token)
            response = await self.stub.RefreshToken(request)

            if response.success:
                return {
                    "success": True,
                    "access_token": response.access_token,
                    "refresh_token": response.refresh_token,
                    "expires_in": response.expires_in
                    if hasattr(response, "expires_in")
                    else 900,
                }
            else:
                return {
                    "success": False,
                    "error": response.error_message or "Token refresh failed",
                }

        except grpc.RpcError as e:
            logger.error(f"gRPC error refreshing token: {e.code()} - {e.details()}")
            return {
                "success": False,
                "error": "Token refresh failed. Please login again.",
            }
        except Exception as e:
            logger.error(f"Error refreshing token: {str(e)}")
            return {
                "success": False,
                "error": "Token refresh failed. Please login again.",
            }

    async def list_active_sessions(self, user_id: str) -> Dict[str, Any]:
        """
        List all active sessions for a user

        Args:
            user_id: User ID to query sessions

        Returns:
            Dict containing list of active sessions
        """
        try:
            await self.ensure_connected()

            request = auth_pb2.ListActiveSessionsRequest(user_id=user_id)
            response = await self.stub.ListActiveSessions(request)

            if response.success:
                sessions = []
                for session in response.sessions:
                    sessions.append(
                        {
                            "session_id": session.session_id,
                            "device": session.device
                            if hasattr(session, "device")
                            else "Unknown",
                            "ip_address": session.ip_address
                            if hasattr(session, "ip_address")
                            else None,
                            "created_at": session.created_at
                            if hasattr(session, "created_at")
                            else None,
                            "last_active": session.last_active
                            if hasattr(session, "last_active")
                            else None,
                        }
                    )

                return {"success": True, "sessions": sessions, "total": len(sessions)}
            else:
                return {
                    "success": False,
                    "error": response.error_message or "Failed to list sessions",
                }

        except grpc.RpcError as e:
            logger.error(f"gRPC error listing sessions: {e.code()} - {e.details()}")
            return {"success": False, "error": "Failed to retrieve sessions."}
        except Exception as e:
            logger.error(f"Error listing sessions: {str(e)}")
            return {"success": False, "error": "Failed to retrieve sessions."}

    async def revoke_session(self, session_id: str, user_id: str) -> Dict[str, Any]:
        """
        Revoke a specific session

        Args:
            session_id: Session ID to revoke
            user_id: User ID for authorization

        Returns:
            Dict containing success status
        """
        try:
            await self.ensure_connected()

            request = auth_pb2.RevokeSessionRequest(
                session_id=session_id, user_id=user_id
            )
            response = await self.stub.RevokeSession(request)

            if response.success:
                return {"success": True, "message": "Session revoked successfully"}
            else:
                return {
                    "success": False,
                    "error": response.error_message or "Failed to revoke session",
                }

        except grpc.RpcError as e:
            logger.error(f"gRPC error revoking session: {e.code()} - {e.details()}")
            return {"success": False, "error": "Failed to revoke session."}
        except Exception as e:
            logger.error(f"Error revoking session: {str(e)}")
            return {"success": False, "error": "Failed to revoke session."}

    async def logout(
        self, user_id: str, refresh_token: str = None, logout_all_devices: bool = False
    ) -> Dict[str, Any]:
        """
        Logout user - revoke refresh token(s)

        Args:
            user_id: User ID
            refresh_token: Specific refresh token to revoke (optional)
            logout_all_devices: If True, revoke all tokens for user

        Returns:
            Dict containing success status and revoked token count
        """
        try:
            await self.ensure_connected()

            request = auth_pb2.LogoutRequest(
                user_id=user_id,
                refresh_token=refresh_token if refresh_token else "",
                logout_all_devices=logout_all_devices,
            )
            response = await self.stub.Logout(request)

            if response.success:
                return {
                    "success": True,
                    "message": response.message,
                    "revoked_tokens": response.revoked_tokens,
                }
            else:
                return {"success": False, "error": response.message or "Logout failed"}

        except grpc.RpcError as e:
            logger.error(f"gRPC error during logout: {e.code()} - {e.details()}")
            return {"success": False, "error": "Logout failed. Please try again."}
        except Exception as e:
            logger.error(f"Error during logout: {str(e)}")
            return {"success": False, "error": "Logout failed. Please try again."}

    async def generate_tokens_for_qr_login(
        self,
        user_id: str,
        tenant_id: str,
        email: str,
        role: str,
        username: str = None,
        device_info: str = None,
    ) -> Dict[str, Any]:
        """
        Generate tokens for QR login flow (desktop session)

        This is called when mobile user approves QR login.
        Uses the auth service's internal token generation.

        Args:
            user_id: User ID
            tenant_id: Tenant ID
            email: User email
            role: User role
            username: Username (optional)
            device_info: Device info string (optional)

        Returns:
            Dict containing access_token and refresh_token
        """
        try:
            await self.ensure_connected()

            # Use CreateTokens RPC if available, otherwise fall back to internal generation
            try:
                # Check if CreateTokensRequest exists in proto
                if not hasattr(auth_pb2, "CreateTokensRequest"):
                    logger.info(
                        "CreateTokensRequest not in proto, using local JWT generation"
                    )
                    return await self._generate_tokens_locally(
                        user_id, tenant_id, email, role, username, device_info
                    )

                request = auth_pb2.CreateTokensRequest(
                    user_id=user_id,
                    tenant_id=tenant_id,
                    email=email,
                    role=role,
                    username=username or "",
                    device_info=device_info or "QR Login - Web Session",
                )
                response = await self.stub.CreateTokens(request)

                if response.success:
                    return type(
                        "TokenResponse",
                        (),
                        {
                            "access_token": response.access_token,
                            "refresh_token": response.refresh_token,
                            "success": True,
                        },
                    )()
                else:
                    raise Exception(response.error_message or "Token generation failed")

            except (grpc.RpcError, AttributeError) as rpc_error:
                # CreateTokens method might not exist - fall back to local generation
                if isinstance(rpc_error, AttributeError) or (
                    hasattr(rpc_error, "code")
                    and rpc_error.code() == grpc.StatusCode.UNIMPLEMENTED
                ):
                    logger.warning(
                        "CreateTokens RPC not available, using local JWT generation"
                    )
                    return await self._generate_tokens_locally(
                        user_id, tenant_id, email, role, username, device_info
                    )
                raise

        except Exception as e:
            logger.error(f"Error generating QR login tokens: {str(e)}")
            # Fall back to local generation
            return await self._generate_tokens_locally(
                user_id, tenant_id, email, role, username, device_info
            )

    async def _generate_tokens_locally(
        self,
        user_id: str,
        tenant_id: str,
        email: str,
        role: str,
        username: str = None,
        device_info: str = None,
    ):
        """
        Generate JWT tokens locally as fallback

        Note: This requires JWT_SECRET to be available
        """
        import jwt
        import os
        from datetime import datetime, timedelta, timezone
        import hashlib

        jwt_secret = os.getenv("JWT_SECRET")
        if not jwt_secret:
            raise ValueError("JWT_SECRET not configured for local token generation")

        now = datetime.now(timezone.utc)

        # Access token (15 min expiry)
        access_payload = {
            "user_id": user_id,
            "tenant_id": tenant_id,
            "role": role,
            "email": email,
            "username": username or email.split("@")[0],
            "token_type": "access",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=15)).timestamp()),
            "nbf": int(now.timestamp()),
        }
        access_token = jwt.encode(access_payload, jwt_secret, algorithm="HS256")

        # Refresh token (7 days expiry)
        refresh_payload = {
            "user_id": user_id,
            "tenant_id": tenant_id,
            "token_type": "refresh",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(days=7)).timestamp()),
            "nbf": int(now.timestamp()),
        }
        refresh_token = jwt.encode(refresh_payload, jwt_secret, algorithm="HS256")

        # Store refresh token in database
        try:
            from backend.api_gateway.app.main import prisma

            token_hash = hashlib.sha256(refresh_token.encode()).hexdigest()

            await prisma.refreshtoken.create(
                data={
                    "userId": user_id,
                    "tenantId": tenant_id,
                    "tokenHash": token_hash,
                    "expiresAt": now + timedelta(days=7),
                    "deviceInfo": device_info or "QR Login - Web Session",
                }
            )
        except Exception as e:
            logger.warning(f"Failed to store refresh token: {e}")

        logger.info(f"Generated local JWT tokens for QR login: user {user_id[:8]}...")

        return type(
            "TokenResponse",
            (),
            {
                "access_token": access_token,
                "refresh_token": refresh_token,
                "success": True,
            },
        )()
