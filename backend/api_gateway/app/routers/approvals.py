"""
Approval Workflows Router - Alur Persetujuan

Endpoints for managing approval workflows and processing approval requests.
NO JOURNAL ENTRIES - This is a process control system.
Journal entries occur when documents are posted after approval.

Endpoints:
# Workflow Management
- GET    /approval-workflows                    - List workflows
- POST   /approval-workflows                    - Create workflow
- GET    /approval-workflows/{id}               - Detail with levels
- PATCH  /approval-workflows/{id}               - Update workflow
- DELETE /approval-workflows/{id}               - Deactivate
- POST   /approval-workflows/{id}/levels        - Add/update levels
- DELETE /approval-workflows/{id}/levels/{level_id} - Remove level

# Approval Requests
- GET    /approval-requests                     - List all requests
- GET    /approval-requests/pending             - My pending approvals
- GET    /approval-requests/submitted           - Requests I submitted
- GET    /approval-requests/{id}                - Detail with history
- POST   /approval-requests/{id}/approve        - Approve current level
- POST   /approval-requests/{id}/reject         - Reject
- POST   /approval-requests/{id}/cancel         - Cancel (by requester)
- POST   /approval-requests/{id}/escalate       - Manual escalate

# Delegation
- GET    /approval-delegates                    - List my delegates
- POST   /approval-delegates                    - Create delegation
- DELETE /approval-delegates/{id}               - Remove delegation

# Reports
- GET    /approval-requests/statistics          - Approval stats
- GET    /approval-requests/turnaround-time     - Average approval time
"""

from fastapi import APIRouter, HTTPException, Request, Query
from typing import Optional, Literal
from uuid import UUID
import logging
import asyncpg
from datetime import date, datetime
import uuid as uuid_module

from ..schemas.approvals import (
    CreateApprovalWorkflowRequest,
    UpdateApprovalWorkflowRequest,
    CreateApprovalLevelRequest,
    UpdateApprovalLevelRequest,
    ApproveRequestBody,
    RejectRequestBody,
    EscalateRequestBody,
    CancelRequestBody,
    CreateDelegationRequest,
    ApprovalWorkflowListResponse,
    ApprovalWorkflowDetailResponse,
    ApprovalRequestListResponse,
    ApprovalRequestDetailResponse,
    PendingApprovalsResponse,
    DelegationListResponse,
    ApprovalStatisticsResponse,
    SubmitApprovalResponse,
    ApprovalActionResponse,
    ApprovalResponse,
)
from ..config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

# Connection pool
_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
    """Get or create connection pool."""
    global _pool
    if _pool is None:
        db_config = settings.get_db_config()
        _pool = await asyncpg.create_pool(
            **db_config,
            min_size=2,
            max_size=10,
            command_timeout=30
        )
    return _pool


def get_user_context(request: Request) -> dict:
    """Extract and validate user context from request."""
    if not hasattr(request.state, 'user') or not request.state.user:
        raise HTTPException(status_code=401, detail="Authentication required")

    user = request.state.user
    tenant_id = user.get("tenant_id")
    user_id = user.get("user_id")

    if not tenant_id:
        raise HTTPException(status_code=401, detail="Invalid user context")

    return {
        "tenant_id": tenant_id,
        "user_id": UUID(user_id) if user_id else None,
        "user_email": user.get("email"),
        "user_role": user.get("role")
    }


# =============================================================================
# WORKFLOW MANAGEMENT
# =============================================================================

@router.get("/approval-workflows", response_model=ApprovalWorkflowListResponse)
async def list_workflows(
    request: Request,
    document_type: Optional[str] = Query(None),
    is_active: Optional[bool] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
):
    """List approval workflows."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            conditions = ["aw.tenant_id = $1"]
            params = [ctx["tenant_id"]]
            param_idx = 2

            if document_type:
                conditions.append(f"aw.document_type = ${param_idx}")
                params.append(document_type)
                param_idx += 1

            if is_active is not None:
                conditions.append(f"aw.is_active = ${param_idx}")
                params.append(is_active)
                param_idx += 1

            where_clause = " AND ".join(conditions)

            count_query = f"SELECT COUNT(*) FROM approval_workflows aw WHERE {where_clause}"
            total = await conn.fetchval(count_query, *params)

            query = f"""
                SELECT aw.id, aw.name, aw.description, aw.document_type,
                       aw.min_amount, aw.max_amount, aw.is_active, aw.is_sequential,
                       aw.created_at,
                       (SELECT COUNT(*) FROM approval_levels WHERE workflow_id = aw.id) as level_count
                FROM approval_workflows aw
                WHERE {where_clause}
                ORDER BY aw.document_type, aw.min_amount
                LIMIT ${param_idx} OFFSET ${param_idx + 1}
            """
            params.extend([limit, skip])

            rows = await conn.fetch(query, *params)

            items = [
                {
                    "id": str(row["id"]),
                    "name": row["name"],
                    "description": row["description"],
                    "document_type": row["document_type"],
                    "min_amount": row["min_amount"],
                    "max_amount": row["max_amount"],
                    "is_active": row["is_active"],
                    "is_sequential": row["is_sequential"],
                    "level_count": row["level_count"],
                    "created_at": row["created_at"].isoformat(),
                }
                for row in rows
            ]

            return {
                "items": items,
                "total": total,
                "has_more": (skip + limit) < total
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing workflows: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list workflows")


@router.post("/approval-workflows", response_model=ApprovalResponse, status_code=201)
async def create_workflow(request: Request, body: CreateApprovalWorkflowRequest):
    """Create a new approval workflow."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Check for duplicate name
            existing = await conn.fetchrow("""
                SELECT id FROM approval_workflows
                WHERE tenant_id = $1 AND name = $2
            """, ctx["tenant_id"], body.name)

            if existing:
                raise HTTPException(status_code=400, detail="Workflow with this name already exists")

            workflow_id = await conn.fetchval("""
                INSERT INTO approval_workflows (
                    tenant_id, name, description, document_type,
                    min_amount, max_amount, is_sequential, auto_approve_below_min,
                    created_by
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                RETURNING id
            """,
                ctx["tenant_id"],
                body.name,
                body.description,
                body.document_type,
                body.min_amount,
                body.max_amount,
                body.is_sequential,
                body.auto_approve_below_min,
                ctx["user_id"]
            )

            return {
                "success": True,
                "message": "Approval workflow created",
                "data": {"id": str(workflow_id)}
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating workflow: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create workflow")


@router.get("/approval-workflows/{workflow_id}", response_model=ApprovalWorkflowDetailResponse)
async def get_workflow(request: Request, workflow_id: UUID):
    """Get workflow detail with levels."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT * FROM approval_workflows
                WHERE id = $1 AND tenant_id = $2
            """, workflow_id, ctx["tenant_id"])

            if not row:
                raise HTTPException(status_code=404, detail="Workflow not found")

            # Get levels
            levels = await conn.fetch("""
                SELECT id, level_order, name, approver_type, approver_user_id,
                       approver_role, can_reject, auto_escalate_hours
                FROM approval_levels
                WHERE workflow_id = $1
                ORDER BY level_order
            """, workflow_id)

            return {
                "success": True,
                "data": {
                    "id": str(row["id"]),
                    "name": row["name"],
                    "description": row["description"],
                    "document_type": row["document_type"],
                    "min_amount": row["min_amount"],
                    "max_amount": row["max_amount"],
                    "is_active": row["is_active"],
                    "is_sequential": row["is_sequential"],
                    "auto_approve_below_min": row["auto_approve_below_min"],
                    "levels": [
                        {
                            "id": str(lv["id"]),
                            "level_order": lv["level_order"],
                            "name": lv["name"],
                            "approver_type": lv["approver_type"],
                            "approver_user_id": str(lv["approver_user_id"]) if lv["approver_user_id"] else None,
                            "approver_role": lv["approver_role"],
                            "can_reject": lv["can_reject"],
                            "auto_escalate_hours": lv["auto_escalate_hours"],
                        }
                        for lv in levels
                    ],
                    "created_at": row["created_at"].isoformat(),
                    "updated_at": row["updated_at"].isoformat(),
                    "created_by": str(row["created_by"]) if row["created_by"] else None,
                }
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting workflow: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get workflow")


@router.patch("/approval-workflows/{workflow_id}", response_model=ApprovalResponse)
async def update_workflow(
    request: Request,
    workflow_id: UUID,
    body: UpdateApprovalWorkflowRequest
):
    """Update an approval workflow."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            existing = await conn.fetchrow("""
                SELECT id FROM approval_workflows
                WHERE id = $1 AND tenant_id = $2
            """, workflow_id, ctx["tenant_id"])

            if not existing:
                raise HTTPException(status_code=404, detail="Workflow not found")

            updates = []
            params = []
            param_idx = 1

            if body.name is not None:
                updates.append(f"name = ${param_idx}")
                params.append(body.name)
                param_idx += 1

            if body.description is not None:
                updates.append(f"description = ${param_idx}")
                params.append(body.description)
                param_idx += 1

            if body.min_amount is not None:
                updates.append(f"min_amount = ${param_idx}")
                params.append(body.min_amount)
                param_idx += 1

            if body.max_amount is not None:
                updates.append(f"max_amount = ${param_idx}")
                params.append(body.max_amount)
                param_idx += 1

            if body.is_active is not None:
                updates.append(f"is_active = ${param_idx}")
                params.append(body.is_active)
                param_idx += 1

            if body.is_sequential is not None:
                updates.append(f"is_sequential = ${param_idx}")
                params.append(body.is_sequential)
                param_idx += 1

            if body.auto_approve_below_min is not None:
                updates.append(f"auto_approve_below_min = ${param_idx}")
                params.append(body.auto_approve_below_min)
                param_idx += 1

            if not updates:
                return {"success": True, "message": "No changes", "data": None}

            updates.append("updated_at = NOW()")
            params.extend([workflow_id, ctx["tenant_id"]])

            await conn.execute(f"""
                UPDATE approval_workflows
                SET {", ".join(updates)}
                WHERE id = ${param_idx} AND tenant_id = ${param_idx + 1}
            """, *params)

            return {
                "success": True,
                "message": "Workflow updated",
                "data": {"id": str(workflow_id)}
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating workflow: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update workflow")


@router.delete("/approval-workflows/{workflow_id}", response_model=ApprovalResponse)
async def deactivate_workflow(request: Request, workflow_id: UUID):
    """Deactivate an approval workflow."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            result = await conn.execute("""
                UPDATE approval_workflows
                SET is_active = false, updated_at = NOW()
                WHERE id = $1 AND tenant_id = $2
            """, workflow_id, ctx["tenant_id"])

            if result == "UPDATE 0":
                raise HTTPException(status_code=404, detail="Workflow not found")

            return {
                "success": True,
                "message": "Workflow deactivated",
                "data": {"id": str(workflow_id)}
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deactivating workflow: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to deactivate workflow")


# =============================================================================
# APPROVAL LEVELS
# =============================================================================

@router.post("/approval-workflows/{workflow_id}/levels", response_model=ApprovalResponse, status_code=201)
async def add_approval_level(
    request: Request,
    workflow_id: UUID,
    body: CreateApprovalLevelRequest
):
    """Add a level to a workflow."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Verify workflow exists
            workflow = await conn.fetchrow("""
                SELECT id FROM approval_workflows
                WHERE id = $1 AND tenant_id = $2
            """, workflow_id, ctx["tenant_id"])

            if not workflow:
                raise HTTPException(status_code=404, detail="Workflow not found")

            level_id = await conn.fetchval("""
                INSERT INTO approval_levels (
                    workflow_id, level_order, name, approver_type,
                    approver_user_id, approver_role, approver_user_ids, approver_roles,
                    auto_escalate_hours, escalate_to_user_id,
                    can_reject, notify_on_pending, notify_on_approved, notify_on_rejected
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
                ON CONFLICT (workflow_id, level_order) DO UPDATE SET
                    name = EXCLUDED.name,
                    approver_type = EXCLUDED.approver_type,
                    approver_user_id = EXCLUDED.approver_user_id,
                    approver_role = EXCLUDED.approver_role,
                    approver_user_ids = EXCLUDED.approver_user_ids,
                    approver_roles = EXCLUDED.approver_roles,
                    auto_escalate_hours = EXCLUDED.auto_escalate_hours,
                    escalate_to_user_id = EXCLUDED.escalate_to_user_id,
                    can_reject = EXCLUDED.can_reject,
                    notify_on_pending = EXCLUDED.notify_on_pending,
                    notify_on_approved = EXCLUDED.notify_on_approved,
                    notify_on_rejected = EXCLUDED.notify_on_rejected
                RETURNING id
            """,
                workflow_id,
                body.level_order,
                body.name,
                body.approver_type,
                body.approver_user_id,
                body.approver_role,
                body.approver_user_ids,
                body.approver_roles,
                body.auto_escalate_hours,
                body.escalate_to_user_id,
                body.can_reject,
                body.notify_on_pending,
                body.notify_on_approved,
                body.notify_on_rejected
            )

            return {
                "success": True,
                "message": "Approval level added/updated",
                "data": {"id": str(level_id)}
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error adding approval level: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to add approval level")


@router.delete("/approval-workflows/{workflow_id}/levels/{level_id}", response_model=ApprovalResponse)
async def remove_approval_level(
    request: Request,
    workflow_id: UUID,
    level_id: UUID
):
    """Remove a level from a workflow."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Verify workflow ownership
            workflow = await conn.fetchrow("""
                SELECT id FROM approval_workflows
                WHERE id = $1 AND tenant_id = $2
            """, workflow_id, ctx["tenant_id"])

            if not workflow:
                raise HTTPException(status_code=404, detail="Workflow not found")

            result = await conn.execute("""
                DELETE FROM approval_levels
                WHERE id = $1 AND workflow_id = $2
            """, level_id, workflow_id)

            if result == "DELETE 0":
                raise HTTPException(status_code=404, detail="Level not found")

            return {
                "success": True,
                "message": "Approval level removed",
                "data": {"id": str(level_id)}
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error removing approval level: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to remove approval level")


# =============================================================================
# APPROVAL REQUESTS
# =============================================================================

@router.get("/approval-requests", response_model=ApprovalRequestListResponse)
async def list_approval_requests(
    request: Request,
    status: Optional[Literal["pending", "approved", "rejected", "cancelled"]] = Query(None),
    document_type: Optional[str] = Query(None),
    from_date: Optional[date] = Query(None),
    to_date: Optional[date] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
):
    """List all approval requests."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            conditions = ["ar.tenant_id = $1"]
            params = [ctx["tenant_id"]]
            param_idx = 2

            if status:
                conditions.append(f"ar.status = ${param_idx}")
                params.append(status)
                param_idx += 1

            if document_type:
                conditions.append(f"ar.document_type = ${param_idx}")
                params.append(document_type)
                param_idx += 1

            if from_date:
                conditions.append(f"ar.requested_at::DATE >= ${param_idx}")
                params.append(from_date)
                param_idx += 1

            if to_date:
                conditions.append(f"ar.requested_at::DATE <= ${param_idx}")
                params.append(to_date)
                param_idx += 1

            where_clause = " AND ".join(conditions)

            count_query = f"SELECT COUNT(*) FROM approval_requests ar WHERE {where_clause}"
            total = await conn.fetchval(count_query, *params)

            query = f"""
                SELECT ar.id, aw.name as workflow_name, ar.document_type, ar.document_id,
                       ar.document_number, ar.document_amount, ar.current_level,
                       ar.status, ar.requested_by, ar.requested_at
                FROM approval_requests ar
                JOIN approval_workflows aw ON ar.workflow_id = aw.id
                WHERE {where_clause}
                ORDER BY ar.requested_at DESC
                LIMIT ${param_idx} OFFSET ${param_idx + 1}
            """
            params.extend([limit, skip])

            rows = await conn.fetch(query, *params)

            items = [
                {
                    "id": str(row["id"]),
                    "workflow_name": row["workflow_name"],
                    "document_type": row["document_type"],
                    "document_id": str(row["document_id"]),
                    "document_number": row["document_number"],
                    "document_amount": row["document_amount"],
                    "current_level": row["current_level"],
                    "status": row["status"],
                    "requested_by": str(row["requested_by"]),
                    "requested_at": row["requested_at"],
                }
                for row in rows
            ]

            return {
                "items": items,
                "total": total,
                "has_more": (skip + limit) < total
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing approval requests: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list approval requests")


@router.get("/approval-requests/pending", response_model=PendingApprovalsResponse)
async def get_pending_approvals(request: Request):
    """Get approvals pending for current user."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT * FROM get_pending_approvals_for_user($1, $2, $3)
            """, ctx["tenant_id"], ctx["user_id"], ctx.get("user_role", ""))

            items = []
            for row in rows:
                waiting_hours = None
                if row["requested_at"]:
                    delta = datetime.utcnow() - row["requested_at"].replace(tzinfo=None)
                    waiting_hours = round(delta.total_seconds() / 3600, 1)

                items.append({
                    "request_id": str(row["request_id"]),
                    "workflow_name": row["workflow_name"],
                    "document_type": row["document_type"],
                    "document_id": str(row["document_id"]),
                    "document_number": row["document_number"],
                    "document_amount": row["document_amount"],
                    "current_level": row["current_level"],
                    "level_name": row["level_name"],
                    "requested_by": str(row["requested_by"]),
                    "requested_at": row["requested_at"],
                    "waiting_hours": waiting_hours
                })

            return {
                "items": items,
                "total": len(items)
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting pending approvals: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get pending approvals")


@router.get("/approval-requests/submitted")
async def get_submitted_requests(
    request: Request,
    status: Optional[Literal["pending", "approved", "rejected", "cancelled"]] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
):
    """Get approval requests submitted by current user."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            conditions = ["ar.tenant_id = $1", "ar.requested_by = $2"]
            params = [ctx["tenant_id"], ctx["user_id"]]
            param_idx = 3

            if status:
                conditions.append(f"ar.status = ${param_idx}")
                params.append(status)
                param_idx += 1

            where_clause = " AND ".join(conditions)

            count_query = f"SELECT COUNT(*) FROM approval_requests ar WHERE {where_clause}"
            total = await conn.fetchval(count_query, *params)

            query = f"""
                SELECT ar.id, aw.name as workflow_name, ar.document_type, ar.document_id,
                       ar.document_number, ar.document_amount, ar.current_level,
                       ar.status, ar.requested_at, ar.completed_at
                FROM approval_requests ar
                JOIN approval_workflows aw ON ar.workflow_id = aw.id
                WHERE {where_clause}
                ORDER BY ar.requested_at DESC
                LIMIT ${param_idx} OFFSET ${param_idx + 1}
            """
            params.extend([limit, skip])

            rows = await conn.fetch(query, *params)

            return {
                "items": [
                    {
                        "id": str(row["id"]),
                        "workflow_name": row["workflow_name"],
                        "document_type": row["document_type"],
                        "document_id": str(row["document_id"]),
                        "document_number": row["document_number"],
                        "document_amount": row["document_amount"],
                        "current_level": row["current_level"],
                        "status": row["status"],
                        "requested_at": row["requested_at"].isoformat(),
                        "completed_at": row["completed_at"].isoformat() if row["completed_at"] else None
                    }
                    for row in rows
                ],
                "total": total,
                "has_more": (skip + limit) < total
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting submitted requests: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get submitted requests")


@router.get("/approval-requests/{request_id}", response_model=ApprovalRequestDetailResponse)
async def get_approval_request(request: Request, request_id: UUID):
    """Get approval request detail with history."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT * FROM get_approval_request_detail($1)
            """, request_id)

            if not row:
                raise HTTPException(status_code=404, detail="Approval request not found")

            # Get total levels
            total_levels = await conn.fetchval("""
                SELECT COUNT(*) FROM approval_levels WHERE workflow_id = $1
            """, row["workflow_id"])

            actions = row["actions"] or []

            return {
                "success": True,
                "data": {
                    "id": str(row["request_id"]),
                    "workflow_id": str(row["workflow_id"]),
                    "workflow_name": row["workflow_name"],
                    "document_type": row["document_type"],
                    "document_id": str(row["document_id"]),
                    "document_number": row["document_number"],
                    "document_amount": row["document_amount"],
                    "current_level": row["current_level"],
                    "total_levels": total_levels,
                    "status": row["status"],
                    "requested_by": str(row["requested_by"]),
                    "requested_at": row["requested_at"],
                    "completed_at": row["completed_at"],
                    "notes": None,
                    "actions": [
                        {
                            "level_order": a["level_order"],
                            "level_name": a["level_name"],
                            "action": a["action"],
                            "action_by": str(a["action_by"]),
                            "action_at": a["action_at"],
                            "comments": a.get("comments")
                        }
                        for a in actions
                    ]
                }
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting approval request: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get approval request")


# =============================================================================
# APPROVAL ACTIONS
# =============================================================================

@router.post("/approval-requests/{request_id}/approve", response_model=ApprovalActionResponse)
async def approve_request(request: Request, request_id: UUID, body: ApproveRequestBody):
    """Approve current level of an approval request."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                # Get request
                ar = await conn.fetchrow("""
                    SELECT ar.*, aw.is_sequential
                    FROM approval_requests ar
                    JOIN approval_workflows aw ON ar.workflow_id = aw.id
                    WHERE ar.id = $1 AND ar.tenant_id = $2 AND ar.status = 'pending'
                """, request_id, ctx["tenant_id"])

                if not ar:
                    raise HTTPException(status_code=404, detail="Pending approval request not found")

                # Get current level
                level = await conn.fetchrow("""
                    SELECT * FROM approval_levels
                    WHERE workflow_id = $1 AND level_order = $2
                """, ar["workflow_id"], ar["current_level"])

                if not level:
                    raise HTTPException(status_code=400, detail="Approval level not found")

                # Check if user can approve
                can_approve = await conn.fetchval("""
                    SELECT can_user_approve($1, $2, $3)
                """, level["id"], ctx["user_id"], ctx.get("user_role", ""))

                if not can_approve:
                    raise HTTPException(status_code=403, detail="Not authorized to approve")

                # Record action
                await conn.execute("""
                    INSERT INTO approval_actions (request_id, level_id, action, action_by, comments)
                    VALUES ($1, $2, 'approved', $3, $4)
                """, request_id, level["id"], ctx["user_id"], body.comments)

                # Check if more levels
                next_level = await conn.fetchrow("""
                    SELECT * FROM approval_levels
                    WHERE workflow_id = $1 AND level_order = $2
                """, ar["workflow_id"], ar["current_level"] + 1)

                if next_level:
                    # Move to next level
                    await conn.execute("""
                        UPDATE approval_requests
                        SET current_level = current_level + 1
                        WHERE id = $1
                    """, request_id)

                    return {
                        "success": True,
                        "status": "pending",
                        "message": f"Approved. Moved to level {ar['current_level'] + 1}",
                        "next_level": ar["current_level"] + 1
                    }
                else:
                    # All levels approved
                    await conn.execute("""
                        UPDATE approval_requests
                        SET status = 'approved', completed_at = NOW()
                        WHERE id = $1
                    """, request_id)

                    # Update document status
                    await update_document_approval_status(
                        conn, ar["document_type"], ar["document_id"], "approved", request_id
                    )

                    return {
                        "success": True,
                        "status": "approved",
                        "message": "Fully approved",
                        "next_level": None
                    }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error approving request: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to approve request")


@router.post("/approval-requests/{request_id}/reject", response_model=ApprovalActionResponse)
async def reject_request(request: Request, request_id: UUID, body: RejectRequestBody):
    """Reject an approval request."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                # Get request
                ar = await conn.fetchrow("""
                    SELECT ar.* FROM approval_requests ar
                    WHERE ar.id = $1 AND ar.tenant_id = $2 AND ar.status = 'pending'
                """, request_id, ctx["tenant_id"])

                if not ar:
                    raise HTTPException(status_code=404, detail="Pending approval request not found")

                # Get current level
                level = await conn.fetchrow("""
                    SELECT * FROM approval_levels
                    WHERE workflow_id = $1 AND level_order = $2
                """, ar["workflow_id"], ar["current_level"])

                if not level or not level["can_reject"]:
                    raise HTTPException(status_code=400, detail="This level cannot reject")

                # Check if user can approve/reject
                can_approve = await conn.fetchval("""
                    SELECT can_user_approve($1, $2, $3)
                """, level["id"], ctx["user_id"], ctx.get("user_role", ""))

                if not can_approve:
                    raise HTTPException(status_code=403, detail="Not authorized to reject")

                # Record action
                await conn.execute("""
                    INSERT INTO approval_actions (request_id, level_id, action, action_by, comments)
                    VALUES ($1, $2, 'rejected', $3, $4)
                """, request_id, level["id"], ctx["user_id"], body.comments)

                # Update request
                await conn.execute("""
                    UPDATE approval_requests
                    SET status = 'rejected', completed_at = NOW()
                    WHERE id = $1
                """, request_id)

                # Update document status
                await update_document_approval_status(
                    conn, ar["document_type"], ar["document_id"], "rejected", request_id
                )

                return {
                    "success": True,
                    "status": "rejected",
                    "message": "Request rejected",
                    "next_level": None
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error rejecting request: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to reject request")


@router.post("/approval-requests/{request_id}/cancel", response_model=ApprovalActionResponse)
async def cancel_request(request: Request, request_id: UUID, body: CancelRequestBody):
    """Cancel an approval request (by requester only)."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                ar = await conn.fetchrow("""
                    SELECT * FROM approval_requests
                    WHERE id = $1 AND tenant_id = $2 AND status = 'pending'
                """, request_id, ctx["tenant_id"])

                if not ar:
                    raise HTTPException(status_code=404, detail="Pending approval request not found")

                if ar["requested_by"] != ctx["user_id"]:
                    raise HTTPException(status_code=403, detail="Only requester can cancel")

                await conn.execute("""
                    UPDATE approval_requests
                    SET status = 'cancelled', completed_at = NOW(), notes = $2
                    WHERE id = $1
                """, request_id, body.reason)

                # Update document status
                await update_document_approval_status(
                    conn, ar["document_type"], ar["document_id"], "cancelled", request_id
                )

                return {
                    "success": True,
                    "status": "cancelled",
                    "message": "Request cancelled",
                    "next_level": None
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error cancelling request: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to cancel request")


async def update_document_approval_status(conn, document_type: str, document_id: UUID, status: str, request_id: UUID):
    """Update the approval status on the source document."""
    table_map = {
        "purchase_order": "purchase_orders",
        "bill": "bills",
        "sales_order": "sales_orders",
    }

    table = table_map.get(document_type)
    if table:
        await conn.execute(f"""
            UPDATE {table}
            SET approval_status = $1, approval_request_id = $2
            WHERE id = $3
        """, status, request_id, document_id)


# =============================================================================
# DELEGATION
# =============================================================================

@router.get("/approval-delegates", response_model=DelegationListResponse)
async def list_delegations(request: Request):
    """List approval delegations for current user."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT id, approver_user_id, delegate_user_id, start_date, end_date,
                       workflow_ids, is_active, created_at
                FROM approval_delegates
                WHERE tenant_id = $1 AND approver_user_id = $2
                ORDER BY start_date DESC
            """, ctx["tenant_id"], ctx["user_id"])

            return {
                "items": [
                    {
                        "id": str(row["id"]),
                        "approver_user_id": str(row["approver_user_id"]),
                        "delegate_user_id": str(row["delegate_user_id"]),
                        "start_date": row["start_date"],
                        "end_date": row["end_date"],
                        "workflow_ids": [str(w) for w in row["workflow_ids"]] if row["workflow_ids"] else None,
                        "is_active": row["is_active"],
                        "created_at": row["created_at"].isoformat()
                    }
                    for row in rows
                ],
                "total": len(rows)
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing delegations: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list delegations")


@router.post("/approval-delegates", response_model=ApprovalResponse, status_code=201)
async def create_delegation(request: Request, body: CreateDelegationRequest):
    """Create an approval delegation."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            delegation_id = await conn.fetchval("""
                INSERT INTO approval_delegates (
                    tenant_id, approver_user_id, delegate_user_id,
                    start_date, end_date, workflow_ids, created_by
                ) VALUES ($1, $2, $3, $4, $5, $6, $7)
                RETURNING id
            """,
                ctx["tenant_id"],
                ctx["user_id"],
                body.delegate_user_id,
                body.start_date,
                body.end_date,
                body.workflow_ids,
                ctx["user_id"]
            )

            return {
                "success": True,
                "message": "Delegation created",
                "data": {"id": str(delegation_id)}
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating delegation: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create delegation")


@router.delete("/approval-delegates/{delegation_id}", response_model=ApprovalResponse)
async def remove_delegation(request: Request, delegation_id: UUID):
    """Remove an approval delegation."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            result = await conn.execute("""
                UPDATE approval_delegates
                SET is_active = false
                WHERE id = $1 AND tenant_id = $2 AND approver_user_id = $3
            """, delegation_id, ctx["tenant_id"], ctx["user_id"])

            if result == "UPDATE 0":
                raise HTTPException(status_code=404, detail="Delegation not found")

            return {
                "success": True,
                "message": "Delegation removed",
                "data": {"id": str(delegation_id)}
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error removing delegation: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to remove delegation")


# =============================================================================
# REPORTS
# =============================================================================

@router.get("/approval-requests/statistics", response_model=ApprovalStatisticsResponse)
async def get_approval_statistics(
    request: Request,
    from_date: Optional[date] = Query(None),
    to_date: Optional[date] = Query(None),
):
    """Get approval statistics."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT * FROM get_approval_statistics($1, $2, $3)
            """, ctx["tenant_id"], from_date, to_date)

            return {
                "success": True,
                "data": [
                    {
                        "document_type": row["document_type"],
                        "total_requests": row["total_requests"],
                        "pending_count": row["pending_count"],
                        "approved_count": row["approved_count"],
                        "rejected_count": row["rejected_count"],
                        "cancelled_count": row["cancelled_count"],
                        "avg_approval_hours": float(row["avg_approval_hours"]) if row["avg_approval_hours"] else None
                    }
                    for row in rows
                ],
                "period": {
                    "from_date": from_date.isoformat() if from_date else None,
                    "to_date": to_date.isoformat() if to_date else None
                }
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting approval statistics: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get approval statistics")


@router.get("/approval-requests/turnaround-time")
async def get_turnaround_time(
    request: Request,
    from_date: Optional[date] = Query(None),
    to_date: Optional[date] = Query(None),
):
    """Get average approval turnaround time by workflow."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT aw.name as workflow_name, aw.document_type,
                       AVG(EXTRACT(EPOCH FROM (ar.completed_at - ar.requested_at)) / 3600) as avg_hours,
                       MIN(EXTRACT(EPOCH FROM (ar.completed_at - ar.requested_at)) / 3600) as min_hours,
                       MAX(EXTRACT(EPOCH FROM (ar.completed_at - ar.requested_at)) / 3600) as max_hours,
                       COUNT(*)::INTEGER as total_completed
                FROM approval_requests ar
                JOIN approval_workflows aw ON ar.workflow_id = aw.id
                WHERE ar.tenant_id = $1
                AND ar.status = 'approved'
                AND ar.completed_at IS NOT NULL
                AND ($2::DATE IS NULL OR ar.requested_at::DATE >= $2)
                AND ($3::DATE IS NULL OR ar.requested_at::DATE <= $3)
                GROUP BY aw.name, aw.document_type
                ORDER BY avg_hours DESC
            """, ctx["tenant_id"], from_date, to_date)

            return {
                "success": True,
                "data": [
                    {
                        "workflow_name": row["workflow_name"],
                        "document_type": row["document_type"],
                        "avg_hours": round(row["avg_hours"], 1) if row["avg_hours"] else 0,
                        "min_hours": round(row["min_hours"], 1) if row["min_hours"] else 0,
                        "max_hours": round(row["max_hours"], 1) if row["max_hours"] else 0,
                        "total_completed": row["total_completed"]
                    }
                    for row in rows
                ]
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting turnaround time: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get turnaround time")
