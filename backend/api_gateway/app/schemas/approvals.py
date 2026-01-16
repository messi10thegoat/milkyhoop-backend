"""
Pydantic schemas for Approval Workflows module.

Approval Workflows enable multi-level approval for transactions:
- Define workflows per document type (PO, Bill, Sales Order, etc.)
- Amount-based workflow triggers
- Sequential or parallel approval levels
- Delegation for vacation coverage
- Full audit trail of approval actions

NO JOURNAL ENTRIES - This is a process control system.
Journal entries occur when documents are posted after approval.
"""

from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Dict, Any, Literal
from datetime import date, datetime
from uuid import UUID


# =============================================================================
# REQUEST MODELS - Workflow
# =============================================================================

class CreateApprovalWorkflowRequest(BaseModel):
    """Request to create an approval workflow."""
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = None
    document_type: Literal[
        "purchase_order", "bill", "expense", "sales_order", "journal_entry"
    ] = Field(..., description="Document type this workflow applies to")
    min_amount: int = Field(0, ge=0, description="Trigger if amount >= this")
    max_amount: Optional[int] = Field(None, ge=0, description="Trigger if amount <= this (NULL = no max)")
    is_sequential: bool = Field(True, description="Must approve in order vs parallel")
    auto_approve_below_min: bool = Field(True, description="Auto-approve if below min_amount")

    @field_validator('name')
    @classmethod
    def validate_name(cls, v):
        if not v or not v.strip():
            raise ValueError('Workflow name is required')
        return v.strip()


class UpdateApprovalWorkflowRequest(BaseModel):
    """Request to update an approval workflow."""
    name: Optional[str] = Field(None, max_length=100)
    description: Optional[str] = None
    min_amount: Optional[int] = Field(None, ge=0)
    max_amount: Optional[int] = Field(None, ge=0)
    is_active: Optional[bool] = None
    is_sequential: Optional[bool] = None
    auto_approve_below_min: Optional[bool] = None


# =============================================================================
# REQUEST MODELS - Approval Level
# =============================================================================

class CreateApprovalLevelRequest(BaseModel):
    """Request to create/add an approval level."""
    level_order: int = Field(..., ge=1, le=10, description="Order in workflow (1, 2, 3...)")
    name: str = Field(..., min_length=1, max_length=100, description="Level name e.g. 'Manager'")
    approver_type: Literal["user", "role", "any_of_users", "any_of_roles"]
    approver_user_id: Optional[UUID] = Field(None, description="If approver_type = 'user'")
    approver_role: Optional[str] = Field(None, max_length=50, description="If approver_type = 'role'")
    approver_user_ids: Optional[List[UUID]] = Field(None, description="If approver_type = 'any_of_users'")
    approver_roles: Optional[List[str]] = Field(None, description="If approver_type = 'any_of_roles'")
    auto_escalate_hours: Optional[int] = Field(None, ge=1, le=720, description="Auto-escalate after X hours")
    escalate_to_user_id: Optional[UUID] = None
    can_reject: bool = Field(True)
    notify_on_pending: bool = Field(True)
    notify_on_approved: bool = Field(True)
    notify_on_rejected: bool = Field(True)

    @field_validator('name')
    @classmethod
    def validate_name(cls, v):
        if not v or not v.strip():
            raise ValueError('Level name is required')
        return v.strip()


class UpdateApprovalLevelRequest(BaseModel):
    """Request to update an approval level."""
    name: Optional[str] = Field(None, max_length=100)
    approver_type: Optional[Literal["user", "role", "any_of_users", "any_of_roles"]] = None
    approver_user_id: Optional[UUID] = None
    approver_role: Optional[str] = Field(None, max_length=50)
    approver_user_ids: Optional[List[UUID]] = None
    approver_roles: Optional[List[str]] = None
    auto_escalate_hours: Optional[int] = Field(None, ge=1, le=720)
    escalate_to_user_id: Optional[UUID] = None
    can_reject: Optional[bool] = None
    notify_on_pending: Optional[bool] = None
    notify_on_approved: Optional[bool] = None
    notify_on_rejected: Optional[bool] = None


# =============================================================================
# REQUEST MODELS - Approval Actions
# =============================================================================

class ApproveRequestBody(BaseModel):
    """Request body for approving."""
    comments: Optional[str] = Field(None, max_length=500)


class RejectRequestBody(BaseModel):
    """Request body for rejecting."""
    comments: str = Field(..., min_length=1, max_length=500, description="Rejection reason required")


class EscalateRequestBody(BaseModel):
    """Request body for manual escalation."""
    escalate_to_user_id: UUID
    reason: str = Field(..., min_length=1, max_length=500)


class CancelRequestBody(BaseModel):
    """Request body for cancellation."""
    reason: Optional[str] = Field(None, max_length=500)


# =============================================================================
# REQUEST MODELS - Submit for Approval
# =============================================================================

class SubmitForApprovalRequest(BaseModel):
    """Request to submit a document for approval."""
    notes: Optional[str] = Field(None, max_length=500)


# =============================================================================
# REQUEST MODELS - Delegation
# =============================================================================

class CreateDelegationRequest(BaseModel):
    """Request to create approval delegation."""
    delegate_user_id: UUID = Field(..., description="User who will act as delegate")
    start_date: date = Field(..., description="Delegation start date")
    end_date: date = Field(..., description="Delegation end date")
    workflow_ids: Optional[List[UUID]] = Field(None, description="Specific workflows (NULL = all)")

    @field_validator('end_date')
    @classmethod
    def validate_dates(cls, v, info):
        if info.data.get('start_date') and v < info.data['start_date']:
            raise ValueError('End date must be >= start date')
        return v


# =============================================================================
# RESPONSE MODELS - Workflow
# =============================================================================

class ApprovalLevelItem(BaseModel):
    """Approval level summary."""
    id: str
    level_order: int
    name: str
    approver_type: str
    approver_user_id: Optional[str] = None
    approver_role: Optional[str] = None
    can_reject: bool
    auto_escalate_hours: Optional[int] = None


class ApprovalWorkflowItem(BaseModel):
    """Approval workflow list item."""
    id: str
    name: str
    description: Optional[str] = None
    document_type: str
    min_amount: int
    max_amount: Optional[int] = None
    is_active: bool
    is_sequential: bool
    level_count: int = 0
    created_at: str


class ApprovalWorkflowDetail(BaseModel):
    """Detailed approval workflow with levels."""
    id: str
    name: str
    description: Optional[str] = None
    document_type: str
    min_amount: int
    max_amount: Optional[int] = None
    is_active: bool
    is_sequential: bool
    auto_approve_below_min: bool
    levels: List[ApprovalLevelItem]
    created_at: str
    updated_at: str
    created_by: Optional[str] = None


# =============================================================================
# RESPONSE MODELS - Approval Request
# =============================================================================

class ApprovalActionItem(BaseModel):
    """Single approval action in history."""
    level_order: int
    level_name: str
    action: str
    action_by: str
    action_at: datetime
    comments: Optional[str] = None


class ApprovalRequestItem(BaseModel):
    """Approval request list item."""
    id: str
    workflow_name: str
    document_type: str
    document_id: str
    document_number: Optional[str] = None
    document_amount: Optional[int] = None
    current_level: int
    status: str
    requested_by: str
    requested_at: datetime


class ApprovalRequestDetail(BaseModel):
    """Detailed approval request with history."""
    id: str
    workflow_id: str
    workflow_name: str
    document_type: str
    document_id: str
    document_number: Optional[str] = None
    document_amount: Optional[int] = None
    current_level: int
    total_levels: int
    status: str
    requested_by: str
    requested_at: datetime
    completed_at: Optional[datetime] = None
    notes: Optional[str] = None
    actions: List[ApprovalActionItem] = []


class PendingApprovalItem(BaseModel):
    """Pending approval for a user."""
    request_id: str
    workflow_name: str
    document_type: str
    document_id: str
    document_number: Optional[str] = None
    document_amount: Optional[int] = None
    current_level: int
    level_name: str
    requested_by: str
    requested_at: datetime
    waiting_hours: Optional[float] = None


# =============================================================================
# RESPONSE MODELS - Delegation
# =============================================================================

class DelegationItem(BaseModel):
    """Approval delegation item."""
    id: str
    approver_user_id: str
    delegate_user_id: str
    start_date: date
    end_date: date
    workflow_ids: Optional[List[str]] = None
    is_active: bool
    created_at: str


# =============================================================================
# RESPONSE MODELS - Statistics
# =============================================================================

class ApprovalStatistics(BaseModel):
    """Approval statistics by document type."""
    document_type: str
    total_requests: int
    pending_count: int
    approved_count: int
    rejected_count: int
    cancelled_count: int
    avg_approval_hours: Optional[float] = None


class TurnaroundTimeReport(BaseModel):
    """Turnaround time by workflow."""
    workflow_name: str
    document_type: str
    avg_hours: float
    min_hours: float
    max_hours: float
    total_completed: int


# =============================================================================
# GENERIC RESPONSE MODELS
# =============================================================================

class ApprovalWorkflowListResponse(BaseModel):
    """Response for listing workflows."""
    items: List[ApprovalWorkflowItem]
    total: int
    has_more: bool = False


class ApprovalWorkflowDetailResponse(BaseModel):
    """Response for workflow detail."""
    success: bool = True
    data: ApprovalWorkflowDetail


class ApprovalRequestListResponse(BaseModel):
    """Response for listing approval requests."""
    items: List[ApprovalRequestItem]
    total: int
    has_more: bool = False


class ApprovalRequestDetailResponse(BaseModel):
    """Response for approval request detail."""
    success: bool = True
    data: ApprovalRequestDetail


class PendingApprovalsResponse(BaseModel):
    """Response for pending approvals."""
    items: List[PendingApprovalItem]
    total: int


class DelegationListResponse(BaseModel):
    """Response for listing delegations."""
    items: List[DelegationItem]
    total: int


class ApprovalStatisticsResponse(BaseModel):
    """Response for approval statistics."""
    success: bool = True
    data: List[ApprovalStatistics]
    period: Optional[Dict[str, str]] = None


class SubmitApprovalResponse(BaseModel):
    """Response for submit for approval."""
    success: bool = True
    status: str  # "approved", "pending", "not_required"
    message: str
    request_id: Optional[str] = None


class ApprovalActionResponse(BaseModel):
    """Response for approval action (approve/reject)."""
    success: bool = True
    status: str
    message: str
    next_level: Optional[int] = None


class ApprovalResponse(BaseModel):
    """Generic approval operation response."""
    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None
