import grpc
import logging
from typing import Optional, Dict, Any
from fastapi import Request, HTTPException, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

# Import from container-accessible path
import sys
import os
sys.path.append('/app')
sys.path.append('/app/backend/api_gateway')
sys.path.append('/app/backend/api_gateway/libs/milkyhoop_protos')
sys.path.append('/app/backend/api_gateway/app')

# Proto imports
try:
    from backend.api_gateway.libs.milkyhoop_protos import auth_service_pb2
    from backend.api_gateway.libs.milkyhoop_protos import auth_service_pb2_grpc
except ImportError:
    import auth_service_pb2
    import auth_service_pb2_grpc

# Session manager import
from services.session_manager import SessionManager

logger = logging.getLogger(__name__)

class AuthMiddleware(BaseHTTPMiddleware):
    """Authentication middleware with Redis session management"""
    
    def __init__(self, app, auth_service_host: str = "auth_service:5004"):
        super().__init__(app)
        self.auth_service_host = auth_service_host
        self.session_manager = SessionManager()
        
        # Protected paths (Setup Mode)
        self.protected_paths = ["/chat/", "/api/setup/", "/api/test/", "/api/auth/"]
        # Public paths (Customer Mode + utilities) 
        self.public_paths = ["/health", "/healthz", "/docs", "/openapi.json", "/redoc", "/api/tenant/"]
        
    async def dispatch(self, request: Request, call_next):
        print(f"🔧 Middleware processing: {request.url.path}")
        
        # ROOT PATH - always public
        if request.url.path == "/":
            return await call_next(request)
            
        # PROTECTED PATHS - authentication required
        if self._is_protected_path(request.url.path):
            print(f"🔍 Protected path detected: {request.url.path}")
            
            if request.method == "OPTIONS":
                return await call_next(request)
                
            try:
                token = self._extract_token(request)
                if not token:
                    return JSONResponse(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        content={
                            "error": "Authentication required", 
                            "code": "MISSING_TOKEN",
                            "message": "This endpoint requires authentication. Please provide Bearer token."
                        }
                    )
                
                # CHECK SESSION FIRST (Redis cache)
                user_context = self.session_manager.get_session(token)
                
                if user_context:
                    # Session exists - extend it
                    self.session_manager.extend_session(token, 3600)
                    print(f"✅ Session found (cached): {user_context.get('user_id')}")
                else:
                    # No session - validate with auth service
                    user_context = await self._validate_token_with_auth_service(token)
                    if not user_context:
                        return JSONResponse(
                            status_code=status.HTTP_401_UNAUTHORIZED,
                            content={
                                "error": "Invalid token", 
                                "code": "INVALID_TOKEN",
                                "message": "Token validation failed"
                            }
                        )
                    
                    # Store new session
                    expires_in = user_context.get('expires_in', 3600)
                    self.session_manager.store_session(token, user_context, expires_in)
                    print(f"✅ New session created: {user_context.get('user_id')}")
                
                # Inject user context
                request.state.user = user_context
                request.state.authenticated = True
                print(f"✅ Authenticated: {user_context.get('user_id')} (tenant: {user_context.get('tenant_id')})")
                
            except Exception as e:
                logger.error(f"Auth middleware error: {e}")
                return JSONResponse(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    content={"error": "Authentication service error"}
                )
        else:
            print(f"🔍 Public path - allowing: {request.url.path}")
            
        return await call_next(request)
        
    def _is_protected_path(self, path: str) -> bool:
        return any(path.startswith(protected) for protected in self.protected_paths)
        
    def _extract_token(self, request: Request) -> Optional[str]:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            return auth_header[7:]
        return None
        
    async def _validate_token_with_auth_service(self, token: str) -> Optional[Dict[str, Any]]:
        """Validate token with auth service via gRPC"""
        try:
            channel = grpc.aio.insecure_channel(self.auth_service_host)
            stub = auth_service_pb2_grpc.AuthServiceStub(channel)
            
            request_msg = auth_service_pb2.ValidateTokenRequest(access_token=token)
            response = await stub.ValidateToken(request_msg)
            
            await channel.close()
            
            if hasattr(response, 'valid') and response.valid:
                print(f"✅ Auth service validation success: {response.user_id}")
                return {
                    "user_id": response.user_id,
                    "tenant_id": response.tenant_id,
                    "role": response.role,
                    "session_id": getattr(response, 'session_id', None),
                    "expires_in": getattr(response, 'expires_in', 3600),
                    "permissions": list(getattr(response, 'permissions', []))
                }
            return None
                
        except Exception as e:
            logger.error(f"Token validation failed: {e}")
            return None

print("🔧 AuthMiddleware with Session Management loaded successfully")
