"""
Pydantic schemas for Audit Trail module.

Audit Trail provides comprehensive logging for all system activities:
- Data changes (create, update, delete)
- User activity tracking
- Login history
- Sensitive data access
- Full-text search capability

NO JOURNAL ENTRIES - This is a logging/compliance system.
"""

from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Dict, Any, Literal
from datetime import date, datetime
from uuid import UUID


# =============================================================================
# REQUEST MODELS - Audit
# =============================================================================

class LogSensitiveAccessRequest(BaseModel):
    """Request to log sensitive data access."""
    data_type: str = Field(..., min_length=1, max_length=50, description="Type of sensitive data accessed")
    entity_type: Optional[str] = Field(None, max_length=100)
    entity_id: Optional[UUID] = None
    reason: Optional[str] = Field(None, max_length=500, description="Reason for accessing the data")
    authorized_by: Optional[UUID] = None
    was_exported: bool = Field(False)
    export_format: Optional[str] = Field(None, max_length=20)


class UpdateRetentionPolicyRequest(BaseModel):
    """Request to update audit retention policy."""
    retention_days: Optional[int] = Field(None, ge=30, le=3650, description="Days to retain logs")
    archive_after_days: Optional[int] = Field(None, ge=30, le=3650)
    delete_after_days: Optional[int] = Field(None, ge=30, le=3650)
    is_active: Optional[bool] = None


class AuditLogQueryParams(BaseModel):
    """Query parameters for audit log search."""
    entity_type: Optional[str] = None
    entity_id: Optional[UUID] = None
    action: Optional[Literal["create", "read", "update", "delete", "login", "logout", "export"]] = None
    user_id: Optional[UUID] = None
    category: Optional[str] = None
    severity: Optional[Literal["info", "warning", "error", "critical"]] = None
    from_date: Optional[date] = None
    to_date: Optional[date] = None
    search: Optional[str] = Field(None, description="Full-text search query")
    skip: int = Field(0, ge=0)
    limit: int = Field(50, ge=1, le=200)


# =============================================================================
# RESPONSE MODELS - Audit Log
# =============================================================================

class AuditLogItem(BaseModel):
    """Single audit log entry."""
    id: str
    event_time: datetime
    user_id: Optional[str] = None
    user_email: Optional[str] = None
    user_name: Optional[str] = None
    ip_address: Optional[str] = None
    action: Optional[str] = None
    entity_type: Optional[str] = None
    entity_id: Optional[str] = None
    entity_number: Optional[str] = None
    description: Optional[str] = None
    changed_fields: Optional[List[str]] = None
    category: Optional[str] = None
    severity: str = "info"


class AuditLogDetail(AuditLogItem):
    """Detailed audit log entry with change data."""
    old_values: Optional[Dict[str, Any]] = None
    new_values: Optional[Dict[str, Any]] = None
    request_id: Optional[str] = None
    request_path: Optional[str] = None
    request_method: Optional[str] = None
    user_agent: Optional[str] = None


class EntityHistoryItem(BaseModel):
    """History entry for a specific entity."""
    event_time: datetime
    action: str
    user_email: Optional[str] = None
    description: Optional[str] = None
    changed_fields: Optional[List[str]] = None
    changes: Optional[Dict[str, Dict[str, Any]]] = None  # {"field": {"old": x, "new": y}}


class EntityHistory(BaseModel):
    """Complete history for an entity."""
    entity_type: str
    entity_id: str
    entity_number: Optional[str] = None
    history: List[EntityHistoryItem]
    total_changes: int


# =============================================================================
# RESPONSE MODELS - Login History
# =============================================================================

class LoginHistoryItem(BaseModel):
    """Single login history entry."""
    id: str
    user_id: str
    user_email: Optional[str] = None
    login_time: datetime
    logout_time: Optional[datetime] = None
    ip_address: Optional[str] = None
    device_type: Optional[str] = None
    location_country: Optional[str] = None
    location_city: Optional[str] = None
    login_status: str
    failure_reason: Optional[str] = None
    is_suspicious: bool = False
    mfa_used: bool = False


class FailedLoginSummary(BaseModel):
    """Summary of failed login attempts."""
    user_email: str
    failed_count: int
    last_attempt: datetime
    reasons: List[str]


# =============================================================================
# RESPONSE MODELS - Sensitive Access
# =============================================================================

class SensitiveAccessItem(BaseModel):
    """Sensitive data access log entry."""
    id: str
    access_time: datetime
    user_id: str
    data_type: str
    entity_type: Optional[str] = None
    entity_id: Optional[str] = None
    reason: Optional[str] = None
    authorized_by: Optional[str] = None
    was_exported: bool = False
    export_format: Optional[str] = None


# =============================================================================
# RESPONSE MODELS - Retention Policy
# =============================================================================

class RetentionPolicyItem(BaseModel):
    """Audit retention policy configuration."""
    id: str
    category: str
    retention_days: int
    archive_after_days: Optional[int] = None
    delete_after_days: Optional[int] = None
    is_active: bool


# =============================================================================
# RESPONSE MODELS - Reports
# =============================================================================

class AuditSummaryItem(BaseModel):
    """Summary of audit activity by action and entity."""
    action: str
    entity_type: str
    count: int


class UserActivitySummary(BaseModel):
    """Summary of user activity."""
    user_id: str
    user_email: Optional[str] = None
    total_actions: int
    actions_by_type: Dict[str, int]
    last_activity: datetime


class ChangesReportItem(BaseModel):
    """Data changes report entry."""
    date: date
    entity_type: str
    creates: int
    updates: int
    deletes: int


# =============================================================================
# GENERIC RESPONSE MODELS
# =============================================================================

class AuditLogListResponse(BaseModel):
    """Response for listing audit logs."""
    items: List[AuditLogItem]
    total: int
    has_more: bool = False


class AuditLogDetailResponse(BaseModel):
    """Response for single audit log detail."""
    success: bool = True
    data: AuditLogDetail


class EntityHistoryResponse(BaseModel):
    """Response for entity history."""
    success: bool = True
    data: EntityHistory


class LoginHistoryListResponse(BaseModel):
    """Response for listing login history."""
    items: List[LoginHistoryItem]
    total: int
    has_more: bool = False


class SensitiveAccessListResponse(BaseModel):
    """Response for listing sensitive data access."""
    items: List[SensitiveAccessItem]
    total: int
    has_more: bool = False


class RetentionPolicyListResponse(BaseModel):
    """Response for listing retention policies."""
    success: bool = True
    data: List[RetentionPolicyItem]


class AuditSummaryResponse(BaseModel):
    """Response for audit summary."""
    success: bool = True
    data: List[AuditSummaryItem]
    period: Optional[Dict[str, str]] = None


class AuditSearchResponse(BaseModel):
    """Response for audit log search."""
    items: List[AuditLogItem]
    total: int
    has_more: bool = False
    search_query: str


class CleanupResponse(BaseModel):
    """Response for audit cleanup operation."""
    success: bool = True
    message: str
    deleted_count: int


class AuditResponse(BaseModel):
    """Generic audit operation response."""
    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None
