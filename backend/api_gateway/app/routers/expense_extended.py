"""
Expense Extended Router - Expense Claims, Policies, and Recurring Expenses

Additional endpoints for the expense module.
"""

from fastapi import APIRouter, HTTPException, Request, Query
from typing import Optional
import logging
import asyncpg

from ..config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

# Connection pool
_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        db_config = settings.get_db_config()
        _pool = await asyncpg.create_pool(
            **db_config, min_size=2, max_size=10, command_timeout=30
        )
    return _pool


def get_user_context(request: Request) -> dict:
    if not hasattr(request.state, "user") or not request.state.user:
        raise HTTPException(status_code=401, detail="Authentication required")
    user = request.state.user
    tenant_id = user.get("tenant_id")
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Invalid user context")
    return {"tenant_id": tenant_id, "user_id": user.get("user_id")}


# =============================================================================
# EXPENSE POLICY
# =============================================================================


@router.get("/expense-policy")
async def get_expense_policy(request: Request):
    """Get expense policy settings."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM expense_policies
                WHERE tenant_id = $1
            """,
                ctx["tenant_id"],
            )

            if row:
                policy = {
                    "id": str(row["id"]),
                    "require_receipt_above": row.get("require_receipt_above", 100000),
                    "auto_approve_below": row.get("auto_approve_below", 0),
                    "max_claim_amount": row.get("max_claim_amount", 0),
                    "expense_categories": row.get("expense_categories", []),
                    "approval_workflow": row.get("approval_workflow", "single"),
                }
            else:
                # Return default policy
                policy = {
                    "id": None,
                    "require_receipt_above": 100000,
                    "auto_approve_below": 0,
                    "max_claim_amount": 0,
                    "expense_categories": [
                        "Travel",
                        "Meals",
                        "Office Supplies",
                        "Transport",
                        "Other",
                    ],
                    "approval_workflow": "single",
                }

            return {"success": True, "policy": policy}
    except Exception as e:
        logger.error(f"Error getting expense policy: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get policy")


@router.post("/expense-policy")
async def create_expense_policy(request: Request):
    """Create or update expense policy settings."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        body = await request.json()

        async with pool.acquire() as conn:
            existing = await conn.fetchrow(
                "SELECT id FROM expense_policies WHERE tenant_id = $1", ctx["tenant_id"]
            )

            if existing:
                await conn.execute(
                    """
                    UPDATE expense_policies SET
                        require_receipt_above = $1,
                        auto_approve_below = $2,
                        max_claim_amount = $3,
                        expense_categories = $4,
                        approval_workflow = $5,
                        updated_at = NOW()
                    WHERE tenant_id = $6
                """,
                    body.get("require_receipt_above", 100000),
                    body.get("auto_approve_below", 0),
                    body.get("max_claim_amount", 0),
                    body.get("expense_categories", []),
                    body.get("approval_workflow", "single"),
                    ctx["tenant_id"],
                )
            else:
                import uuid as uuid_mod

                await conn.execute(
                    """
                    INSERT INTO expense_policies (
                        id, tenant_id, require_receipt_above, auto_approve_below,
                        max_claim_amount, expense_categories, approval_workflow
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7)
                """,
                    str(uuid_mod.uuid4()),
                    ctx["tenant_id"],
                    body.get("require_receipt_above", 100000),
                    body.get("auto_approve_below", 0),
                    body.get("max_claim_amount", 0),
                    body.get("expense_categories", []),
                    body.get("approval_workflow", "single"),
                )

            return {"success": True, "message": "Policy saved"}
    except Exception as e:
        logger.error(f"Error saving policy: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to save policy")


@router.put("/expense-policy")
async def update_expense_policy(request: Request):
    """Alias for POST - update expense policy."""
    return await create_expense_policy(request)


# =============================================================================
# EXPENSE CLAIMS
# =============================================================================


@router.get("/expense-claims")
async def list_expense_claims(
    request: Request,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    status: Optional[str] = Query(None),
    employee_id: Optional[str] = Query(None),
):
    """List expense claims."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        offset = (page - 1) * limit

        async with pool.acquire() as conn:
            # Build filters
            filters = ["tenant_id = $1"]
            params = [ctx["tenant_id"]]
            idx = 2

            if status:
                filters.append(f"status = ${idx}")
                params.append(status)
                idx += 1

            if employee_id:
                filters.append(f"employee_id::text = ${idx}")
                params.append(employee_id)
                idx += 1

            where = " AND ".join(filters)
            params.extend([limit, offset])

            rows = await conn.fetch(
                f"""
                SELECT ec.*, e.name as employee_name
                FROM expense_claims ec
                LEFT JOIN employees e ON e.id = ec.employee_id
                WHERE {where}
                ORDER BY ec.created_at DESC
                LIMIT ${idx} OFFSET ${idx + 1}
            """,
                *params,
            )

            count_params = params[:-2]
            total = await conn.fetchval(
                f"""
                SELECT COUNT(*) FROM expense_claims WHERE {where}
            """,
                *count_params,
            )

            claims = [
                {
                    "id": str(row["id"]),
                    "claim_number": row.get("claim_number"),
                    "employee_id": str(row["employee_id"])
                    if row.get("employee_id")
                    else None,
                    "employee_name": row.get("employee_name"),
                    "description": row.get("description"),
                    "total_amount": row.get("total_amount", 0),
                    "status": row.get("status", "draft"),
                    "submitted_at": row["submitted_at"].isoformat()
                    if row.get("submitted_at")
                    else None,
                    "approved_at": row["approved_at"].isoformat()
                    if row.get("approved_at")
                    else None,
                    "created_at": row["created_at"].isoformat()
                    if row.get("created_at")
                    else None,
                }
                for row in rows
            ]

            return {"success": True, "claims": claims, "total": total, "page": page}
    except Exception as e:
        logger.error(f"Error listing claims: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list claims")


@router.get("/expense-claims/summary")
async def get_expense_claims_summary(request: Request):
    """Get expense claims summary statistics."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            summary = await conn.fetchrow(
                """
                SELECT
                    COUNT(*) FILTER (WHERE status = 'draft') as draft_count,
                    COUNT(*) FILTER (WHERE status = 'pending') as pending_count,
                    COUNT(*) FILTER (WHERE status = 'approved') as approved_count,
                    COUNT(*) FILTER (WHERE status = 'rejected') as rejected_count,
                    COUNT(*) FILTER (WHERE status = 'reimbursed') as reimbursed_count,
                    COALESCE(SUM(total_amount) FILTER (WHERE status = 'pending'), 0) as pending_amount,
                    COALESCE(SUM(total_amount) FILTER (WHERE status = 'approved'), 0) as approved_amount
                FROM expense_claims
                WHERE tenant_id = $1
            """,
                ctx["tenant_id"],
            )

            return {
                "success": True,
                "summary": {
                    "draft": summary["draft_count"] or 0,
                    "pending": summary["pending_count"] or 0,
                    "approved": summary["approved_count"] or 0,
                    "rejected": summary["rejected_count"] or 0,
                    "reimbursed": summary["reimbursed_count"] or 0,
                    "pending_amount": summary["pending_amount"] or 0,
                    "approved_amount": summary["approved_amount"] or 0,
                },
            }
    except Exception as e:
        logger.error(f"Error getting summary: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get summary")


@router.get("/expense-claims/approval-stats")
async def get_approval_stats(request: Request):
    """Get approval statistics for the current user."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            stats = await conn.fetchrow(
                """
                SELECT
                    COUNT(*) FILTER (WHERE status = 'pending') as awaiting_approval,
                    COUNT(*) FILTER (WHERE status = 'approved' AND approved_at > CURRENT_DATE - INTERVAL '30 days') as recently_approved,
                    COUNT(*) FILTER (WHERE status = 'rejected' AND updated_at > CURRENT_DATE - INTERVAL '30 days') as recently_rejected
                FROM expense_claims
                WHERE tenant_id = $1
            """,
                ctx["tenant_id"],
            )

            return {
                "success": True,
                "stats": {
                    "awaiting_approval": stats["awaiting_approval"] or 0,
                    "recently_approved": stats["recently_approved"] or 0,
                    "recently_rejected": stats["recently_rejected"] or 0,
                },
            }
    except Exception as e:
        logger.error(f"Error getting stats: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get stats")


@router.get("/expense-claims/{claim_id}")
async def get_expense_claim(request: Request, claim_id: str):
    """Get expense claim detail."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT ec.*, e.name as employee_name
                FROM expense_claims ec
                LEFT JOIN employees e ON e.id = ec.employee_id
                WHERE ec.id = $1 AND ec.tenant_id = $2
            """,
                claim_id,
                ctx["tenant_id"],
            )

            if not row:
                raise HTTPException(status_code=404, detail="Claim not found")

            # Get line items
            lines = await conn.fetch(
                """
                SELECT * FROM expense_claim_lines
                WHERE claim_id = $1
                ORDER BY created_at ASC
            """,
                claim_id,
            )

            return {
                "success": True,
                "claim": {
                    "id": str(row["id"]),
                    "claim_number": row.get("claim_number"),
                    "employee_id": str(row["employee_id"])
                    if row.get("employee_id")
                    else None,
                    "employee_name": row.get("employee_name"),
                    "description": row.get("description"),
                    "total_amount": row.get("total_amount", 0),
                    "status": row.get("status", "draft"),
                    "submitted_at": row["submitted_at"].isoformat()
                    if row.get("submitted_at")
                    else None,
                    "approved_at": row["approved_at"].isoformat()
                    if row.get("approved_at")
                    else None,
                    "created_at": row["created_at"].isoformat()
                    if row.get("created_at")
                    else None,
                    "lines": [
                        {
                            "id": str(line["id"]),
                            "description": line.get("description"),
                            "category": line.get("category"),
                            "amount": line.get("amount", 0),
                            "receipt_url": line.get("receipt_url"),
                            "expense_date": line["expense_date"].isoformat()
                            if line.get("expense_date")
                            else None,
                        }
                        for line in lines
                    ],
                },
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting claim: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get claim")


@router.post("/expense-claims")
async def create_expense_claim(request: Request):
    """Create a new expense claim."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        body = await request.json()

        async with pool.acquire() as conn:
            import uuid as uuid_mod

            claim_id = str(uuid_mod.uuid4())

            # Generate claim number
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM expense_claims WHERE tenant_id = $1",
                ctx["tenant_id"],
            )
            claim_number = f"EC-{count + 1:05d}"

            await conn.execute(
                """
                INSERT INTO expense_claims (
                    id, tenant_id, claim_number, employee_id, description, total_amount, status
                ) VALUES ($1, $2, $3, $4, $5, $6, 'draft')
            """,
                claim_id,
                ctx["tenant_id"],
                claim_number,
                body.get("employee_id"),
                body.get("description"),
                body.get("total_amount", 0),
            )

            # Insert line items
            for line in body.get("lines", []):
                await conn.execute(
                    """
                    INSERT INTO expense_claim_lines (
                        id, claim_id, description, category, amount, expense_date, receipt_url
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7)
                """,
                    str(uuid_mod.uuid4()),
                    claim_id,
                    line.get("description"),
                    line.get("category"),
                    line.get("amount", 0),
                    line.get("expense_date"),
                    line.get("receipt_url"),
                )

            return {
                "success": True,
                "data": {"id": claim_id, "claim_number": claim_number},
            }
    except Exception as e:
        logger.error(f"Error creating claim: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create claim")


@router.delete("/expense-claims/{claim_id}")
async def delete_expense_claim(request: Request, claim_id: str):
    """Delete an expense claim (only if draft status)."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT status FROM expense_claims WHERE id = $1 AND tenant_id = $2",
                claim_id,
                ctx["tenant_id"],
            )
            if not row:
                raise HTTPException(status_code=404, detail="Claim not found")
            if row["status"] != "draft":
                raise HTTPException(
                    status_code=400, detail="Only draft claims can be deleted"
                )

            await conn.execute(
                "DELETE FROM expense_claim_lines WHERE claim_id = $1", claim_id
            )
            await conn.execute(
                "DELETE FROM expense_claims WHERE id = $1 AND tenant_id = $2",
                claim_id,
                ctx["tenant_id"],
            )

            return {"success": True, "message": "Claim deleted"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting claim: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to delete claim")


@router.post("/expense-claims/{claim_id}/submit")
async def submit_expense_claim(request: Request, claim_id: str):
    """Submit an expense claim for approval."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE expense_claims
                SET status = 'pending', submitted_at = NOW(), updated_at = NOW()
                WHERE id = $1 AND tenant_id = $2 AND status = 'draft'
            """,
                claim_id,
                ctx["tenant_id"],
            )

            if result == "UPDATE 0":
                raise HTTPException(status_code=400, detail="Cannot submit claim")

            return {"success": True, "message": "Claim submitted"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error submitting claim: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to submit claim")


@router.post("/expense-claims/{claim_id}/approve")
async def approve_expense_claim(request: Request, claim_id: str):
    """Approve an expense claim."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        body = await request.json()

        async with pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE expense_claims
                SET status = 'approved', approved_at = NOW(), approved_by = $1,
                    approval_notes = $2, updated_at = NOW()
                WHERE id = $3 AND tenant_id = $4 AND status = 'pending'
            """,
                ctx["user_id"],
                body.get("notes"),
                claim_id,
                ctx["tenant_id"],
            )

            if result == "UPDATE 0":
                raise HTTPException(status_code=400, detail="Cannot approve claim")

            return {"success": True, "message": "Claim approved"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error approving claim: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to approve claim")


@router.post("/expense-claims/{claim_id}/reject")
async def reject_expense_claim(request: Request, claim_id: str):
    """Reject an expense claim."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        body = await request.json()

        async with pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE expense_claims
                SET status = 'rejected', rejection_reason = $1, updated_at = NOW()
                WHERE id = $2 AND tenant_id = $3 AND status = 'pending'
            """,
                body.get("reason"),
                claim_id,
                ctx["tenant_id"],
            )

            if result == "UPDATE 0":
                raise HTTPException(status_code=400, detail="Cannot reject claim")

            return {"success": True, "message": "Claim rejected"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error rejecting claim: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to reject claim")


@router.post("/expense-claims/{claim_id}/reimburse")
async def reimburse_expense_claim(request: Request, claim_id: str):
    """Mark an expense claim as reimbursed."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE expense_claims
                SET status = 'reimbursed', reimbursed_at = NOW(), updated_at = NOW()
                WHERE id = $1 AND tenant_id = $2 AND status = 'approved'
            """,
                claim_id,
                ctx["tenant_id"],
            )

            if result == "UPDATE 0":
                raise HTTPException(status_code=400, detail="Cannot reimburse claim")

            return {"success": True, "message": "Claim reimbursed"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error reimbursing claim: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to reimburse claim")


# =============================================================================
# RECURRING EXPENSES
# =============================================================================


@router.get("/recurring-expenses")
async def list_recurring_expenses(
    request: Request,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    is_active: Optional[bool] = Query(None),
):
    """List recurring expense templates."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        offset = (page - 1) * limit

        async with pool.acquire() as conn:
            filters = ["tenant_id = $1"]
            params = [ctx["tenant_id"]]
            idx = 2

            if is_active is not None:
                filters.append(f"is_active = ${idx}")
                params.append(is_active)
                idx += 1

            where = " AND ".join(filters)
            params.extend([limit, offset])

            rows = await conn.fetch(
                f"""
                SELECT *
                FROM recurring_expenses
                WHERE {where}
                ORDER BY created_at DESC
                LIMIT ${idx} OFFSET ${idx + 1}
            """,
                *params,
            )

            count_params = params[:-2]
            total = await conn.fetchval(
                f"""
                SELECT COUNT(*) FROM recurring_expenses WHERE {where}
            """,
                *count_params,
            )

            templates = [
                {
                    "id": str(row["id"]),
                    "name": row.get("name"),
                    "description": row.get("description"),
                    "amount": row.get("amount", 0),
                    "frequency": row.get("frequency", "monthly"),
                    "next_date": row["next_date"].isoformat()
                    if row.get("next_date")
                    else None,
                    "is_active": row.get("is_active", True),
                    "category": row.get("category"),
                    "vendor_id": str(row["vendor_id"])
                    if row.get("vendor_id")
                    else None,
                }
                for row in rows
            ]

            return {
                "success": True,
                "templates": templates,
                "total": total,
                "page": page,
            }
    except Exception as e:
        logger.error(f"Error listing recurring expenses: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list recurring expenses")


@router.get("/recurring-expenses/summary")
async def get_recurring_expenses_summary(request: Request):
    """Get recurring expenses summary."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            summary = await conn.fetchrow(
                """
                SELECT
                    COUNT(*) FILTER (WHERE is_active = true) as active_count,
                    COUNT(*) FILTER (WHERE is_active = false) as inactive_count,
                    COALESCE(SUM(amount) FILTER (WHERE is_active = true), 0) as monthly_total
                FROM recurring_expenses
                WHERE tenant_id = $1
            """,
                ctx["tenant_id"],
            )

            return {
                "success": True,
                "summary": {
                    "active": summary["active_count"] or 0,
                    "inactive": summary["inactive_count"] or 0,
                    "monthly_total": summary["monthly_total"] or 0,
                },
            }
    except Exception as e:
        logger.error(f"Error getting summary: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get summary")


@router.get("/recurring-expenses/{template_id}")
async def get_recurring_expense(request: Request, template_id: str):
    """Get recurring expense detail."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM recurring_expenses
                WHERE id = $1 AND tenant_id = $2
            """,
                template_id,
                ctx["tenant_id"],
            )

            if not row:
                raise HTTPException(status_code=404, detail="Template not found")

            return {
                "success": True,
                "template": {
                    "id": str(row["id"]),
                    "name": row.get("name"),
                    "description": row.get("description"),
                    "amount": row.get("amount", 0),
                    "frequency": row.get("frequency", "monthly"),
                    "next_date": row["next_date"].isoformat()
                    if row.get("next_date")
                    else None,
                    "is_active": row.get("is_active", True),
                    "category": row.get("category"),
                    "vendor_id": str(row["vendor_id"])
                    if row.get("vendor_id")
                    else None,
                    "payment_account_id": str(row["payment_account_id"])
                    if row.get("payment_account_id")
                    else None,
                },
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting template: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get template")


@router.post("/recurring-expenses")
async def create_recurring_expense(request: Request):
    """Create a recurring expense template."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        body = await request.json()

        async with pool.acquire() as conn:
            import uuid as uuid_mod

            template_id = str(uuid_mod.uuid4())

            await conn.execute(
                """
                INSERT INTO recurring_expenses (
                    id, tenant_id, name, description, amount, frequency,
                    next_date, category, vendor_id, payment_account_id, is_active
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, true)
            """,
                template_id,
                ctx["tenant_id"],
                body.get("name"),
                body.get("description"),
                body.get("amount", 0),
                body.get("frequency", "monthly"),
                body.get("next_date"),
                body.get("category"),
                body.get("vendor_id"),
                body.get("payment_account_id"),
            )

            return {"success": True, "data": {"id": template_id}}
    except Exception as e:
        logger.error(f"Error creating template: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create template")


@router.put("/recurring-expenses/{template_id}")
async def update_recurring_expense(request: Request, template_id: str):
    """Update a recurring expense template."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        body = await request.json()

        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE recurring_expenses SET
                    name = COALESCE($1, name),
                    description = COALESCE($2, description),
                    amount = COALESCE($3, amount),
                    frequency = COALESCE($4, frequency),
                    next_date = COALESCE($5, next_date),
                    category = COALESCE($6, category),
                    vendor_id = COALESCE($7, vendor_id),
                    payment_account_id = COALESCE($8, payment_account_id),
                    updated_at = NOW()
                WHERE id = $9 AND tenant_id = $10
            """,
                body.get("name"),
                body.get("description"),
                body.get("amount"),
                body.get("frequency"),
                body.get("next_date"),
                body.get("category"),
                body.get("vendor_id"),
                body.get("payment_account_id"),
                template_id,
                ctx["tenant_id"],
            )

            return {"success": True, "message": "Template updated"}
    except Exception as e:
        logger.error(f"Error updating template: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update template")


@router.delete("/recurring-expenses/{template_id}")
async def delete_recurring_expense(request: Request, template_id: str):
    """Delete a recurring expense template."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM recurring_expenses WHERE id = $1 AND tenant_id = $2",
                template_id,
                ctx["tenant_id"],
            )

            return {"success": True, "message": "Template deleted"}
    except Exception as e:
        logger.error(f"Error deleting template: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to delete template")


@router.post("/recurring-expenses/{template_id}/toggle")
async def toggle_recurring_expense(request: Request, template_id: str):
    """Toggle active status of a recurring expense template."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        body = await request.json()

        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE recurring_expenses
                SET is_active = $1, updated_at = NOW()
                WHERE id = $2 AND tenant_id = $3
            """,
                body.get("is_active", False),
                template_id,
                ctx["tenant_id"],
            )

            return {"success": True, "message": "Status updated"}
    except Exception as e:
        logger.error(f"Error toggling template: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to toggle status")


# =============================================================================
# EMPLOYEES
# =============================================================================


@router.get("/employees")
async def list_employees(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    is_active: Optional[bool] = Query(None),
):
    """List employees."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            filter_clause = ""
            params = [ctx["tenant_id"], limit]

            if is_active is not None:
                filter_clause = " AND is_active = $3"
                params.append(is_active)

            rows = await conn.fetch(
                f"""
                SELECT id, employee_code, name, email, department, position, is_active
                FROM employees
                WHERE tenant_id = $1 {filter_clause}
                ORDER BY name ASC
                LIMIT $2
            """,
                *params,
            )

            employees = [
                {
                    "id": str(row["id"]),
                    "code": row.get("employee_code"),
                    "name": row.get("name"),
                    "email": row.get("email"),
                    "department": row.get("department"),
                    "position": row.get("position"),
                    "is_active": row.get("is_active", True),
                }
                for row in rows
            ]

            return {"success": True, "employees": employees}
    except Exception as e:
        logger.error(f"Error listing employees: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list employees")


# =============================================================================
# SALESPERSONS
# =============================================================================


@router.get("/salespersons")
async def list_salespersons(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    is_active: Optional[bool] = Query(True),
):
    """List salespersons."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            filter_clause = ""
            params = [ctx["tenant_id"], limit]

            if is_active is not None:
                filter_clause = " AND is_active = $3"
                params.append(is_active)

            rows = await conn.fetch(
                f"""
                SELECT id, code, name, email, phone, commission_rate, is_active
                FROM salespersons
                WHERE tenant_id = $1 {filter_clause}
                ORDER BY name ASC
                LIMIT $2
            """,
                *params,
            )

            salespersons = [
                {
                    "id": str(row["id"]),
                    "code": row.get("code"),
                    "name": row.get("name"),
                    "email": row.get("email"),
                    "phone": row.get("phone"),
                    "commission_rate": row.get("commission_rate", 0),
                    "is_active": row.get("is_active", True),
                }
                for row in rows
            ]

            return {"success": True, "salespersons": salespersons}
    except Exception as e:
        logger.error(f"Error listing salespersons: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list salespersons")


# =============================================================================
# PAYMENT OUT (Bill Payments)
# =============================================================================


@router.get("/payment-out")
async def list_payment_out(
    request: Request,
    limit: int = Query(100, ge=1, le=200),
    page: int = Query(1, ge=1),
):
    """List bill payments (payment out)."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        offset = (page - 1) * limit

        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    bp.id, bp.payment_number, bp.payment_date, bp.amount,
                    bp.payment_method, bp.memo, bp.status,
                    v.name as vendor_name,
                    ba.account_name as payment_account_name
                FROM bill_payments bp
                LEFT JOIN vendors v ON v.id = bp.vendor_id
                LEFT JOIN bank_accounts ba ON ba.id = bp.payment_account_id
                WHERE bp.tenant_id = $1
                ORDER BY bp.payment_date DESC, bp.created_at DESC
                LIMIT $2 OFFSET $3
            """,
                ctx["tenant_id"],
                limit,
                offset,
            )

            payments = [
                {
                    "id": str(row["id"]),
                    "payment_number": row.get("payment_number"),
                    "payment_date": row["payment_date"].isoformat()
                    if row.get("payment_date")
                    else None,
                    "amount": row.get("amount", 0),
                    "payment_method": row.get("payment_method"),
                    "memo": row.get("memo"),
                    "status": row.get("status"),
                    "vendor_name": row.get("vendor_name"),
                    "payment_account_name": row.get("payment_account_name"),
                }
                for row in rows
            ]

            total = await conn.fetchval(
                "SELECT COUNT(*) FROM bill_payments WHERE tenant_id = $1",
                ctx["tenant_id"],
            )

            return {"success": True, "payments": payments, "total": total, "page": page}
    except Exception as e:
        logger.error(f"Error listing payments: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list payments")


# =============================================================================
# INSIGHT DASHBOARD
# =============================================================================


@router.get("/insight")
async def get_insight(
    request: Request,
    period: str = Query("month", description="Period: day, week, month, year"),
):
    """Get business insight dashboard data."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Get period interval
            interval = {
                "day": "1 day",
                "week": "7 days",
                "month": "30 days",
                "year": "365 days",
            }.get(period, "30 days")

            # Revenue
            revenue = await conn.fetchval(
                f"""
                SELECT COALESCE(SUM(total_amount), 0)
                FROM sales_invoices
                WHERE tenant_id = $1
                  AND invoice_date >= CURRENT_DATE - INTERVAL '{interval}'
                  AND status IN ('posted', 'paid', 'partial')
            """,
                ctx["tenant_id"],
            )

            # Expenses
            total_expenses = await conn.fetchval(
                f"""
                SELECT COALESCE(SUM(total_amount), 0)
                FROM expenses
                WHERE tenant_id = $1
                  AND expense_date >= CURRENT_DATE - INTERVAL '{interval}'
                  AND status = 'posted'
            """,
                ctx["tenant_id"],
            )

            # Outstanding AR
            ar_outstanding = await conn.fetchval(
                """
                SELECT COALESCE(SUM(total_amount - COALESCE(amount_paid, 0)), 0)
                FROM sales_invoices
                WHERE tenant_id = $1
                  AND status IN ('posted', 'partial', 'overdue')
            """,
                ctx["tenant_id"],
            )

            # Outstanding AP
            ap_outstanding = await conn.fetchval(
                """
                SELECT COALESCE(SUM(total_amount - COALESCE(amount_paid, 0)), 0)
                FROM bills
                WHERE tenant_id = $1
                  AND status IN ('posted', 'partial', 'overdue')
            """,
                ctx["tenant_id"],
            )

            # Cash balance
            cash_balance = await conn.fetchval(
                """
                SELECT COALESCE(SUM(current_balance), 0)
                FROM bank_accounts
                WHERE tenant_id = $1 AND is_active = true
            """,
                ctx["tenant_id"],
            )

            return {
                "success": True,
                "period": period,
                "metrics": {
                    "revenue": revenue,
                    "expenses": total_expenses,
                    "profit": revenue - total_expenses,
                    "ar_outstanding": ar_outstanding,
                    "ap_outstanding": ap_outstanding,
                    "cash_balance": cash_balance,
                },
            }
    except Exception as e:
        logger.error(f"Error getting insight: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get insight")
