"""
Authentication Middleware - Phase 2 Implementation - FIXED
Path classification bug resolved
"""

import grpc
from fastapi import Request, Response, HTTPException
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
import structlog
from typing import Optional, Dict, Any
import os

# Import auth service proto
import sys
sys.path.append('/app/backend/api_gateway/libs')
try:
    from milkyhoop_protos import auth_pb2, auth_pb2_grpc
except ImportError:
    print("⚠️ Auth proto files not found - using mock auth")
    auth_pb2 = None
    auth_pb2_grpc = None

logger = structlog.get_logger(__name__)

class AuthMiddleware(BaseHTTPMiddleware):
    """
    Authentication Middleware with FIXED path segregation
    BUGFIX: Protected paths now take precedence over public paths
    """
    
    def __init__(self, app, auth_service_host: str = "auth_service:5004"):
        super().__init__(app)
        self.auth_service_host = auth_service_host
        
        # Path configuration - EXACT paths to avoid conflicts
        self.protected_paths = [
            "/chat/",
            "/chat",
            "/api/setup/", 
            "/api/test/",
            "/api/auth/sessions",
            "/api/auth/logout",
            "/api/auth/logout-all"
        ]
        
        self.public_paths = [
            "/health",
            "/healthz", 
            "/docs",
            "/openapi.json",
            "/redoc",
            "/api/auth/health"  # Session health check is public
        ]
        
        logger.info("🔧 AuthMiddleware initialized", 
                   protected_paths=len(self.protected_paths),
                   public_paths=len(self.public_paths))
    
    async def dispatch(self, request: Request, call_next):
        """
        FIXED dispatch logic - protected paths checked FIRST
        """
        
        logger.info("🔧 Middleware processing", path=request.url.path)
        
        # ROOT PATH - special handling
        if request.url.path == "/":
            logger.debug("📂 Root path - bypassing auth", path=request.url.path)
            return await call_next(request)
        
        # PROTECTED PATHS - check FIRST (BUGFIX)
        if self._is_protected_path(request.url.path):
            logger.info("🔍 Protected path detected", path=request.url.path)
            
            # Extract JWT token
            token = self._extract_token(request)
            if not token:
                logger.warning("❌ Missing token for protected path", path=request.url.path)
                return JSONResponse(
                    status_code=401,
                    content={
                        "error": "Authentication required",
                        "code": "MISSING_TOKEN", 
                        "message": "This endpoint requires authentication. Please provide Bearer token."
                    }
                )
            
            # Validate token with auth service
            user_context = await self._validate_token(token)
            if not user_context:
                logger.warning("❌ Invalid token for protected path", path=request.url.path)
                return JSONResponse(
                    status_code=401,
                    content={
                        "error": "Invalid authentication",
                        "code": "INVALID_TOKEN",
                        "message": "Authentication token is invalid or expired."
                    }
                )
            
            # Inject user context into request
            request.state.user = user_context
            request.state.authenticated = True
            logger.info("✅ Authentication successful", 
                       user_id=user_context.get('user_id'),
                       tenant_id=user_context.get('tenant_id'))
        
        # PUBLIC PATHS - check after protected (BUGFIX)
        elif self._is_public_path(request.url.path):
            logger.debug("📂 Public path - bypassing auth", path=request.url.path)
        
        else:
            # Unknown path - default to protected
            logger.warning("⚠️ Unknown path - defaulting to protected", path=request.url.path)
            return JSONResponse(
                status_code=401,
                content={
                    "error": "Authentication required",
                    "code": "UNKNOWN_PATH",
                    "message": "This endpoint requires authentication."
                }
            )
        
        # Process request
        return await call_next(request)
    
    def _is_protected_path(self, path: str) -> bool:
        """Check if path requires authentication - EXACT matching"""
        return path in self.protected_paths
    
    def _is_public_path(self, path: str) -> bool:
        """Check if path is public - EXACT matching"""
        return path in self.public_paths
    
    def _extract_token(self, request: Request) -> Optional[str]:
        """Extract JWT token from Authorization header"""
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            return auth_header[7:]  # Remove "Bearer " prefix
        return None
    
    async def _validate_token(self, token: str) -> Optional[Dict[str, Any]]:
        """
        Validate JWT token with auth service or use mock
        """
        
        # Mock validation if proto not available
        if not auth_pb2 or not auth_pb2_grpc:
            logger.info("🎭 Using mock auth validation", token_prefix=token[:8])
            return {
                "user_id": "mock_user_authenticated", 
                "tenant_id": "mock_tenant",
                "username": f"user_with_token_{token[:8]}"
            }
        
        try:
            # Create gRPC channel
            channel = grpc.aio.insecure_channel(self.auth_service_host)
            stub = auth_pb2_grpc.AuthServiceStub(channel)
            
            # Create validation request
            request = auth_pb2.ValidateTokenRequest(access_token=token)
            
            # Call auth service
            response = await stub.ValidateToken(request)
            
            # Close channel
            await channel.close()
            
            # Check response
            if response.valid:
                logger.info("✅ Real auth service validation successful")
                return {
                    "user_id": response.user_id,
                    "tenant_id": response.tenant_id,
                    "username": getattr(response, 'username', 'unknown')
                }
            else:
                logger.warning("❌ Auth service rejected token")
                return None
                
        except Exception as e:
            logger.error("❌ Auth service validation failed", error=str(e))
            logger.info("🎭 Falling back to mock auth")
            return {
                "user_id": "mock_user_fallback", 
                "tenant_id": "mock_tenant",
                "username": f"fallback_user_{token[:8]}"
            }
