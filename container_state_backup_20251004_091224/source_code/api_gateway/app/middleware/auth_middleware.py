"""
Authentication Middleware with Customer Endpoint Bypass
Bypasses authentication for public customer chat endpoints
"""
import logging
from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)

class AuthMiddleware(BaseHTTPMiddleware):
    """
    Authentication middleware with customer endpoint bypass
    """
    
    def __init__(self, app):
        super().__init__(app)
        self.public_paths = {
            "/healthz",
            "/health", 
            "/docs",
            "/openapi.json",
            "/favicon.ico"
        }
        # Customer endpoints pattern - bypass auth for public customer access
        self.customer_chat_pattern = "/chat"  # Matches /{tenant_id}/chat
        
    async def dispatch(self, request: Request, call_next):
        """
        Process request with authentication bypass for customer endpoints
        """
        path = request.url.path
        
        try:
            # Skip auth for public paths
            if path in self.public_paths:
                return await call_next(request)
            
            # Skip auth for customer chat endpoints (public access)
            if self.customer_chat_pattern in path and request.method == "POST":
                logger.info(f"Bypassing auth for customer endpoint: {path}")
                return await call_next(request)
            
            # Apply auth for all other endpoints
            auth_header = request.headers.get("Authorization")
            if not auth_header or not auth_header.startswith("Bearer "):
                logger.warning(f"Authentication required for: {path}")
                return JSONResponse(
                    status_code=401,
                    content={
                        "error": "Authentication required",
                        "code": "MISSING_TOKEN", 
                        "message": "This endpoint requires authentication."
                    }
                )
            
            # Token validation would go here for protected endpoints
            # For now, accept any Bearer token for protected endpoints
            return await call_next(request)
            
        except Exception as e:
            logger.error(f"Auth middleware error: {str(e)}")
            return JSONResponse(
                status_code=500,
                content={
                    "error": "Authentication error",
                    "message": "Internal authentication error"
                }
            )
