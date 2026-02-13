"""
MilkyHoop Policy Engine Service
================================

Central authority untuk semua access decisions di MilkyHoop.
Mengimplementasikan IRON LAWs:
- Law 0: Separation of Concerns - Policy Engine = central authority untuk access decisions
- Law 10: AI Safety Boundary - Policy Engine gatekeeping
- Law 12: Audit Immutability - log semua permission checks

Core Responsibilities:
- Permission checks (siapa boleh apa)
- Visibility control (siapa boleh lihat apa - FCL)
- Approval workflow integration
- Audit logging untuk semua access decisions

Usage:
    from policy_engine import PolicyEngine, User, Resource

    engine = PolicyEngine(pool)
    
    # Check permission
    can_create = await engine.can(user, 'C', resource)
    
    # Get visibility levels
    visibility = await engine.get_visibility(user)
    
    # Check approval requirement
    approval_rule = await engine.requires_approval(transaction)
"""

__version__ = "1.0.0"
__author__ = "MilkyHoop Team"

# Models
from .app.models import (
    User,
    Resource,
    ApprovalRule,
    PermissionCheck,
    VisibilityLevel,
    PolicyDecision,
    AuditLogEntry,
)

# Services
from .app.services import (
    PolicyEngine,
    PermissionService,
    VisibilityService,
)

# Config
from .app.config import settings

__all__ = [
    # Version
    "__version__",
    
    # Models
    "User",
    "Resource",
    "ApprovalRule",
    "PermissionCheck",
    "VisibilityLevel",
    "PolicyDecision",
    "AuditLogEntry",
    
    # Services
    "PolicyEngine",
    "PermissionService",
    "VisibilityService",
    
    # Config
    "settings",
]
