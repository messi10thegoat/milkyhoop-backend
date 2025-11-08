"""
Audit Logging Helper for Authentication Events
Logs all auth-related activities to audit_logs table
"""

from datetime import datetime
from typing import Optional, Dict, Any
from backend.api_gateway.libs.milkyhoop_prisma import Prisma, fields

# Global Prisma instance
prisma = Prisma()


async def log_auth_event(
    event_type: str,
    user_id: Optional[str] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    success: bool = True,
    error_message: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None
) -> None:
    """
    Log authentication event to audit_logs table
    
    Args:
        event_type: Type of event (LOGIN, LOGOUT, REGISTER, FAILED_LOGIN, etc)
        user_id: User ID if available (must be valid User.id or None)
        ip_address: Client IP address
        user_agent: Client User-Agent header
        success: Whether the operation was successful
        error_message: Error message if operation failed
        metadata: Additional metadata as JSON
    """
    try:
        # Ensure Prisma is connected
        if not prisma.is_connected():
            await prisma.connect()
        
        # Build data dict dynamically - only include non-None values
        # This prevents field constraint errors and type mismatches
        data = {
            'eventType': event_type,
            'success': success
        }
        
        # Only add optional fields if they have values
        if user_id is not None:
            data['userId'] = user_id
        
        if ip_address is not None:
            data['ipAddress'] = ip_address
        
        if user_agent is not None:
            data['userAgent'] = user_agent
        
        if error_message is not None:
            data['errorMessage'] = error_message
        
        if metadata is not None:
            data['metadata'] = fields.Json(metadata)
        
        # Create audit log entry
        await prisma.auditlog.create(data=data)
        
        print(f"✅ Audit log created: {event_type} for user {user_id or 'anonymous'}")
        
    except Exception as e:
        # Don't raise - audit logging should never break main flow
        print(f"⚠️  Audit logging failed: {e}")


# Event type constants for convenience
class AuditEventType:
    LOGIN = "LOGIN"
    LOGOUT = "LOGOUT"
    REGISTER = "REGISTER"
    FAILED_LOGIN = "FAILED_LOGIN"
    TOKEN_REFRESH = "TOKEN_REFRESH"
    TOKEN_REVOKE = "TOKEN_REVOKE"
    PASSWORD_RESET = "PASSWORD_RESET"
    PASSWORD_CHANGE = "PASSWORD_CHANGE"
    EMAIL_VERIFICATION = "EMAIL_VERIFICATION"
    TWO_FACTOR_ENABLE = "TWO_FACTOR_ENABLE"
    TWO_FACTOR_DISABLE = "TWO_FACTOR_DISABLE"