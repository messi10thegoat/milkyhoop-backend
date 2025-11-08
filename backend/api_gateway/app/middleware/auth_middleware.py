"""
Authentication Middleware with Real Token Validation
"""
import logging
import re
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from backend.api_gateway.app.services.auth_instance import auth_client

logger = logging.getLogger(__name__)

class AuthMiddleware(BaseHTTPMiddleware):
    
    def __init__(self, app):
        super().__init__(app)
        self.public_paths = {
            "/healthz", "/health", "/docs", "/openapi.json",
            "/favicon.ico", "/api/auth/register", "/api/auth/login", "/"
        }
        
    def _is_customer_chat_endpoint(self, path: str) -> bool:
        """Check if path matches /{tenant_id}/chat pattern"""
        customer_pattern = r'^/[^/]+/chat/?$'
        return bool(re.match(customer_pattern, path))
        
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        
        try:
            if path in self.public_paths:
                return await call_next(request)
            
            if self._is_customer_chat_endpoint(path) and request.method == "POST":
                logger.info(f"Bypassing auth for customer endpoint: {path}")
                return await call_next(request)
            
            auth_header = request.headers.get("Authorization")
            if not auth_header or not auth_header.startswith("Bearer "):
                return JSONResponse(status_code=401, content={
                    "error": "Authentication required", "code": "MISSING_TOKEN"
                })
            
            token = auth_header.replace("Bearer ", "")
            
            if not token or token.strip() == "":
                return JSONResponse(status_code=401, content={
                    "error": "Invalid token", "code": "EMPTY_TOKEN"
                })
            
            try:
                validation_result = await auth_client.validate_token(token)
                
                if not validation_result.get("valid"):
                    return JSONResponse(status_code=401, content={
                        "error": "Invalid token", "code": "INVALID_TOKEN"
                    })
                
                request.state.user = {
                    "user_id": validation_result.get("user_id"),
                    "tenant_id": validation_result.get("tenant_id", "default"),
                    "role": validation_result.get("role", "USER"),
                    "email": validation_result.get("email"),
                    "username": validation_result.get("username")
                }
                
            except Exception as e:
                logger.error(f"Token validation error: {str(e)}")
                return JSONResponse(status_code=401, content={
                    "error": "Authentication failed", "code": "VALIDATION_ERROR"
                })
            
            return await call_next(request)
            
        except Exception as e:
            logger.error(f"Auth middleware error: {str(e)}")
            return JSONResponse(status_code=500, content={"error": "Authentication error"})
