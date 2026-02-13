"""
Policy Engine - Central Authority for Access Decisions

IRON LAW Compliance:
- Law 0: Separation of Concerns - Policy Engine = central authority untuk access decisions
- Law 10: AI Safety Boundary - Policy Engine gatekeeping
- Law 12: Audit Immutability - log semua permission checks

This is the main entry point for all authorization decisions in MilkyHoop.
ALL access requests MUST go through PolicyEngine.can() method.
"""
import logging
import json
from typing import List, Optional, Dict, Any
from datetime import datetime
from uuid import uuid4

from ..models.policy_models import (
    User,
    Resource,
    ApprovalRule,
    PermissionCheck,
    PolicyDecision,
    AuditLogEntry,
    DecisionReason,
)
from ..config import settings
from .permission_service import PermissionService
from .visibility_service import VisibilityService

logger = logging.getLogger(__name__)


# SQL Queries for Approval Workflows
GET_APPROVAL_RULE = """
    SELECT 
        aw.id,
        aw.name,
        aw.document_type,
        aw.min_amount,
        aw.max_amount,
        aw.min_approvals,
        aw.approver_role_codes,
        aw.require_sequential,
        aw.expiry_hours,
        aw.is_active
    FROM approval_workflows aw
    WHERE aw.tenant_id = $1
    AND aw.document_type = $2
    AND aw.is_active = TRUE
    AND ($3 >= aw.min_amount OR aw.min_amount IS NULL)
    AND ($3 <= aw.max_amount OR aw.max_amount IS NULL)
    ORDER BY aw.min_amount DESC NULLS LAST
    LIMIT 1
"""

GET_APPROVERS_BY_ROLE = """
    SELECT 
        u.id,
        u.email,
        u.name,
        tm.role_id,
        tr.code as role_code
    FROM users u
    JOIN tenant_members tm ON tm.user_id = u.id
    JOIN tenant_roles tr ON tr.id = tm.role_id
    WHERE tm.tenant_id = $1
    AND tr.code = ANY($2::text[])
    AND tm.is_active = TRUE
"""

INSERT_AUDIT_LOG = """
    INSERT INTO policy_audit_log (
        id, timestamp, user_id, tenant_id, role_code, is_ai_agent,
        action, resource_module, resource_entity_type, resource_entity_id,
        allowed, reason, request_id, ip_address, user_agent, metadata
    ) VALUES (
        $1, $2, $3, $4, $5, $6,
        $7, $8, $9, $10,
        $11, $12, $13, $14, $15, $16
    )
"""

CHECK_IMMUTABLE_TRANSACTION = """
    SELECT 
        EXISTS (
            SELECT 1 
            FROM immutable_transactions it
            WHERE it.entity_type = $1
            AND it.entity_id = $2
            AND it.tenant_id = $3
        ) as is_immutable
"""


class PolicyEngine:
    """
    Central authority untuk semua access decisions.
    
    IRON LAW 0: Separation of Concerns
    - Policy Engine -> Siapa boleh apa, lihat apa, kapan
    - Financial Core -> Eksekusi transaksi, journal, report
    
    Usage:
        engine = PolicyEngine(pool)
        
        # Check permission
        can_create = await engine.can(user, 'C', resource)
        
        # Get full decision with audit
        decision = await engine.check(user, 'C', resource)
        
        # Get visibility
        levels = await engine.get_visibility(user)
    """
    
    def __init__(self, pool, enable_audit: bool = True):
        """
        Initialize PolicyEngine.
        
        Args:
            pool: Database connection pool (asyncpg)
            enable_audit: Whether to log all decisions (IRON LAW 12)
        """
        self.pool = pool
        self.enable_audit = enable_audit and settings.audit.enabled
        
        # Initialize sub-services
        self.permission_service = PermissionService(pool)
        self.visibility_service = VisibilityService(pool)
        
        logger.info(f"PolicyEngine initialized. Audit: {self.enable_audit}")
    
    async def can(
        self, 
        user: User, 
        action: str, 
        resource: Resource,
        context: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Check if user can perform action on resource.
        
        This is the main entry point for permission checks.
        
        Args:
            user: User performing the action
            action: Action code (C, R, U, D, V, A, P, E)
            resource: Resource being accessed
            context: Optional additional context
            
        Returns:
            bool: True if allowed, False if denied
            
        Actions:
            C - Create
            R - Read
            U - Update
            D - Delete
            V - Void
            A - Approve
            P - Post
            E - Export
        """
        decision = await self.check(user, action, resource, context)
        return decision.allowed
    
    async def check(
        self, 
        user: User, 
        action: str, 
        resource: Resource,
        context: Optional[Dict[str, Any]] = None
    ) -> PolicyDecision:
        """
        Perform full permission check with audit logging.
        
        Returns PolicyDecision with all details including reason for denial.
        """
        # Create permission check record
        check = PermissionCheck(
            user=user,
            action=action,
            resource=resource,
            ip_address=context.get("ip_address") if context else None,
            user_agent=context.get("user_agent") if context else None,
            is_ai_initiated=user.is_ai_agent,
        )
        
        evaluated_rules: List[str] = []
        
        # 1. Check if user is active
        if not user.is_active:
            decision = PolicyDecision(
                allowed=False,
                reason=DecisionReason.DENIED_INACTIVE_USER,
                check=check,
                evaluated_rules=["user_active_check"],
            )
            await self._audit_decision(decision)
            return decision
        evaluated_rules.append("user_active_check")
        
        # 2. Check AI Safety Boundary (IRON LAW 10)
        if user.is_ai_agent:
            ai_allowed, ai_reason = await self.permission_service.check_ai_boundary(
                user, action, resource
            )
            if not ai_allowed:
                decision = PolicyDecision(
                    allowed=False,
                    reason=ai_reason,
                    check=check,
                    evaluated_rules=evaluated_rules + ["ai_boundary_check"],
                )
                await self._audit_decision(decision)
                return decision
        evaluated_rules.append("ai_boundary_check")
        
        # 3. Check immutability for modify/delete actions
        if action in ["U", "D", "V"] and resource.entity_id:
            is_immutable = await self._check_immutable(resource)
            if is_immutable:
                decision = PolicyDecision(
                    allowed=False,
                    reason=DecisionReason.DENIED_IMMUTABLE,
                    check=check,
                    evaluated_rules=evaluated_rules + ["immutability_check"],
                )
                await self._audit_decision(decision)
                return decision
        evaluated_rules.append("immutability_check")
        
        # 4. Check base permission
        has_permission, permission_reason = await self.permission_service.has_permission(
            user, action, resource
        )
        if not has_permission:
            decision = PolicyDecision(
                allowed=False,
                reason=permission_reason,
                check=check,
                evaluated_rules=evaluated_rules + ["permission_check"],
            )
            await self._audit_decision(decision)
            return decision
        evaluated_rules.append("permission_check")
        
        # 5. Check visibility/confidentiality
        if resource.confidentiality_level:
            can_see, visibility_reason = await self.visibility_service.check_visibility(
                user, resource
            )
            if not can_see:
                decision = PolicyDecision(
                    allowed=False,
                    reason=visibility_reason,
                    check=check,
                    evaluated_rules=evaluated_rules + ["visibility_check"],
                    visibility_filtered=True,
                )
                await self._audit_decision(decision)
                return decision
        evaluated_rules.append("visibility_check")
        
        # 6. Check if approval is required (for write operations)
        approval_rule = None
        if action in ["C", "U", "V", "P"] and resource.amount:
            approval_rule = await self.requires_approval({
                "tenant_id": user.tenant_id,
                "document_type": resource.entity_type,
                "amount": resource.amount,
            })
        evaluated_rules.append("approval_check")
        
        # Success!
        decision = PolicyDecision(
            allowed=True,
            reason=DecisionReason.ALLOWED,
            check=check,
            approval_required=approval_rule is not None,
            approval_rule=approval_rule,
            evaluated_rules=evaluated_rules,
        )
        
        await self._audit_decision(decision)
        return decision
    
    async def get_visibility(self, user: User) -> List[str]:
        """
        Get confidentiality levels user can access.
        
        Returns list like ['L1', 'L2', 'L3'].
        """
        return await self.visibility_service.get_visibility_levels(user)
    
    async def requires_approval(self, transaction: Dict[str, Any]) -> Optional[ApprovalRule]:
        """
        Check if transaction requires approval.
        
        Args:
            transaction: Dict with keys:
                - tenant_id: str
                - document_type: str (e.g., 'sales_invoice', 'purchase_order')
                - amount: float
                
        Returns:
            ApprovalRule if approval required, None otherwise
        """
        tenant_id = transaction.get("tenant_id")
        document_type = transaction.get("document_type")
        amount = transaction.get("amount", 0)
        
        if not tenant_id or not document_type:
            return None
        
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    GET_APPROVAL_RULE,
                    tenant_id,
                    document_type,
                    amount
                )
                
                if not row:
                    return None
                
                return ApprovalRule(
                    id=str(row["id"]),
                    name=row["name"],
                    min_approvals=row["min_approvals"],
                    approver_role_codes=row["approver_role_codes"] or [],
                    min_amount=row["min_amount"],
                    max_amount=row["max_amount"],
                    document_type=row["document_type"],
                    require_sequential_approval=row["require_sequential"] or False,
                    expiry_hours=row["expiry_hours"] or 72,
                )
                
        except Exception as e:
            logger.error(f"Error checking approval requirement: {e}")
            return None
    
    async def get_approvers(self, rule: ApprovalRule, tenant_id: str) -> List[User]:
        """
        Get list of users who can approve based on rule.
        
        Args:
            rule: ApprovalRule with approver_role_codes
            tenant_id: Tenant ID
            
        Returns:
            List of User objects who can approve
        """
        if not rule.approver_role_codes:
            return []
        
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(
                    GET_APPROVERS_BY_ROLE,
                    tenant_id,
                    rule.approver_role_codes
                )
                
                approvers = []
                for row in rows:
                    approvers.append(User(
                        id=str(row["id"]),
                        tenant_id=tenant_id,
                        role_id=str(row["role_id"]),
                        role_code=row["role_code"],
                        email=row["email"],
                        name=row["name"],
                    ))
                
                return approvers
                
        except Exception as e:
            logger.error(f"Error fetching approvers: {e}")
            return []
    
    def apply_visibility_filter(self, base_query: str, user: User, visibility_levels: Optional[List[str]] = None) -> str:
        """
        Inject visibility filter into SQL query.
        
        Args:
            base_query: Original SQL query
            user: User for context
            visibility_levels: Pre-fetched visibility levels (optional)
            
        Returns:
            Modified query with visibility filter
            
        Note: Use get_visibility() first if visibility_levels not provided.
        """
        if visibility_levels is None:
            # Use default minimal visibility
            visibility_levels = ["L1"]
        
        return self.visibility_service.apply_visibility_filter(
            base_query, user, visibility_levels
        )
    
    async def _check_immutable(self, resource: Resource) -> bool:
        """Check if resource is immutable (cannot be modified)"""
        if not resource.entity_type or not resource.entity_id:
            return False
        
        try:
            async with self.pool.acquire() as conn:
                result = await conn.fetchval(
                    CHECK_IMMUTABLE_TRANSACTION,
                    resource.entity_type,
                    resource.entity_id,
                    resource.tenant_id or ""
                )
                return result or False
        except Exception as e:
            logger.error(f"Error checking immutability: {e}")
            return False
    
    async def _audit_decision(self, decision: PolicyDecision) -> None:
        """
        Log decision to audit trail.
        
        IRON LAW 12: Audit Immutability
        - All decisions are logged
        - Logs are append-only
        """
        if not self.enable_audit:
            return
        
        # Only log denials if configured
        if settings.audit.log_denied_only and decision.allowed:
            return
        
        try:
            entry = AuditLogEntry.from_decision(decision)
            
            async with self.pool.acquire() as conn:
                await conn.execute(
                    INSERT_AUDIT_LOG,
                    entry.id,
                    entry.timestamp,
                    entry.user_id,
                    entry.tenant_id,
                    entry.role_code,
                    entry.is_ai_agent,
                    entry.action,
                    entry.resource_module,
                    entry.resource_entity_type,
                    entry.resource_entity_id,
                    entry.allowed,
                    entry.reason,
                    entry.request_id,
                    entry.ip_address,
                    entry.user_agent,
                    json.dumps(entry.metadata) if entry.metadata else None,
                )
                
            logger.debug(f"Audit logged: {entry.id} - {entry.action} - {entry.allowed}")
            
        except Exception as e:
            # Don't fail the request if audit logging fails
            # But log the error for monitoring
            logger.error(f"Failed to write audit log: {e}")
    
    def clear_cache(self):
        """Clear all caches"""
        self.permission_service.clear_cache()
        self.visibility_service.clear_cache()
        logger.info("PolicyEngine cache cleared")


# Convenience function for creating PolicyEngine
async def create_policy_engine(pool=None, enable_audit: bool = True) -> PolicyEngine:
    """
    Factory function to create PolicyEngine with database pool.
    
    Usage:
        engine = await create_policy_engine()
        # or with existing pool:
        engine = await create_policy_engine(pool=my_pool)
    """
    if pool is None:
        from ..utils.db import get_pool
        pool = await get_pool()
    
    return PolicyEngine(pool, enable_audit=enable_audit)
