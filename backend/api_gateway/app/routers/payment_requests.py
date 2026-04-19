"""
Payment Request Router
Endpoints untuk permintaan pembayaran (digitizing transfer culture)

IRON LAW COMPLIANCE:
- Law 0: Separation of Concerns - Router handles HTTP, Service handles logic
- Law 6: Source Traceability - Journal linked via source_type/source_id
"""
import logging
from typing import Optional
from fastapi import APIRouter, Request, Query, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)


def validate_uuid(value: str) -> bool:
    """Validate if string is valid UUID format"""
    try:
        UUID(value)
        return True
    except (ValueError, TypeError):
        return False


router = APIRouter(prefix="/api/payment-requests", tags=["payment-requests"])


# === SCHEMAS ===
class CreatePaymentRequestDTO(BaseModel):
    purpose: str
    description: Optional[str] = None
    amount: int  # In smallest currency unit
    bank_account_from: Optional[str] = None
    recipient_bank_name: str
    recipient_account_number: str
    recipient_account_name: str
    reference_type: Optional[str] = None
    reference_id: Optional[str] = None
    reference_number: Optional[str] = None


class ApproveRejectDTO(BaseModel):
    reason: Optional[str] = None


class MarkPaidDTO(BaseModel):
    proof_url: str
    payment_reference: Optional[str] = None


# === HELPER ===
def _get_user_context(request: Request) -> dict:
    if not hasattr(request.state, "user") or not request.state.user:
        raise HTTPException(status_code=401, detail="Authentication required")
    return request.state.user


# === ENDPOINTS ===


@router.get("")
async def list_payment_requests(
    request: Request,
    status: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
):
    """List payment requests dengan filter dan pagination"""
    from backend.api_gateway.app.services.payment_request_service import (
        get_payment_request_service,
    )

    user = _get_user_context(request)
    service = await get_payment_request_service()

    offset = (page - 1) * per_page
    result = await service.list(
        tenant_id=user["tenant_id"],
        user_visibility=user.get("visibility_levels", ["L1", "L2", "L3"]),
        status=status,
        offset=offset,
        limit=per_page,
    )
    return result


@router.get("/summary")
async def get_payment_request_summary(request: Request):
    """Summary untuk Stats Card"""
    from backend.api_gateway.app.services.payment_request_service import (
        get_payment_request_service,
    )

    user = _get_user_context(request)
    service = await get_payment_request_service()

    return await service.get_summary(
        tenant_id=user["tenant_id"],
        user_visibility=user.get("visibility_levels", ["L1", "L2", "L3"]),
    )


@router.get("/{request_id}")
async def get_payment_request(request: Request, request_id: str):
    """Get detail payment request"""
    # Validate UUID format first
    if not validate_uuid(request_id):
        raise HTTPException(status_code=400, detail="Invalid payment request ID format")

    from backend.api_gateway.app.services.payment_request_service import (
        get_payment_request_service,
    )

    user = _get_user_context(request)
    service = await get_payment_request_service()

    result = await service.get_by_id(
        tenant_id=user["tenant_id"],
        request_id=request_id,
        user_visibility=user.get("visibility_levels", ["L1", "L2", "L3"]),
    )
    if not result:
        raise HTTPException(status_code=404, detail="Payment request not found")
    return result


@router.post("")
async def create_payment_request(request: Request, data: CreatePaymentRequestDTO):
    """Bendahara creates new payment request"""
    from backend.api_gateway.app.services.payment_request_service import (
        get_payment_request_service,
    )

    user = _get_user_context(request)
    service = await get_payment_request_service()

    result = await service.create(
        tenant_id=user["tenant_id"],
        user_id=user["user_id"],
        user_name=user.get("username") or user.get("email", "Unknown"),
        data=data.dict(),
    )
    return result


@router.post("/{request_id}/approve")
async def approve_payment_request(
    request: Request, request_id: str, data: ApproveRejectDTO = None
):
    """Owner approves payment request"""
    from backend.api_gateway.app.services.payment_request_service import (
        get_payment_request_service,
    )

    user = _get_user_context(request)
    service = await get_payment_request_service()

    # Check business role - only OWNER or FINANCE_MGR can approve
    business_role = user.get("business_role_code")
    if business_role and business_role not in ["OWNER", "FINANCE_MGR"]:
        raise HTTPException(
            status_code=403, detail="Only Owner or Finance Manager can approve"
        )

    result = await service.approve(
        tenant_id=user["tenant_id"],
        request_id=request_id,
        approver_id=user["user_id"],
        approver_name=user.get("username") or user.get("email", "Unknown"),
    )
    return result


@router.post("/{request_id}/reject")
async def reject_payment_request(
    request: Request, request_id: str, data: ApproveRejectDTO
):
    """Owner rejects payment request"""
    from backend.api_gateway.app.services.payment_request_service import (
        get_payment_request_service,
    )

    user = _get_user_context(request)
    service = await get_payment_request_service()

    # Check business role
    business_role = user.get("business_role_code")
    if business_role and business_role not in ["OWNER", "FINANCE_MGR"]:
        raise HTTPException(
            status_code=403, detail="Only Owner or Finance Manager can reject"
        )

    result = await service.reject(
        tenant_id=user["tenant_id"],
        request_id=request_id,
        approver_id=user["user_id"],
        approver_name=user.get("username") or user.get("email", "Unknown"),
        reason=data.reason if data else None,
    )
    return result


@router.post("/{request_id}/cancel")
async def cancel_payment_request(request: Request, request_id: str):
    """Requestor cancels own request"""
    from backend.api_gateway.app.services.payment_request_service import (
        get_payment_request_service,
    )

    user = _get_user_context(request)
    service = await get_payment_request_service()

    result = await service.cancel(
        tenant_id=user["tenant_id"], request_id=request_id, user_id=user["user_id"]
    )
    return result


@router.post("/{request_id}/mark-paid")
async def mark_payment_request_paid(
    request: Request, request_id: str, data: MarkPaidDTO
):
    """
    Owner marks as paid + uploads proof, triggers auto-journal.

    IRON LAW 6: Creates journal with source_type='PAYMENT_REQUEST'
    IRON LAW 8: Balance changes only via this journal
    """
    from backend.api_gateway.app.services.payment_request_service import (
        get_payment_request_service,
    )

    user = _get_user_context(request)
    service = await get_payment_request_service()

    # Check business role
    business_role = user.get("business_role_code")
    if business_role and business_role not in ["OWNER", "FINANCE_MGR"]:
        raise HTTPException(
            status_code=403, detail="Only Owner or Finance Manager can mark as paid"
        )

    result = await service.mark_paid(
        tenant_id=user["tenant_id"],
        request_id=request_id,
        payer_id=user["user_id"],
        payer_name=user.get("username") or user.get("email", "Unknown"),
        proof_url=data.proof_url,
        payment_reference=data.payment_reference,
    )
    return result


@router.get("/{request_id}/journal-entries")
async def get_payment_request_journal_entries(request: Request, request_id: str):
    """
    Tab: Journal Entries - Get journal entries linked to this payment request.
    """
    try:
        from uuid import UUID as _UUID

        try:
            _UUID(request_id)
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="Invalid request_id format")

        user = _get_user_context(request)
        tenant_id = user["tenant_id"]

        from ..services.db_pool import get_db_pool

        pool = await get_db_pool()

        async with pool.acquire() as conn:
            pr = await conn.fetchrow(
                """
                SELECT id, request_number, journal_entry_id, status
                FROM payment_requests
                WHERE id = $1::uuid AND tenant_id = $2
                """,
                request_id,
                tenant_id,
            )

            if not pr:
                raise HTTPException(status_code=404, detail="Payment request not found")

            journal_ids = []
            if pr["journal_entry_id"]:
                journal_ids.append(pr["journal_entry_id"])

            source_journals = await conn.fetch(
                """
                SELECT id FROM journal_entries
                WHERE tenant_id = $1 AND source_id = $2::uuid
                """,
                tenant_id,
                request_id,
            )
            for row in source_journals:
                if row["id"] not in journal_ids:
                    journal_ids.append(row["id"])

            if not journal_ids:
                return {
                    "success": True,
                    "data": [],
                    "total": 0,
                    "summary": {
                        "total_debit": 0,
                        "total_credit": 0,
                        "is_balanced": True,
                    },
                }

            journals = await conn.fetch(
                """
                SELECT je.id, je.journal_number, je.journal_date, je.description,
                       je.source_type, je.status, je.total_debit, je.total_credit
                FROM journal_entries je
                WHERE je.id = ANY($1::uuid[])
                ORDER BY je.journal_date, je.created_at
                """,
                journal_ids,
            )

            journal_data = []
            total_debit = 0
            total_credit = 0

            for journal in journals:
                lines = await conn.fetch(
                    """
                    SELECT jl.id, jl.line_number, jl.account_id, jl.debit, jl.credit, jl.memo,
                           coa.account_code, coa.name as account_name
                    FROM journal_lines jl
                    JOIN chart_of_accounts coa ON coa.id = jl.account_id
                    WHERE jl.journal_id = $1
                    ORDER BY jl.line_number
                    """,
                    journal["id"],
                )

                line_data = [
                    {
                        "id": str(line["id"]),
                        "line_number": line["line_number"],
                        "account_id": str(line["account_id"]),
                        "account_code": line["account_code"],
                        "account_name": line["account_name"],
                        "debit": float(line["debit"] or 0),
                        "credit": float(line["credit"] or 0),
                        "memo": line["memo"] or "",
                    }
                    for line in lines
                ]

                journal_debit = float(journal["total_debit"] or 0)
                journal_credit = float(journal["total_credit"] or 0)
                total_debit += journal_debit
                total_credit += journal_credit

                journal_data.append(
                    {
                        "id": str(journal["id"]),
                        "journal_number": journal["journal_number"],
                        "journal_date": journal["journal_date"].isoformat()
                        if journal["journal_date"]
                        else None,
                        "description": journal["description"],
                        "source_type": journal["source_type"],
                        "status": journal["status"],
                        "total_debit": journal_debit,
                        "total_credit": journal_credit,
                        "is_balanced": abs(journal_debit - journal_credit) < 0.01,
                        "lines": line_data,
                    }
                )

            return {
                "success": True,
                "data": journal_data,
                "total": len(journal_data),
                "summary": {
                    "total_debit": total_debit,
                    "total_credit": total_credit,
                    "is_balanced": abs(total_debit - total_credit) < 0.01,
                },
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Error getting payment request journal entries: {e}", exc_info=True
        )
        raise HTTPException(status_code=500, detail="Failed to get journal entries")
