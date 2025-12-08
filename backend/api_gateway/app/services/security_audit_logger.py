"""
Security Audit Logger
Logs security-relevant events for compliance and forensics
"""
import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from enum import Enum


class AuditEventType(str, Enum):
    """Types of security audit events"""
    # Authentication events
    LOGIN_SUCCESS = "AUTH_LOGIN_SUCCESS"
    LOGIN_FAILED = "AUTH_LOGIN_FAILED"
    LOGOUT = "AUTH_LOGOUT"
    REGISTER = "AUTH_REGISTER"
    PASSWORD_CHANGE = "AUTH_PASSWORD_CHANGE"
    PASSWORD_RESET_REQUEST = "AUTH_PASSWORD_RESET_REQUEST"
    PASSWORD_RESET_COMPLETE = "AUTH_PASSWORD_RESET_COMPLETE"
    TOKEN_REFRESH = "AUTH_TOKEN_REFRESH"
    TOKEN_REVOKE = "AUTH_TOKEN_REVOKE"

    # Authorization events
    ACCESS_DENIED = "AUTHZ_ACCESS_DENIED"
    ROLE_CHANGE = "AUTHZ_ROLE_CHANGE"
    PERMISSION_CHANGE = "AUTHZ_PERMISSION_CHANGE"

    # Security events
    RATE_LIMIT_EXCEEDED = "SEC_RATE_LIMIT"
    ACCOUNT_LOCKED = "SEC_ACCOUNT_LOCKED"
    ACCOUNT_UNLOCKED = "SEC_ACCOUNT_UNLOCKED"
    SUSPICIOUS_ACTIVITY = "SEC_SUSPICIOUS"
    SQL_INJECTION_ATTEMPT = "SEC_SQL_INJECTION"
    XSS_ATTEMPT = "SEC_XSS"
    CSRF_ATTEMPT = "SEC_CSRF"

    # Data events
    DATA_ACCESS = "DATA_ACCESS"
    DATA_EXPORT = "DATA_EXPORT"
    DATA_DELETE = "DATA_DELETE"
    DATA_MODIFY = "DATA_MODIFY"

    # System events
    CONFIG_CHANGE = "SYS_CONFIG_CHANGE"
    SERVICE_START = "SYS_SERVICE_START"
    SERVICE_STOP = "SYS_SERVICE_STOP"
    ERROR = "SYS_ERROR"


class SecurityAuditLogger:
    """
    Centralized security audit logging.

    Features:
    - Structured JSON logging
    - Severity levels
    - Compliance-ready format (SOC2, PCI-DSS)
    - Tamper-evident logging (hash chain)
    """

    def __init__(self, service_name: str = "api_gateway"):
        self.service_name = service_name
        self.logger = logging.getLogger(f"security_audit.{service_name}")

        # Set up structured logging handler
        self._setup_logger()

        # For hash chain (tamper evidence)
        self._last_hash: Optional[str] = None

    def _setup_logger(self):
        """Configure the audit logger"""
        # Create logs directory if needed
        log_dir = os.getenv("AUDIT_LOG_DIR", "/var/log/milkyhoop/audit")
        os.makedirs(log_dir, exist_ok=True)

        # File handler for audit logs
        handler = logging.FileHandler(
            os.path.join(log_dir, f"{self.service_name}_audit.log")
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
        self.logger.addHandler(handler)
        self.logger.setLevel(logging.INFO)

    def _create_audit_record(
        self,
        event_type: AuditEventType,
        severity: str,
        user_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        ip_address: Optional[str] = None,
        request_id: Optional[str] = None,
        resource: Optional[str] = None,
        action: Optional[str] = None,
        outcome: str = "success",
        details: Optional[Dict[str, Any]] = None,
        error_message: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a structured audit record"""
        import hashlib

        timestamp = datetime.now(timezone.utc).isoformat()

        record = {
            "timestamp": timestamp,
            "service": self.service_name,
            "event_type": event_type.value,
            "severity": severity,
            "user_id": user_id,
            "tenant_id": tenant_id,
            "ip_address": ip_address,
            "request_id": request_id,
            "resource": resource,
            "action": action,
            "outcome": outcome,
            "details": details or {},
            "error_message": error_message,
        }

        # Add hash chain for tamper evidence
        record_str = json.dumps(record, sort_keys=True)
        if self._last_hash:
            record_str = f"{self._last_hash}:{record_str}"
        record["hash"] = hashlib.sha256(record_str.encode()).hexdigest()[:16]
        self._last_hash = record["hash"]

        return record

    def log(
        self,
        event_type: AuditEventType,
        severity: str = "INFO",
        **kwargs
    ):
        """Log a security audit event"""
        record = self._create_audit_record(event_type, severity, **kwargs)
        self.logger.info(json.dumps(record))
        return record

    # Convenience methods for common events
    def log_login_success(
        self,
        user_id: str,
        ip_address: str,
        request_id: Optional[str] = None,
        **kwargs
    ):
        """Log successful login"""
        return self.log(
            AuditEventType.LOGIN_SUCCESS,
            severity="INFO",
            user_id=user_id,
            ip_address=ip_address,
            request_id=request_id,
            action="login",
            outcome="success",
            **kwargs
        )

    def log_login_failed(
        self,
        ip_address: str,
        reason: str,
        request_id: Optional[str] = None,
        user_id: Optional[str] = None,
        **kwargs
    ):
        """Log failed login attempt"""
        return self.log(
            AuditEventType.LOGIN_FAILED,
            severity="WARNING",
            user_id=user_id,
            ip_address=ip_address,
            request_id=request_id,
            action="login",
            outcome="failure",
            error_message=reason,
            **kwargs
        )

    def log_access_denied(
        self,
        user_id: str,
        resource: str,
        required_role: str,
        user_role: str,
        ip_address: str,
        request_id: Optional[str] = None,
        **kwargs
    ):
        """Log access denied event"""
        return self.log(
            AuditEventType.ACCESS_DENIED,
            severity="WARNING",
            user_id=user_id,
            ip_address=ip_address,
            request_id=request_id,
            resource=resource,
            action="access",
            outcome="denied",
            details={
                "required_role": required_role,
                "user_role": user_role,
            },
            **kwargs
        )

    def log_rate_limit(
        self,
        ip_address: str,
        endpoint: str,
        request_id: Optional[str] = None,
        **kwargs
    ):
        """Log rate limit exceeded"""
        return self.log(
            AuditEventType.RATE_LIMIT_EXCEEDED,
            severity="WARNING",
            ip_address=ip_address,
            request_id=request_id,
            resource=endpoint,
            action="rate_limit",
            outcome="blocked",
            **kwargs
        )

    def log_account_locked(
        self,
        ip_address: str,
        duration_minutes: int,
        attempt_count: int,
        request_id: Optional[str] = None,
        **kwargs
    ):
        """Log account lockout"""
        return self.log(
            AuditEventType.ACCOUNT_LOCKED,
            severity="WARNING",
            ip_address=ip_address,
            request_id=request_id,
            action="lockout",
            outcome="locked",
            details={
                "duration_minutes": duration_minutes,
                "attempt_count": attempt_count,
            },
            **kwargs
        )

    def log_suspicious_activity(
        self,
        ip_address: str,
        activity_type: str,
        details: Dict[str, Any],
        request_id: Optional[str] = None,
        user_id: Optional[str] = None,
        **kwargs
    ):
        """Log suspicious activity detection"""
        return self.log(
            AuditEventType.SUSPICIOUS_ACTIVITY,
            severity="CRITICAL",
            user_id=user_id,
            ip_address=ip_address,
            request_id=request_id,
            action=activity_type,
            outcome="detected",
            details=details,
            **kwargs
        )

    def log_data_access(
        self,
        user_id: str,
        tenant_id: str,
        resource: str,
        action: str,
        ip_address: str,
        request_id: Optional[str] = None,
        record_count: Optional[int] = None,
        **kwargs
    ):
        """Log data access for compliance"""
        return self.log(
            AuditEventType.DATA_ACCESS,
            severity="INFO",
            user_id=user_id,
            tenant_id=tenant_id,
            ip_address=ip_address,
            request_id=request_id,
            resource=resource,
            action=action,
            outcome="success",
            details={"record_count": record_count} if record_count else {},
            **kwargs
        )


# Singleton instance
audit_logger = SecurityAuditLogger()
