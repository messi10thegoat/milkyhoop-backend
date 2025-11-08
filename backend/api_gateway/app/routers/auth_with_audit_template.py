"""
REFERENCE TEMPLATE - How to add audit logging to auth.py

This shows the pattern for adding audit logs to each endpoint.
DO NOT replace your auth.py with this - use as reference only!
"""

# Add this import at the top
from backend.api_gateway.app.services.audit_logger import log_auth_event, AuditEventType

# Example 1: Login endpoint with audit logging
@router.post("/login")
async def login(data: LoginRequest, request: Request):
    try:
        # Existing login logic...
        response = await auth_client.login(data.email, data.password)
        
        # ✅ ADD THIS: Log successful login
        await log_auth_event(
            event_type=AuditEventType.LOGIN,
            user_id=response.user_id,
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            success=True,
            metadata={"email": data.email}
        )
        
        return response
        
    except Exception as e:
        # ✅ ADD THIS: Log failed login
        await log_auth_event(
            event_type=AuditEventType.FAILED_LOGIN,
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            success=False,
            error_message=str(e),
            metadata={"email": data.email}
        )
        raise


# Example 2: Register endpoint with audit logging
@router.post("/register")
async def register(data: RegisterRequest, request: Request):
    try:
        # Existing register logic...
        response = await auth_client.register(data)
        
        # ✅ ADD THIS: Log successful registration
        await log_auth_event(
            event_type=AuditEventType.REGISTER,
            user_id=response.user_id,
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            success=True,
            metadata={"email": data.email}
        )
        
        return response
        
    except Exception as e:
        # Log failed registration if needed
        raise


# Example 3: Token refresh with audit logging
@router.post("/refresh")
async def refresh_token(data: RefreshTokenRequest, request: Request):
    try:
        # Existing refresh logic...
        result = await auth_client.refresh_token(data.refresh_token)
        
        # ✅ ADD THIS: Log token refresh
        await log_auth_event(
            event_type=AuditEventType.TOKEN_REFRESH,
            user_id=result.get("user_id"),
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            success=True
        )
        
        return result
        
    except Exception as e:
        raise
