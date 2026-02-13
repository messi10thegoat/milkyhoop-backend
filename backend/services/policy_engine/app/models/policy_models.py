"""
Policy Engine Models

Core data structures untuk Policy Engine.
Mengikuti IRON LAW 0: Separation of Concerns - Models terpisah dari logic.
"""
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, List, Dict, Any
from uuid import UUID, uuid4


class ApprovalStatus(str, Enum):
    """Status approval workflow"""
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class DecisionReason(str, Enum):
    """Reason for policy decision"""
    ALLOWED = "ALLOWED"
    DENIED_NO_PERMISSION = "DENIED_NO_PERMISSION"
    DENIED_VISIBILITY = "DENIED_VISIBILITY"
    DENIED_APPROVAL_REQUIRED = "DENIED_APPROVAL_REQUIRED"
    DENIED_AI_BOUNDARY = "DENIED_AI_BOUNDARY"
    DENIED_INACTIVE_USER = "DENIED_INACTIVE_USER"
    DENIED_TENANT_MISMATCH = "DENIED_TENANT_MISMATCH"
    DENIED_FISCAL_CLOSED = "DENIED_FISCAL_CLOSED"
    DENIED_IMMUTABLE = "DENIED_IMMUTABLE"


@dataclass
class User:
    """
    User context untuk permission checks.
    
    Represents the actor performing an action.
    """
    id: str
    tenant_id: str
    role_id: str
    role_code: str
    
    # Optional metadata
    email: Optional[str] = None
    name: Optional[str] = None
    is_active: bool = True
    is_ai_agent: bool = False  # IRON LAW 10: AI Safety Boundary
    
    # Cached permissions (populated by PermissionService)
    _permissions: Optional[Dict[str, List[str]]] = field(default=None, repr=False)
    _visibility_levels: Optional[List[str]] = field(default=None, repr=False)


@dataclass
class Resource:
    """
    Resource being accessed.
    
    Represents the target of an action.
    """
    module: str  # e.g., "sales", "purchase", "inventory"
    entity_type: Optional[str] = None  # e.g., "invoice", "bill", "product"
    entity_id: Optional[str] = None  # UUID of specific entity
    confidentiality_level: Optional[str] = None  # L1-L5
    
    # Additional context
    tenant_id: Optional[str] = None
    amount: Optional[float] = None  # For approval threshold checks
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class ApprovalRule:
    """
    Approval workflow rule.
    
    Integrates with approval_workflows table (V056).
    """
    id: str
    name: str
    min_approvals: int
    approver_role_codes: List[str]
    
    # Thresholds
    min_amount: Optional[float] = None
    max_amount: Optional[float] = None
    document_type: Optional[str] = None
    
    # Configuration
    is_active: bool = True
    auto_approve_below_threshold: bool = False
    require_sequential_approval: bool = False
    expiry_hours: int = 72  # 3 days default


@dataclass
class PermissionCheck:
    """
    Permission check request.
    
    Captures all context needed for a permission decision.
    """
    user: User
    action: str  # C, R, U, D, V, A, P, E
    resource: Resource
    
    # Request context
    request_id: str = field(default_factory=lambda: str(uuid4()))
    timestamp: datetime = field(default_factory=datetime.utcnow)
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    
    # AI context (Law 10)
    is_ai_initiated: bool = False
    ai_confidence: Optional[float] = None


@dataclass
class VisibilityLevel:
    """
    Visibility/Confidentiality level definition.
    
    FCL (Financial Confidentiality Level) implementation.
    """
    level: str  # L1-L5
    name: str
    description: str
    min_role_hierarchy: int
    
    # Access restrictions
    allowed_modules: Optional[List[str]] = None
    excluded_fields: Optional[List[str]] = None


@dataclass
class PolicyDecision:
    """
    Result of a policy check.
    
    IRON LAW 12: All decisions are logged immutably.
    """
    allowed: bool
    reason: DecisionReason
    
    # Context
    check: PermissionCheck
    decision_id: str = field(default_factory=lambda: str(uuid4()))
    timestamp: datetime = field(default_factory=datetime.utcnow)
    
    # Additional info
    approval_required: bool = False
    approval_rule: Optional[ApprovalRule] = None
    visibility_filtered: bool = False
    filtered_fields: Optional[List[str]] = None
    
    # Audit trail
    policy_version: str = "1.0.0"
    evaluated_rules: Optional[List[str]] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization"""
        return {
            "decision_id": self.decision_id,
            "allowed": self.allowed,
            "reason": self.reason.value,
            "timestamp": self.timestamp.isoformat(),
            "user_id": self.check.user.id,
            "tenant_id": self.check.user.tenant_id,
            "action": self.check.action,
            "resource_module": self.check.resource.module,
            "resource_entity_type": self.check.resource.entity_type,
            "resource_entity_id": self.check.resource.entity_id,
            "approval_required": self.approval_required,
            "visibility_filtered": self.visibility_filtered,
            "policy_version": self.policy_version,
        }


@dataclass
class AuditLogEntry:
    """
    Audit log entry for policy checks.
    
    IRON LAW 12: Audit Immutability
    - Append-only log
    - No edits or deletes
    - Full context preserved
    """
    id: str = field(default_factory=lambda: str(uuid4()))
    timestamp: datetime = field(default_factory=datetime.utcnow)
    
    # Actor
    user_id: str = ""
    tenant_id: str = ""
    role_code: str = ""
    is_ai_agent: bool = False
    
    # Action
    action: str = ""
    resource_module: str = ""
    resource_entity_type: Optional[str] = None
    resource_entity_id: Optional[str] = None
    
    # Decision
    allowed: bool = False
    reason: str = ""
    
    # Context
    request_id: Optional[str] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    
    # Additional data
    metadata: Optional[Dict[str, Any]] = None
    
    @classmethod
    def from_decision(cls, decision: PolicyDecision) -> "AuditLogEntry":
        """Create audit log entry from policy decision"""
        return cls(
            user_id=decision.check.user.id,
            tenant_id=decision.check.user.tenant_id,
            role_code=decision.check.user.role_code,
            is_ai_agent=decision.check.user.is_ai_agent,
            action=decision.check.action,
            resource_module=decision.check.resource.module,
            resource_entity_type=decision.check.resource.entity_type,
            resource_entity_id=decision.check.resource.entity_id,
            allowed=decision.allowed,
            reason=decision.reason.value,
            request_id=decision.check.request_id,
            ip_address=decision.check.ip_address,
            user_agent=decision.check.user_agent,
            metadata={
                "approval_required": decision.approval_required,
                "visibility_filtered": decision.visibility_filtered,
                "policy_version": decision.policy_version,
            }
        )
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for storage"""
        return {
            "id": self.id,
            "timestamp": self.timestamp.isoformat(),
            "user_id": self.user_id,
            "tenant_id": self.tenant_id,
            "role_code": self.role_code,
            "is_ai_agent": self.is_ai_agent,
            "action": self.action,
            "resource_module": self.resource_module,
            "resource_entity_type": self.resource_entity_type,
            "resource_entity_id": self.resource_entity_id,
            "allowed": self.allowed,
            "reason": self.reason,
            "request_id": self.request_id,
            "ip_address": self.ip_address,
            "user_agent": self.user_agent,
            "metadata": self.metadata,
        }
