"""
Bills Router - Faktur Pembelian (Purchase Invoice) Management

Endpoints for managing bills, payments, and attachments.
Integrates with accounting kernel for AP and journal entries.
"""

from fastapi import APIRouter, HTTPException, Request, Query, UploadFile, File
from typing import Optional, Literal
from uuid import UUID
from datetime import date
import logging
import asyncpg

# Import schemas
from ..schemas.bills import (
    CreateBillRequest,
    UpdateBillRequest,
    RecordPaymentRequest,
    MarkPaidRequest,
    VoidBillRequest,
    BillListResponse,
    BillDetailResponse,
    CreateBillResponse,
    UpdateBillResponse,
    DeleteBillResponse,
    RecordPaymentResponse,
    MarkPaidResponse,
    VoidBillResponse,
    BillSummaryResponse,
    UploadAttachmentResponse
)

# Import service
from ..services.bills_service import BillsService

# Import centralized config
from ..config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

# Connection pool (initialized on first request)
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


async def get_bills_service() -> BillsService:
    """Get BillsService instance with connection pool."""
    pool = await get_pool()
    # TODO: Integrate AccountingFacade when available
    return BillsService(pool, accounting_facade=None)


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
        "user_id": UUID(user_id) if user_id else None
    }


# =============================================================================
# LIST BILLS
# =============================================================================
@router.get("", response_model=BillListResponse)
async def list_bills(
    request: Request,
    skip: int = Query(0, ge=0, description="Offset for pagination"),
    limit: int = Query(20, ge=1, le=100, description="Items per page"),
    status: Literal["all", "paid", "unpaid", "partial", "overdue"] = Query(
        "all", description="Filter by status"
    ),
    search: Optional[str] = Query(None, description="Search invoice number or vendor"),
    sort_by: Literal["created_at", "due_date", "amount", "vendor_name"] = Query(
        "created_at", description="Sort field"
    ),
    sort_order: Literal["asc", "desc"] = Query("desc", description="Sort order"),
    due_date_from: Optional[date] = Query(None, description="Filter due date from"),
    due_date_to: Optional[date] = Query(None, description="Filter due date to"),
    vendor_id: Optional[UUID] = Query(None, description="Filter by vendor")
):
    """
    List bills with filtering, sorting, and pagination.

    **Status values:**
    - `all`: All bills
    - `paid`: Fully paid (amount_paid >= amount)
    - `unpaid`: No payment yet, not overdue
    - `partial`: Partially paid
    - `overdue`: Past due date with balance remaining
    """
    try:
        ctx = get_user_context(request)
        service = await get_bills_service()

        result = await service.list_bills(
            tenant_id=ctx["tenant_id"],
            skip=skip,
            limit=limit,
            status=status,
            search=search,
            sort_by=sort_by,
            sort_order=sort_order,
            due_date_from=due_date_from,
            due_date_to=due_date_to,
            vendor_id=vendor_id
        )

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing bills: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list bills")


# =============================================================================
# GET SUMMARY
# =============================================================================
@router.get("/summary", response_model=BillSummaryResponse)
async def get_bills_summary(
    request: Request,
    period: str = Query(
        "current_month",
        description="Period: current_month, last_month, current_year, or YYYY-MM"
    )
):
    """
    Get bills summary statistics for dashboard.

    Returns total amounts, counts, and breakdown by status.
    """
    try:
        ctx = get_user_context(request)
        service = await get_bills_service()

        result = await service.get_summary(
            tenant_id=ctx["tenant_id"],
            period=period
        )

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting summary: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get summary")


# =============================================================================
# HEALTH CHECK (must be before /{bill_id} to avoid route conflict)
# =============================================================================
@router.get("/health")
async def health_check():
    """Health check endpoint for the bills service."""
    return {"status": "ok", "service": "bills"}


# =============================================================================
# GET BILL DETAIL
# =============================================================================
@router.get("/{bill_id}", response_model=BillDetailResponse)
async def get_bill_detail(
    request: Request,
    bill_id: UUID
):
    """
    Get detailed information for a single bill.

    Includes items, payments, and attachments.
    """
    try:
        ctx = get_user_context(request)
        service = await get_bills_service()

        bill = await service.get_bill(
            tenant_id=ctx["tenant_id"],
            bill_id=bill_id
        )

        if not bill:
            raise HTTPException(status_code=404, detail="Bill not found")

        return {"success": True, "data": bill}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting bill {bill_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get bill")


# =============================================================================
# CREATE BILL
# =============================================================================
@router.post("", response_model=CreateBillResponse, status_code=201)
async def create_bill(
    request: Request,
    body: CreateBillRequest
):
    """
    Create a new bill (faktur pembelian).

    - If `invoice_number` is not provided, it will be auto-generated.
    - Either `vendor_id` or `vendor_name` must be provided.
    - At least one item is required.

    This also creates:
    - An AP (Accounts Payable) record in the accounting kernel
    - A journal entry (DR Inventory/Expense, CR AP)
    """
    try:
        ctx = get_user_context(request)

        if not ctx["user_id"]:
            raise HTTPException(status_code=401, detail="User ID required")

        if not body.vendor_name and not body.vendor_id:
            raise HTTPException(
                status_code=400,
                detail="Either vendor_name or vendor_id is required"
            )

        service = await get_bills_service()

        result = await service.create_bill(
            tenant_id=ctx["tenant_id"],
            request=body.model_dump(),
            user_id=ctx["user_id"]
        )

        if not result["success"]:
            raise HTTPException(status_code=400, detail=result["message"])

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating bill: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create bill")


# =============================================================================
# UPDATE BILL
# =============================================================================
@router.patch("/{bill_id}", response_model=UpdateBillResponse)
async def update_bill(
    request: Request,
    bill_id: UUID,
    body: UpdateBillRequest
):
    """
    Update a bill.

    **Restrictions:**
    - Only bills with no payments can be updated
    - Voided bills cannot be updated
    """
    try:
        ctx = get_user_context(request)
        service = await get_bills_service()

        result = await service.update_bill(
            tenant_id=ctx["tenant_id"],
            bill_id=bill_id,
            request=body.model_dump(exclude_unset=True)
        )

        if not result["success"]:
            raise HTTPException(status_code=400, detail=result["message"])

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating bill {bill_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update bill")


# =============================================================================
# DELETE BILL
# =============================================================================
@router.delete("/{bill_id}", response_model=DeleteBillResponse)
async def delete_bill(
    request: Request,
    bill_id: UUID
):
    """
    Delete a bill.

    **Restrictions:**
    - Only bills with no payments can be deleted
    - Use void endpoint for bills with payments
    """
    try:
        ctx = get_user_context(request)
        service = await get_bills_service()

        result = await service.delete_bill(
            tenant_id=ctx["tenant_id"],
            bill_id=bill_id
        )

        if not result["success"]:
            raise HTTPException(status_code=400, detail=result["message"])

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting bill {bill_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to delete bill")


# =============================================================================
# RECORD PAYMENT
# =============================================================================
@router.post("/{bill_id}/payments", response_model=RecordPaymentResponse, status_code=201)
async def record_payment(
    request: Request,
    bill_id: UUID,
    body: RecordPaymentRequest
):
    """
    Record a payment for a bill.

    - Payment amount must not exceed the remaining balance
    - Creates a journal entry (DR AP, CR Kas/Bank)
    - Updates bill status automatically
    """
    try:
        ctx = get_user_context(request)

        if not ctx["user_id"]:
            raise HTTPException(status_code=401, detail="User ID required")

        service = await get_bills_service()

        result = await service.record_payment(
            tenant_id=ctx["tenant_id"],
            bill_id=bill_id,
            request=body.model_dump(),
            user_id=ctx["user_id"]
        )

        if not result["success"]:
            raise HTTPException(status_code=400, detail=result["message"])

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error recording payment for bill {bill_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to record payment")


# =============================================================================
# MARK AS PAID (Quick Action)
# =============================================================================
@router.patch("/{bill_id}/mark-paid", response_model=MarkPaidResponse)
async def mark_bill_paid(
    request: Request,
    bill_id: UUID,
    body: MarkPaidRequest
):
    """
    Mark a bill as fully paid.

    This is a convenience endpoint that pays the full remaining balance.
    Useful for swipe-to-pay actions in mobile apps.
    """
    try:
        ctx = get_user_context(request)

        if not ctx["user_id"]:
            raise HTTPException(status_code=401, detail="User ID required")

        service = await get_bills_service()

        result = await service.mark_paid(
            tenant_id=ctx["tenant_id"],
            bill_id=bill_id,
            request=body.model_dump(),
            user_id=ctx["user_id"]
        )

        if not result["success"]:
            raise HTTPException(status_code=400, detail=result["message"])

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error marking bill {bill_id} as paid: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to mark as paid")


# =============================================================================
# VOID BILL
# =============================================================================
@router.post("/{bill_id}/void", response_model=VoidBillResponse)
async def void_bill(
    request: Request,
    bill_id: UUID,
    body: VoidBillRequest
):
    """
    Void a bill.

    - Reason is required for audit trail
    - Creates reversal journal entries if payments exist
    - Voids the AP record in accounting kernel
    """
    try:
        ctx = get_user_context(request)

        if not ctx["user_id"]:
            raise HTTPException(status_code=401, detail="User ID required")

        service = await get_bills_service()

        result = await service.void_bill(
            tenant_id=ctx["tenant_id"],
            bill_id=bill_id,
            request=body.model_dump(),
            user_id=ctx["user_id"]
        )

        if not result["success"]:
            raise HTTPException(status_code=400, detail=result["message"])

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error voiding bill {bill_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to void bill")


# =============================================================================
# UPLOAD ATTACHMENT
# =============================================================================
@router.post("/{bill_id}/attachments", response_model=UploadAttachmentResponse, status_code=201)
async def upload_attachment(
    request: Request,
    bill_id: UUID,
    file: UploadFile = File(..., description="Image or PDF file (max 5MB)")
):
    """
    Upload an attachment to a bill.

    Supported formats: JPEG, PNG, PDF (max 5MB)
    """
    try:
        ctx = get_user_context(request)

        if not ctx["user_id"]:
            raise HTTPException(status_code=401, detail="User ID required")

        # Validate file size (5MB max)
        MAX_SIZE = 5 * 1024 * 1024
        content = await file.read()
        if len(content) > MAX_SIZE:
            raise HTTPException(
                status_code=400,
                detail="File size exceeds 5MB limit"
            )

        # Validate file type
        allowed_types = ["image/jpeg", "image/png", "application/pdf"]
        if file.content_type not in allowed_types:
            raise HTTPException(
                status_code=400,
                detail=f"File type not allowed. Allowed: {', '.join(allowed_types)}"
            )

        # TODO: Implement file storage (S3, local, etc.)
        # For now, return placeholder response
        return {
            "success": True,
            "data": {
                "id": str(bill_id),  # placeholder
                "filename": file.filename,
                "url": f"/attachments/{bill_id}/{file.filename}"  # placeholder
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error uploading attachment for bill {bill_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to upload attachment")


