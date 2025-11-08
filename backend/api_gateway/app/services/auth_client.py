import grpc
from grpc import aio
import asyncio
from typing import Dict, List, Any, Optional
import os
import logging

from milkyhoop_protos import auth_service_pb2_grpc, auth_service_pb2 as auth_pb2

logger = logging.getLogger(__name__)

class AuthClient:
    def __init__(self, host: str = "milkyhoop-dev-auth_service-1", port: int = 8013, timeout: float = 60.0):
        self.target = f"{host}:{port}"
        self.timeout = timeout
        self.channel: Optional[grpc.aio.Channel] = None
        self.stub: Optional[auth_service_pb2_grpc.AuthServiceStub] = None
        self._connect_lock = asyncio.Lock()

    async def connect(self):
        """Connect to auth service with persistent channel"""
        async with self._connect_lock:
            if self.channel is None or self.stub is None:
                self.channel = grpc.aio.insecure_channel(
                    self.target,
                    options=[
                        ('grpc.keepalive_time_ms', 10000),
                        ('grpc.keepalive_timeout_ms', 5000),
                        ('grpc.keepalive_permit_without_calls', True),
                        ('grpc.http2.max_pings_without_data', 0),
                    ]
                )
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

    async def register_user(self, email: str, password: str, name: str, username: str) -> Dict[str, Any]:
        """Register new user - returns ALL fields including tokens"""
        try:
            await self.ensure_connected()
            
            request = auth_pb2.RegisterRequest(
                email=email,
                password=password,
                name=name,
                username=username
            )
            
            response = await self.stub.Register(request)
            
            return {
                "success": response.success,
                "message": response.message,
                "user_id": response.user_id if response.success else None,
                "access_token": response.access_token if response.success else None,
                "refresh_token": response.refresh_token if response.success else None
            }
            
        except Exception as e:
            logger.error(f"Registration error: {str(e)}")
            return {
                "success": False, 
                "message": f"Registration error: {str(e)}",
                "user_id": None,
                "access_token": None,
                "refresh_token": None
            }

    async def login_user(self, email: str, password: str) -> Dict[str, Any]:
        """Login user - returns tokens"""
        try:
            await self.ensure_connected()
            
            request = auth_pb2.LoginRequest(
                email=email,
                password=password
            )
            
            response = await self.stub.Login(request)
            
            return {
                "success": response.success if hasattr(response, 'success') else True,
                "message": response.message if hasattr(response, 'message') else "Login successful",
                "access_token": response.access_token if hasattr(response, 'access_token') else None,
                "refresh_token": response.refresh_token if hasattr(response, 'refresh_token') else None,
                "user_id": response.user_id if hasattr(response, 'user_id') else None,
                "email": response.email if hasattr(response, 'email') else None,
                "name": response.name if hasattr(response, 'name') else None,
                "role": response.role if hasattr(response, 'role') else None,
                "tenant_id": response.tenant_id if hasattr(response, 'tenant_id') else None
            }
            
        except Exception as e:
            logger.error(f"Login error: {str(e)}")
            return {
                "success": False, 
                "message": f"Login error: {str(e)}",
                "access_token": None,
                "refresh_token": None,
                "user_id": None
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
                "valid": response.valid if hasattr(response, 'valid') else False,
                "user_id": response.user_id if hasattr(response, 'user_id') else None,
                "tenant_id": response.tenant_id if hasattr(response, 'tenant_id') else None,
                "role": response.role if hasattr(response, 'role') else None,
                "email": response.email if hasattr(response, 'email') else None,
                "username": response.username if hasattr(response, 'username') else None,
                "message": response.message if hasattr(response, 'message') else None
            }
            
        except Exception as e:
            logger.error(f"Token validation error: {str(e)}")
            return {
                "valid": False, 
                "message": f"Validation error: {str(e)}",
                "user_id": None
            }

    async def get_user_profile(self, user_id: str) -> Dict[str, Any]:
        """Get user profile"""
        try:
            await self.ensure_connected()
            
            request = auth_pb2.UserInfoRequest(user_id=user_id)
            response = await self.stub.GetUserInfo(request)
            
            return {
                "success": True,
                "user_id": response.user_id if hasattr(response, 'user_id') else None,
                "email": response.email if hasattr(response, 'email') else None,
                "name": response.name if hasattr(response, 'name') else None
            }
            
        except Exception as e:
            logger.error(f"Profile error: {str(e)}")
            return {
                "success": False, 
                "message": f"Profile error: {str(e)}",
                "user_id": None,
                "email": None
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
                    "expires_at": response.expires_at
                }
            else:
                return {
                    "success": False,
                    "error": response.error_message or "Token refresh failed"
                }
                
        except grpc.RpcError as e:
            logger.error(f"gRPC error refreshing token: {e.code()} - {e.details()}")
            return {
                "success": False,
                "error": f"Failed to refresh token: {e.details()}"
            }
        except Exception as e:
            logger.error(f"Error refreshing token: {str(e)}")
            return {
                "success": False,
                "error": f"Failed to refresh token: {str(e)}"
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
                    sessions.append({
                        "session_id": session.session_id,
                        "device": session.device if hasattr(session, 'device') else "Unknown",
                        "ip_address": session.ip_address if hasattr(session, 'ip_address') else None,
                        "created_at": session.created_at if hasattr(session, 'created_at') else None,
                        "last_active": session.last_active if hasattr(session, 'last_active') else None
                    })
                
                return {
                    "success": True,
                    "sessions": sessions,
                    "total": len(sessions)
                }
            else:
                return {
                    "success": False,
                    "error": response.error_message or "Failed to list sessions"
                }
                
        except grpc.RpcError as e:
            logger.error(f"gRPC error listing sessions: {e.code()} - {e.details()}")
            return {
                "success": False,
                "error": f"Failed to list sessions: {e.details()}"
            }
        except Exception as e:
            logger.error(f"Error listing sessions: {str(e)}")
            return {
                "success": False,
                "error": f"Failed to list sessions: {str(e)}"
            }

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
                session_id=session_id,
                user_id=user_id
            )
            response = await self.stub.RevokeSession(request)
            
            if response.success:
                return {
                    "success": True,
                    "message": "Session revoked successfully"
                }
            else:
                return {
                    "success": False,
                    "error": response.error_message or "Failed to revoke session"
                }
                
        except grpc.RpcError as e:
            logger.error(f"gRPC error revoking session: {e.code()} - {e.details()}")
            return {
                "success": False,
                "error": f"Failed to revoke session: {e.details()}"
            }
        except Exception as e:
            logger.error(f"Error revoking session: {str(e)}")
            return {
                "success": False,
                "error": f"Failed to revoke session: {str(e)}"
            }
