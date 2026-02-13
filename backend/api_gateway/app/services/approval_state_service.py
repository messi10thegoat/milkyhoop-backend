"""
Approval State Service - State Machine for Approval Workflows

Enforces valid state transitions for approval requests.
Handles multi-level sequential approvals, escalation, and delegation.

State Machine:
    PENDING → APPROVED (all levels approved)
    PENDING → REJECTED (any level rejected)
    PENDING → CANCELLED (by requester)
    PENDING → ESCALATED (timeout or manual)

Level States:
    WAITING → APPROVED → (next level or complete)
    WAITING → REJECTED → (request rejected)
    WAITING → SKIPPED → (auto-skip if conditions met)
    WAITING → ESCALATED → (to designated escalation target)

IRON LAW COMPLIANCE:
- Law 8: No Silent Mutation - All state changes logged to approval_actions
- Law 12: Audit Immutability - Actions recorded permanently
- Law 14: Idempotency - Same approve/reject action is idempotent
"""
import logging
from typing import Optional, List, Dict, Any, Tuple
from uuid import UUID
from datetime import datetime, timedelta
from enum import Enum
import asyncpg

logger = logging.getLogger(__name__)


class ApprovalStatus(str, Enum):
    PENDING = 'pending'
    APPROVED = 'approved'
    REJECTED = 'rejected'
    CANCELLED = 'cancelled'


class ActionType(str, Enum):
    APPROVED = 'approved'
    REJECTED = 'rejected'
    ESCALATED = 'escalated'
    SKIPPED = 'skipped'


class ApproverType(str, Enum):
    USER = 'user'
    ROLE = 'role'
    ANY_OF_USERS = 'any_of_users'
    ANY_OF_ROLES = 'any_of_roles'


class ApprovalStateError(Exception):
    """Exception for invalid state transitions."""
    def __init__(self, message: str, code: str = 'INVALID_STATE'):
        self.message = message
        self.code = code
        super().__init__(message)


class ApprovalStateService:
    """
    State machine service for approval workflow management.
    
    Responsibilities:
    1. Validate state transitions
    2. Process approve/reject actions
    3. Handle multi-level sequential approvals
    4. Manage escalation and delegation
    5. Trigger document status updates on completion
    """
    
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool
    
    async def submit_for_approval(
        self,
        tenant_id: str,
        document_type: str,
        document_id: UUID,
        document_number: str,
        document_amount: int,
        requested_by: UUID,
        notes: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Submit a document for approval.
        Creates an approval_request and links to the appropriate workflow.
        
        Returns the created approval request.
        """
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                # Find applicable workflow
                workflow = await conn.fetchrow('''
                    SELECT id, name, is_sequential, auto_approve_below_min
                    FROM approval_workflows
                    WHERE tenant_id = 
                    AND document_type = 
                    AND is_active = TRUE
                    AND ( >= min_amount OR min_amount IS NULL OR min_amount = 0)
                    AND ( <= max_amount OR max_amount IS NULL)
                    ORDER BY min_amount DESC NULLS LAST
                    LIMIT 1
                ''', tenant_id, document_type, document_amount)
                
                if not workflow:
                    # No workflow found - auto-approve
                    logger.info(f"No workflow found for {document_type} amount={document_amount}, auto-approving")
                    return {
                        'status': 'auto_approved',
                        'message': 'No approval workflow required for this amount'
                    }
                
                # Check if already submitted
                existing = await conn.fetchrow('''
                    SELECT id, status FROM approval_requests
                    WHERE tenant_id =  AND document_type =  AND document_id = 
                ''', tenant_id, document_type, document_id)
                
                if existing:
                    if existing['status'] == 'pending':
                        raise ApprovalStateError(
                            'Document already pending approval',
                            'ALREADY_PENDING'
                        )
                    elif existing['status'] == 'approved':
                        raise ApprovalStateError(
                            'Document already approved',
                            'ALREADY_APPROVED'
                        )
                
                # Auto-approve if below minimum
                if workflow['auto_approve_below_min']:
                    min_amount_row = await conn.fetchval('''
                        SELECT min_amount FROM approval_workflows WHERE id = 
                    ''', workflow['id'])
                    if min_amount_row and document_amount < min_amount_row:
                        return {
                            'status': 'auto_approved',
                            'message': f'Amount below minimum threshold for {workflow["name"]}'
                        }
                
                # Create approval request
                request_id = await conn.fetchval('''
                    INSERT INTO approval_requests (
                        tenant_id, workflow_id, document_type, document_id,
                        document_number, document_amount, requested_by,
                        current_level, status, notes
                    ) VALUES (, , , , , , , 1, 'pending', )
                    ON CONFLICT (tenant_id, document_type, document_id) 
                    DO UPDATE SET 
                        status = 'pending',
                        current_level = 1,
                        workflow_id = EXCLUDED.workflow_id,
                        document_amount = EXCLUDED.document_amount,
                        requested_at = NOW()
                    RETURNING id
                ''', tenant_id, workflow['id'], document_type, document_id,
                    document_number, document_amount, requested_by, notes)
                
                # Get first level info
                first_level = await conn.fetchrow('''
                    SELECT id, name, approver_type, approver_user_id, approver_role,
                           approver_user_ids, approver_roles
                    FROM approval_levels
                    WHERE workflow_id = 
                    ORDER BY level_order
                    LIMIT 1
                ''', workflow['id'])
                
                return {
                    'id': str(request_id),
                    'status': 'pending',
                    'workflow_id': str(workflow['id']),
                    'workflow_name': workflow['name'],
                    'current_level': 1,
                    'current_level_name': first_level['name'] if first_level else None,
                    'message': f'Submitted for approval via {workflow["name"]}'
                }
    
    async def approve(
        self,
        request_id: UUID,
        action_by: UUID,
        comments: Optional[str] = None,
        tenant_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Approve the current level of an approval request.
        If this is the last level, marks the request as approved.
        """
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                # Get current request state
                request = await conn.fetchrow('''
                    SELECT ar.id, ar.tenant_id, ar.workflow_id, ar.document_type,
                           ar.document_id, ar.document_number, ar.current_level,
                           ar.status, aw.is_sequential
                    FROM approval_requests ar
                    JOIN approval_workflows aw ON aw.id = ar.workflow_id
                    WHERE ar.id = 
                    FOR UPDATE
                ''', request_id)
                
                if not request:
                    raise ApprovalStateError('Approval request not found', 'NOT_FOUND')
                
                if request['status'] != 'pending':
                    raise ApprovalStateError(
                        f'Cannot approve request in {request["status"]} status',
                        'INVALID_STATUS'
                    )
                
                # Validate approver has permission for current level
                current_level = await conn.fetchrow('''
                    SELECT al.id, al.name, al.approver_type, al.approver_user_id,
                           al.approver_role, al.approver_user_ids, al.approver_roles
                    FROM approval_levels al
                    WHERE al.workflow_id =  AND al.level_order = 
                ''', request['workflow_id'], request['current_level'])
                
                if not current_level:
                    raise ApprovalStateError('Current approval level not found', 'LEVEL_NOT_FOUND')
                
                # Check if user can approve this level
                can_approve = await self._can_user_approve_level(
                    conn, action_by, current_level, request['tenant_id']
                )
                
                if not can_approve:
                    raise ApprovalStateError(
                        'You are not authorized to approve this level',
                        'NOT_AUTHORIZED'
                    )
                
                # Check for duplicate action (idempotency)
                existing_action = await conn.fetchrow('''
                    SELECT id FROM approval_actions
                    WHERE request_id =  AND level_id =  AND action = 'approved'
                ''', request_id, current_level['id'])
                
                if existing_action:
                    # Already approved - idempotent, return success
                    return {
                        'status': 'already_approved',
                        'message': 'This level was already approved'
                    }
                
                # Record the approval action
                await conn.execute('''
                    INSERT INTO approval_actions (
                        request_id, level_id, action, action_by, comments
                    ) VALUES (, , 'approved', , )
                ''', request_id, current_level['id'], action_by, comments)
                
                # Check if there are more levels
                next_level = await conn.fetchrow('''
                    SELECT id, name, level_order FROM approval_levels
                    WHERE workflow_id =  AND level_order > 
                    ORDER BY level_order
                    LIMIT 1
                ''', request['workflow_id'], request['current_level'])
                
                if next_level:
                    # Move to next level
                    await conn.execute('''
                        UPDATE approval_requests
                        SET current_level = 
                        WHERE id = 
                    ''', next_level['level_order'], request_id)
                    
                    return {
                        'status': 'level_approved',
                        'current_level': next_level['level_order'],
                        'current_level_name': next_level['name'],
                        'message': f'Level {request["current_level"]} approved, moved to level {next_level["level_order"]}'
                    }
                else:
                    # All levels approved - complete the request
                    await conn.execute('''
                        UPDATE approval_requests
                        SET status = 'approved', completed_at = NOW()
                        WHERE id = 
                    ''', request_id)
                    
                    # Trigger document status update
                    await self._update_document_status(
                        conn, request['document_type'], request['document_id'],
                        'approved', request['tenant_id']
                    )
                    
                    return {
                        'status': 'approved',
                        'message': 'All levels approved, request completed',
                        'document_type': request['document_type'],
                        'document_id': str(request['document_id'])
                    }
    
    async def reject(
        self,
        request_id: UUID,
        action_by: UUID,
        reason: str,
        tenant_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Reject the approval request.
        Can be done at any level - immediately marks request as rejected.
        """
        if not reason or not reason.strip():
            raise ApprovalStateError('Rejection reason is required', 'REASON_REQUIRED')
        
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                # Get current request state
                request = await conn.fetchrow('''
                    SELECT ar.id, ar.tenant_id, ar.workflow_id, ar.document_type,
                           ar.document_id, ar.current_level, ar.status
                    FROM approval_requests ar
                    WHERE ar.id = 
                    FOR UPDATE
                ''', request_id)
                
                if not request:
                    raise ApprovalStateError('Approval request not found', 'NOT_FOUND')
                
                if request['status'] != 'pending':
                    raise ApprovalStateError(
                        f'Cannot reject request in {request["status"]} status',
                        'INVALID_STATUS'
                    )
                
                # Get current level
                current_level = await conn.fetchrow('''
                    SELECT id, name, can_reject FROM approval_levels
                    WHERE workflow_id =  AND level_order = 
                ''', request['workflow_id'], request['current_level'])
                
                if not current_level:
                    raise ApprovalStateError('Current approval level not found', 'LEVEL_NOT_FOUND')
                
                if not current_level['can_reject']:
                    raise ApprovalStateError(
                        'This level cannot reject requests',
                        'REJECTION_NOT_ALLOWED'
                    )
                
                # Validate approver
                can_reject = await self._can_user_approve_level(
                    conn, action_by, current_level, request['tenant_id']
                )
                
                if not can_reject:
                    raise ApprovalStateError(
                        'You are not authorized to reject this level',
                        'NOT_AUTHORIZED'
                    )
                
                # Record the rejection action
                await conn.execute('''
                    INSERT INTO approval_actions (
                        request_id, level_id, action, action_by, comments
                    ) VALUES (, , 'rejected', , )
                ''', request_id, current_level['id'], action_by, reason)
                
                # Mark request as rejected
                await conn.execute('''
                    UPDATE approval_requests
                    SET status = 'rejected', completed_at = NOW()
                    WHERE id = 
                ''', request_id)
                
                # Update document status
                await self._update_document_status(
                    conn, request['document_type'], request['document_id'],
                    'rejected', request['tenant_id']
                )
                
                return {
                    'status': 'rejected',
                    'message': 'Request rejected',
                    'document_type': request['document_type'],
                    'document_id': str(request['document_id']),
                    'reason': reason
                }
    
    async def cancel(
        self,
        request_id: UUID,
        cancelled_by: UUID,
        reason: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Cancel an approval request (by the requester).
        Only the original requester can cancel.
        """
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                request = await conn.fetchrow('''
                    SELECT id, requested_by, status, document_type, document_id
                    FROM approval_requests
                    WHERE id = 
                    FOR UPDATE
                ''', request_id)
                
                if not request:
                    raise ApprovalStateError('Approval request not found', 'NOT_FOUND')
                
                if request['status'] != 'pending':
                    raise ApprovalStateError(
                        f'Cannot cancel request in {request["status"]} status',
                        'INVALID_STATUS'
                    )
                
                if request['requested_by'] != cancelled_by:
                    raise ApprovalStateError(
                        'Only the requester can cancel this request',
                        'NOT_AUTHORIZED'
                    )
                
                await conn.execute('''
                    UPDATE approval_requests
                    SET status = 'cancelled', completed_at = NOW(), notes = 
                    WHERE id = 
                ''', request_id, reason)
                
                return {
                    'status': 'cancelled',
                    'message': 'Request cancelled'
                }
    
    async def escalate(
        self,
        request_id: UUID,
        escalated_by: UUID,
        escalate_to: UUID,
        reason: str
    ) -> Dict[str, Any]:
        """
        Manually escalate to a different approver.
        """
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                request = await conn.fetchrow('''
                    SELECT ar.id, ar.workflow_id, ar.current_level, ar.status
                    FROM approval_requests ar
                    WHERE ar.id = 
                    FOR UPDATE
                ''', request_id)
                
                if not request:
                    raise ApprovalStateError('Approval request not found', 'NOT_FOUND')
                
                if request['status'] != 'pending':
                    raise ApprovalStateError(
                        f'Cannot escalate request in {request["status"]} status',
                        'INVALID_STATUS'
                    )
                
                current_level = await conn.fetchrow('''
                    SELECT id FROM approval_levels
                    WHERE workflow_id =  AND level_order = 
                ''', request['workflow_id'], request['current_level'])
                
                # Record escalation
                await conn.execute('''
                    INSERT INTO approval_actions (
                        request_id, level_id, action, action_by,
                        escalated_to, escalation_reason
                    ) VALUES (, , 'escalated', , , )
                ''', request_id, current_level['id'], escalated_by, escalate_to, reason)
                
                return {
                    'status': 'escalated',
                    'escalated_to': str(escalate_to),
                    'message': 'Request escalated'
                }
    
    async def _can_user_approve_level(
        self,
        conn: asyncpg.Connection,
        user_id: UUID,
        level: asyncpg.Record,
        tenant_id: str
    ) -> bool:
        """Check if a user can approve a specific level."""
        approver_type = level['approver_type']
        
        if approver_type == 'user':
            return level['approver_user_id'] == user_id
        
        elif approver_type == 'any_of_users':
            user_ids = level['approver_user_ids'] or []
            return user_id in user_ids
        
        elif approver_type == 'role':
            # Check user's role
            user_role = await conn.fetchval('''
                SELECT r.code FROM user_tenant_roles utr
                JOIN roles r ON r.id = utr.role_id
                WHERE utr.user_id =  AND utr.tenant_id = 
            ''', user_id, tenant_id)
            return user_role == level['approver_role']
        
        elif approver_type == 'any_of_roles':
            user_role = await conn.fetchval('''
                SELECT r.code FROM user_tenant_roles utr
                JOIN roles r ON r.id = utr.role_id
                WHERE utr.user_id =  AND utr.tenant_id = 
            ''', user_id, tenant_id)
            roles = level['approver_roles'] or []
            return user_role in roles
        
        return False
    
    async def _update_document_status(
        self,
        conn: asyncpg.Connection,
        document_type: str,
        document_id: UUID,
        approval_result: str,
        tenant_id: str
    ):
        """
        Update the source document's status based on approval result.
        This is the integration point between approval workflow and document lifecycle.
        """
        # Map document types to tables and status fields
        document_tables = {
            'BILL': ('bills', 'status', 'approval_status'),
            'PURCHASE_ORDER': ('purchase_orders', 'status', 'approval_status'),
            'SALES_ORDER': ('sales_orders', 'status', 'approval_status'),
            'PAYMENT_REQUEST': ('payment_requests', 'status', None),
            'EXPENSE': ('expenses', 'status', None),
            'PAYROLL': ('payroll_runs', 'status', None),
        }
        
        if document_type not in document_tables:
            logger.warning(f"Unknown document type for status update: {document_type}")
            return
        
        table, status_field, approval_field = document_tables[document_type]
        
        if approval_result == 'approved':
            new_status = 'APPROVED'
        elif approval_result == 'rejected':
            new_status = 'REJECTED'
        else:
            return
        
        try:
            if approval_field:
                # Table has separate approval_status field
                await conn.execute(f'''
                    UPDATE {table}
                    SET {approval_field} = 
                    WHERE id =  AND tenant_id = 
                ''', new_status, document_id, tenant_id)
            else:
                # Table uses main status field
                await conn.execute(f'''
                    UPDATE {table}
                    SET {status_field} = 
                    WHERE id =  AND tenant_id = 
                ''', new_status, document_id, tenant_id)
                
            logger.info(f"Updated {document_type} {document_id} status to {new_status}")
            
        except Exception as e:
            logger.error(f"Failed to update document status: {e}")
            # Don't fail the approval - document update is secondary


# Singleton instance
_approval_state_service: Optional[ApprovalStateService] = None


def get_approval_state_service() -> ApprovalStateService:
    if _approval_state_service is None:
        raise RuntimeError("ApprovalStateService not initialized")
    return _approval_state_service


def init_approval_state_service(pool: asyncpg.Pool):
    global _approval_state_service
    _approval_state_service = ApprovalStateService(pool)
    logger.info("ApprovalStateService initialized")
