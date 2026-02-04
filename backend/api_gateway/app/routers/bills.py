"""
Bills Router - Faktur Pembelian (Purchase Invoice) Management

Endpoints for managing bills, payments, and attachments.
Integrates with accounting kernel for AP and journal entries.
"""

from fastapi import APIRouter, HTTPException, Request, Query, UploadFile, File
from fastapi.responses import StreamingResponse
from typing import Optional, Literal
from uuid import UUID
from datetime import date, datetime, timedelta
from io import BytesIO
import logging
import asyncpg

from ..utils.sorting import parse_sort_param

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
    OutstandingSummaryResponse,
    UploadAttachmentResponse,
    # V2 schemas
    CreateBillRequestV2,
    UpdateBillRequestV2,
    CreateBillResponseV2,
    CalculateBillResponse,
    # Activity schemas
    BillActivity,
    BillActivityResponse,
)

# Import calculator for preview endpoint
from ..services.bills_service import BillCalculator

# Import services
from ..services.bills_service import BillsService
# Import AccountingFacade for AP integration
try:
    import sys
    sys.path.insert(0, "/app/backend/services")
    from accounting_kernel.integration.facade import AccountingFacade
    HAS_ACCOUNTING = True
except ImportError:
    AccountingFacade = None
    HAS_ACCOUNTING = False
from ..services.pdf_service import get_pdf_service
from ..services.storage_service import get_storage_service

# Import centralized config
from ..config import settings

logger = logging.getLogger(__name__)

# System filter presets (read-only, available to all users)
BILLS_SYSTEM_PRESETS = [
    {
        "id": "system:urgent",
        "name": "Jatuh Tempo Terdekat",
        "description": "Tagihan yang mendekati atau sudah lewat jatuh tempo",
        "config": {
            "sort": "due_date:asc,balance:desc",
            "filters": {"status": ["unpaid", "partial", "overdue"]},
        },
        "is_system": True,
        "icon": "clock",
    },
    {
        "id": "system:recently-paid",
        "name": "Terakhir Dibayar",
        "description": "Tagihan yang baru saja dibayar",
        "config": {
            "sort": "updated_at:desc",
            "filters": {"status": ["paid", "partial"]},
        },
        "is_system": True,
        "icon": "check-circle",
    },
    {
        "id": "system:largest-outstanding",
        "name": "Tagihan Terbesar",
        "description": "Tagihan dengan saldo terbesar",
        "config": {
            "sort": "balance:desc",
            "filters": {"status": ["unpaid", "partial", "overdue"]},
        },
        "is_system": True,
        "icon": "trending-up",
    },
    {
        "id": "system:newest",
        "name": "Terbaru",
        "description": "Tagihan terbaru berdasarkan tanggal dibuat",
        "config": {"sort": "created_at:desc", "filters": {}},
        "is_system": True,
        "icon": "plus-circle",
    },
    {
        "id": "system:by-supplier",
        "name": "Per Supplier",
        "description": "Diurutkan berdasarkan nama supplier",
        "config": {
            "sort": "supplier:asc,due_date:asc",
            "filters": {"status": ["unpaid", "partial", "overdue"]},
        },
        "is_system": True,
        "icon": "users",
    },
]

router = APIRouter()

# Connection pool (initialized on first request)
_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
    """Get or create connection pool."""
    global _pool
    if _pool is None:
        db_config = settings.get_db_config()
        _pool = await asyncpg.create_pool(
            **db_config, min_size=2, max_size=10, command_timeout=30
        )
    return _pool


# Global accounting facade instance
_accounting_facade = None

async def get_accounting_facade():
    """Get or create AccountingFacade instance."""
    global _accounting_facade
    if HAS_ACCOUNTING and _accounting_facade is None:
        pool = await get_pool()
        _accounting_facade = AccountingFacade(pool)
    return _accounting_facade

async def get_bills_service() -> BillsService:
    """Get BillsService instance with connection pool and accounting facade."""
    pool = await get_pool()
    facade = await get_accounting_facade() if HAS_ACCOUNTING else None
    return BillsService(pool, accounting_facade=facade)


def get_user_context(request: Request) -> dict:
    """Extract and validate user context from request."""
    if not hasattr(request.state, "user") or not request.state.user:
        raise HTTPException(status_code=401, detail="Authentication required")

    user = request.state.user
    tenant_id = user.get("tenant_id")
    user_id = user.get("user_id")

    if not tenant_id:
        raise HTTPException(status_code=401, detail="Invalid user context")

    return {"tenant_id": tenant_id, "user_id": UUID(user_id) if user_id else None}


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
    sort: str = Query(
        default="created_at:desc",
        description="Comma-separated sort fields. Format: field:order,field:order. "
        "Fields: created_at, date, number, supplier, due_date, amount, "
        "balance, status, updated_at. Example: status:asc,amount:desc",
    ),
    # Keep legacy params for backward compatibility
    sort_by: Optional[str] = Query(
        None, description="[DEPRECATED] Use 'sort' param instead"
    ),
    sort_order: Optional[str] = Query(
        None, description="[DEPRECATED] Use 'sort' param instead"
    ),
    due_date_from: Optional[date] = Query(None, description="Filter due date from"),
    due_date_to: Optional[date] = Query(None, description="Filter due date to"),
    vendor_id: Optional[UUID] = Query(None, description="Filter by vendor"),
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

        # Parse sort parameter (with legacy fallback)
        # If legacy sort_by is explicitly provided, use it instead of new sort param
        if sort_by is not None:
            # Legacy mode: convert old params to new format
            legacy_order = sort_order or "desc"
            sort_fields = [(sort_by, legacy_order)]
        else:
            sort_fields = parse_sort_param(sort)

        result = await service.list_bills(
            tenant_id=ctx["tenant_id"],
            skip=skip,
            limit=limit,
            status=status,
            search=search,
            sort_fields=sort_fields,
            due_date_from=due_date_from,
            due_date_to=due_date_to,
            vendor_id=vendor_id,
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
        description="Period: current_month, last_month, current_year, or YYYY-MM",
    ),
):
    """
    Get bills summary statistics for dashboard.

    Returns total amounts, counts, and breakdown by status.
    """
    try:
        ctx = get_user_context(request)
        service = await get_bills_service()

        result = await service.get_summary(tenant_id=ctx["tenant_id"], period=period)

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting summary: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get summary")


# =============================================================================
# GET OUTSTANDING SUMMARY (Proper Aging Separation)
# =============================================================================
@router.get("/outstanding-summary", response_model=OutstandingSummaryResponse)
async def get_outstanding_summary(request: Request):
    """
    Get outstanding bills summary with proper aging separation.

    This is the proper accounting view for current outstanding payables (hutang).
    Unlike /summary which filters by period, this shows the current state of
    all unpaid bills with proper aging-based categorization.

    **Aging Logic (mutually exclusive):**
    - `overdue`: due_date < TODAY (sudah jatuh tempo)
    - `current`: due_date >= TODAY OR NULL (belum jatuh tempo)

    **Invariants:**
    - `by_aging.overdue + by_aging.current == total_outstanding`
    - `sum(aging_breakdown.*) == by_aging.overdue`
    - `counts.overdue + counts.current == counts.total`

    **Response structure:**
    - `total_outstanding`: Total outstanding amount
    - `by_aging`: {overdue, current} - mutually exclusive amounts
    - `counts`: {total, overdue, current, partial, partial_overdue, partial_current, vendors, no_due_date}
    - `aging_breakdown`: {overdue_1_30, overdue_31_60, overdue_61_90, overdue_90_plus}
    - `urgency`: {oldest_days, largest_amount, due_within_7_days}

    **Note:** Bills without due_date are treated as "current" (conservative approach)
    and tracked separately via `counts.no_due_date` for visibility.
    """
    try:
        ctx = get_user_context(request)
        service = await get_bills_service()

        result = await service.get_outstanding_summary(tenant_id=ctx["tenant_id"])

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting outstanding summary: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get outstanding summary")


# =============================================================================
# FILTER PRESETS
# =============================================================================
@router.get("/presets", response_model=dict)
async def get_filter_presets(request: Request):
    """
    Get available filter presets for bills.

    Returns system presets that are available to all users.
    User-specific presets will be added in a future release.

    **Usage:**
    1. Fetch presets on page load
    2. Display as quick-filter buttons/chips
    3. When user clicks a preset, apply its `config.sort` and `config.filters`
    """
    try:
        get_user_context(request)  # Validate auth

        return {
            "success": True,
            "data": {
                "system_presets": BILLS_SYSTEM_PRESETS,
                "user_presets": [],  # TODO: Phase 2 - saved user presets
            },
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting presets: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get presets")


# =============================================================================
# APPLY PRESET
# =============================================================================
@router.get("/presets/{preset_id}/apply", response_model=BillListResponse)
async def apply_preset(
    request: Request,
    preset_id: str,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    search: Optional[str] = Query(None),
):
    """
    Fetch bills using a preset's configuration.

    This is a convenience endpoint that applies a preset's sort and filters.
    Equivalent to calling GET /api/bills with the preset's config.
    """
    try:
        ctx = get_user_context(request)

        # Find preset
        preset = None
        for p in BILLS_SYSTEM_PRESETS:
            if p["id"] == preset_id:
                preset = p
                break

        if not preset:
            raise HTTPException(
                status_code=404, detail=f"Preset '{preset_id}' not found"
            )

        config = preset["config"]
        sort_fields = parse_sort_param(config.get("sort", "created_at:desc"))
        filters = config.get("filters", {})

        # Map preset filters to service params
        status_filter = "all"
        if filters.get("status"):
            statuses = filters["status"]
            if len(statuses) == 1:
                status_filter = statuses[0]
            # Multiple statuses: use 'all' and let frontend filter
            # TODO: Support multiple status filter in service

        service = await get_bills_service()
        result = await service.list_bills(
            tenant_id=ctx["tenant_id"],
            skip=skip,
            limit=limit,
            status=status_filter,
            search=search,
            sort_fields=sort_fields,
        )

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error applying preset {preset_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to apply preset")


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
async def get_bill_detail(request: Request, bill_id: UUID):
    """
    Get detailed information for a single bill.

    Includes items, payments, and attachments.
    """
    try:
        ctx = get_user_context(request)
        service = await get_bills_service()

        bill = await service.get_bill(tenant_id=ctx["tenant_id"], bill_id=bill_id)

        if not bill:
            raise HTTPException(status_code=404, detail="Bill not found")

        return {"success": True, "data": bill}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting bill {bill_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get bill")

# =============================================================================
# GET BILL JOURNALS
# =============================================================================
@router.get("/{bill_id}/journals")
async def get_bill_journals(request: Request, bill_id: UUID):
    """
    Get journal entries linked to a specific bill.

    Returns journals where source_type='BILL' and source_id={bill_id}.
    This is a READ-ONLY endpoint.
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            # First verify the bill exists and belongs to this tenant
            bill = await conn.fetchrow(
                """
                SELECT id, invoice_number FROM bills
                WHERE id = $1 AND tenant_id = $2
            """,
                bill_id,
                ctx["tenant_id"],
            )

            if not bill:
                raise HTTPException(status_code=404, detail="Bill not found")

            # Get journals linked to this bill
            journals_rows = await conn.fetch(
                """
                SELECT je.id, je.journal_number, je.journal_date, je.description,
                       je.status, je.total_debit, je.total_credit, je.created_at
                FROM journal_entries je
                WHERE je.tenant_id = $1
                  AND je.source_type = 'BILL'
                  AND je.source_id = $2
                ORDER BY je.created_at DESC
            """,
                ctx["tenant_id"],
                bill_id,
            )

            journals = []
            for je_row in journals_rows:
                # Get lines for each journal
                lines_rows = await conn.fetch(
                    """
                    SELECT jl.id, jl.line_number, jl.debit, jl.credit, jl.memo,
                           coa.account_code, coa.name as account_name
                    FROM journal_lines jl
                    JOIN chart_of_accounts coa ON coa.id = jl.account_id
                    WHERE jl.journal_id = $1
                    ORDER BY jl.line_number
                """,
                    je_row["id"],
                )

                lines = [
                    {
                        "account_name": line["account_name"],
                        "account_code": line["account_code"],
                        "debit": float(line["debit"] or 0),
                        "credit": float(line["credit"] or 0),
                    }
                    for line in lines_rows
                ]

                journals.append({
                    "id": str(je_row["id"]),
                    "entry_number": je_row["journal_number"],
                    "posting_date": je_row["journal_date"].isoformat() if je_row["journal_date"] else None,
                    "description": je_row["description"],
                    "status": je_row["status"].lower() if je_row["status"] else "draft",
                    "total_debit": float(je_row["total_debit"] or 0),
                    "total_credit": float(je_row["total_credit"] or 0),
                    "lines": lines,
                })

            return {"journals": journals}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting journals for bill {bill_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get bill journals")


# =============================================================================
# GET BILL PDF
# =============================================================================
@router.get("/{bill_id}/pdf")
async def get_bill_pdf(
    request: Request,
    bill_id: UUID,
    format: Literal["url", "inline"] = Query(
        "url",
        description="Response format: 'url' returns presigned URL, 'inline' returns PDF bytes",
    ),
):
    """
    Generate PDF for a bill (faktur pembelian).

    **Format options:**
    - `url` (default): Returns presigned URL for download/share (expires in 1 hour)
    - `inline`: Returns PDF bytes directly for browser preview

    **Usage:**
    - For download button: use `?format=url` and redirect to returned URL
    - For inline preview: use `?format=inline` and embed in iframe/viewer
    """
    try:
        ctx = get_user_context(request)
        service = await get_bills_service()

        # Fetch bill with full details
        bill = await service.get_bill_v2(tenant_id=ctx["tenant_id"], bill_id=bill_id)

        if not bill:
            raise HTTPException(status_code=404, detail="Bill not found")

        # Generate PDF
        pdf_service = get_pdf_service()
        pdf_bytes = pdf_service.generate_bill_pdf(bill)

        # Generate filename
        invoice_num = bill.get("invoice_number") or str(bill_id)[:8]
        filename = f"Faktur-{invoice_num}.pdf"

        if format == "inline":
            return StreamingResponse(
                BytesIO(pdf_bytes),
                media_type="application/pdf",
                headers={
                    "Content-Disposition": f'inline; filename="{filename}"',
                    "Cache-Control": "private, max-age=300",
                },
            )

        # Upload to storage and return presigned URL
        storage = get_storage_service()
        file_path = f"{ctx['tenant_id']}/invoices/{bill_id}.pdf"

        url = await storage.upload_bytes(
            content=pdf_bytes,
            file_path=file_path,
            content_type="application/pdf",
            metadata={"bill_id": str(bill_id), "invoice_number": invoice_num},
        )

        # Calculate expiry
        expires_at = datetime.utcnow() + timedelta(seconds=storage.config.url_expiry)

        return {
            "success": True,
            "data": {
                "url": url,
                "expires_at": expires_at.isoformat() + "Z",
                "filename": filename,
            },
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating PDF for bill {bill_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate PDF")


# =============================================================================
# CREATE BILL
# =============================================================================
@router.post("", response_model=CreateBillResponse, status_code=201)
async def create_bill(request: Request, body: CreateBillRequest):
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
                status_code=400, detail="Either vendor_name or vendor_id is required"
            )

        service = await get_bills_service()

        result = await service.create_bill(
            tenant_id=ctx["tenant_id"],
            request=body.model_dump(),
            user_id=ctx["user_id"],
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
async def update_bill(request: Request, bill_id: UUID, body: UpdateBillRequest):
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
            request=body.model_dump(exclude_unset=True),
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
async def delete_bill(request: Request, bill_id: UUID):
    """
    Delete a bill.

    **Restrictions:**
    - Only bills with no payments can be deleted
    - Use void endpoint for bills with payments
    """
    try:
        ctx = get_user_context(request)
        service = await get_bills_service()

        result = await service.delete_bill(tenant_id=ctx["tenant_id"], bill_id=bill_id)

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
@router.post(
    "/{bill_id}/payments", response_model=RecordPaymentResponse, status_code=201
)
async def record_payment(request: Request, bill_id: UUID, body: RecordPaymentRequest):
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
            user_id=ctx["user_id"],
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
async def mark_bill_paid(request: Request, bill_id: UUID, body: MarkPaidRequest):
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
            user_id=ctx["user_id"],
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
async def void_bill(request: Request, bill_id: UUID, body: VoidBillRequest):
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
            user_id=ctx["user_id"],
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
@router.post(
    "/{bill_id}/attachments", response_model=UploadAttachmentResponse, status_code=201
)
async def upload_attachment(
    request: Request,
    bill_id: UUID,
    file: UploadFile = File(..., description="Image or PDF file (max 5MB)"),
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
            raise HTTPException(status_code=400, detail="File size exceeds 5MB limit")

        # Validate file type
        allowed_types = ["image/jpeg", "image/png", "application/pdf"]
        if file.content_type not in allowed_types:
            raise HTTPException(
                status_code=400,
                detail=f"File type not allowed. Allowed: {', '.join(allowed_types)}",
            )

        # TODO: Implement file storage (S3, local, etc.)
        # For now, return placeholder response
        return {
            "success": True,
            "data": {
                "id": str(bill_id),  # placeholder
                "filename": file.filename,
                "url": f"/attachments/{bill_id}/{file.filename}",  # placeholder
            },
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Error uploading attachment for bill {bill_id}: {e}", exc_info=True
        )
        raise HTTPException(status_code=500, detail="Failed to upload attachment")


# =============================================================================
# V2 ENDPOINTS - Extended for Pharmacy
# =============================================================================


@router.post("/v2", response_model=CreateBillResponseV2, status_code=201)
async def create_bill_v2(request: Request, body: CreateBillRequestV2):
    """
    Create a new pharmacy bill with extended fields (V2).

    **Features:**
    - Multi-level discounts: item, invoice, cash
    - Tax calculation: 0%, 11%, or 12%
    - Auto-create vendor if vendor_name provided without vendor_id
    - Auto-generate invoice number (format: PB-YYMM-0001)
    - Pharmacy fields: batch_no, exp_date, bonus_qty

    **Status options:**
    - `draft`: Bill saved but not posted (can be edited)
    - `posted`: Bill posted to accounting (creates AP and journal)

    **Discount rules:**
    - invoice_discount: use percent OR amount (percent takes precedence)
    - cash_discount: use percent OR amount (percent takes precedence)
    """
    try:
        ctx = get_user_context(request)

        if not ctx["user_id"]:
            raise HTTPException(status_code=401, detail="User ID required")

        if not body.vendor_name and not body.vendor_id:
            raise HTTPException(
                status_code=400, detail="Either vendor_name or vendor_id is required"
            )

        service = await get_bills_service()
        result = await service.create_bill_v2(
            tenant_id=ctx["tenant_id"],
            request=body.model_dump(),
            user_id=ctx["user_id"],
        )

        if not result["success"]:
            raise HTTPException(status_code=400, detail=result["message"])

        return result

    except HTTPException:
        raise
    except asyncpg.exceptions.UniqueViolationError as e:
        logger.error(f"Duplicate bill: {e}")
        raise HTTPException(
            status_code=400,
            detail="Nomor faktur sudah digunakan. Gunakan nomor lain atau biarkan kosong untuk auto-generate.",
        )
    except asyncpg.exceptions.ForeignKeyViolationError as e:
        logger.error(f"Foreign key error: {e}")
        raise HTTPException(
            status_code=400,
            detail="Data tidak valid: vendor atau produk tidak ditemukan",
        )
    except asyncpg.exceptions.CheckViolationError as e:
        logger.error(f"Check constraint error: {e}")
        raise HTTPException(
            status_code=400,
            detail="Data tidak valid: nilai di luar batas yang diizinkan",
        )
    except ValueError as e:
        logger.error(f"Validation error: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error creating bill v2: {e}", exc_info=True)
        # Include error type for debugging
        error_type = type(e).__name__
        raise HTTPException(
            status_code=500, detail=f"Gagal membuat faktur: {error_type} - {str(e)}"
        )


@router.post("/{bill_id}/post", response_model=CreateBillResponseV2)
async def post_bill(request: Request, bill_id: UUID):
    """
    Post a draft bill to accounting.

    This action:
    - Creates an AP (Accounts Payable) record
    - Creates a journal entry (DR Inventory/Expense, CR AP)
    - Changes status from 'draft' to 'posted'

    **Important:** Once posted, a bill cannot be edited. Void and recreate if needed.
    """
    try:
        ctx = get_user_context(request)

        if not ctx["user_id"]:
            raise HTTPException(status_code=401, detail="User ID required")

        service = await get_bills_service()
        result = await service.post_bill(
            tenant_id=ctx["tenant_id"], bill_id=bill_id, user_id=ctx["user_id"]
        )

        if not result["success"]:
            raise HTTPException(status_code=400, detail=result["message"])

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error posting bill {bill_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to post bill")


@router.patch("/v2/{bill_id}", response_model=CreateBillResponseV2)
async def update_bill_v2(request: Request, bill_id: UUID, body: UpdateBillRequestV2):
    """
    Update a draft bill (V2).

    **Restrictions:**
    - Only draft bills can be updated
    - Posted, paid, or voided bills cannot be edited

    If items are provided, all existing items will be replaced.
    """
    try:
        ctx = get_user_context(request)

        if not ctx["user_id"]:
            raise HTTPException(status_code=401, detail="User ID required")

        service = await get_bills_service()
        result = await service.update_bill_v2(
            tenant_id=ctx["tenant_id"],
            bill_id=bill_id,
            request=body.model_dump(exclude_unset=True),
            user_id=ctx["user_id"],
        )

        if not result["success"]:
            raise HTTPException(status_code=400, detail=result["message"])

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating bill v2 {bill_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update bill")


@router.get("/v2/{bill_id}", response_model=BillDetailResponse)
async def get_bill_v2(request: Request, bill_id: UUID):
    """
    Get detailed information for a single bill with V2 fields.

    Includes:
    - Extended calculation breakdown (subtotal, discounts, DPP, tax)
    - Pharmacy fields (batch_no, exp_date, bonus_qty)
    - Status v2 (draft, posted, paid, void)
    """
    try:
        ctx = get_user_context(request)
        service = await get_bills_service()

        bill = await service.get_bill_v2(tenant_id=ctx["tenant_id"], bill_id=bill_id)

        if not bill:
            raise HTTPException(status_code=404, detail="Bill not found")

        return {"success": True, "data": bill}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting bill v2 {bill_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get bill")


@router.post("/calculate", response_model=CalculateBillResponse)
async def calculate_bill_totals(request: Request, body: CreateBillRequestV2):
    """
    Preview bill calculation without saving.

    Use this endpoint to show calculated totals in the UI before submitting.
    This is a read-only operation that doesn't modify any data.

    **Returns:**
    - subtotal: Sum of (qty * price) for all items
    - item_discount_total: Sum of item-level discounts
    - invoice_discount_total: Invoice-level discount amount
    - cash_discount_total: Cash/early payment discount
    - dpp: Dasar Pengenaan Pajak (tax base)
    - tax_amount: Calculated tax
    - grand_total: Final total
    """
    try:
        get_user_context(request)  # Validate auth

        result = BillCalculator.calculate(
            items=[item.model_dump() for item in body.items],
            invoice_discount_percent=body.invoice_discount_percent,
            invoice_discount_amount=body.invoice_discount_amount,
            cash_discount_percent=body.cash_discount_percent,
            cash_discount_amount=body.cash_discount_amount,
            tax_rate=body.tax_rate,
            dpp_manual=body.dpp_manual,
        )

        return {"success": True, "calculation": result}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error calculating bill: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to calculate")



# =============================================================================
# ACTIVITY ENDPOINT
# =============================================================================


@router.get("/{bill_id}/activity", response_model=BillActivityResponse)
async def get_bill_activity(request: Request, bill_id: UUID):
    """
    Get activity log / audit trail for a bill.

    Derives activities from:
    - Bill creation (created_at)
    - Bill payments (from bill_payments table)
    - Status changes (voided_at)
    - Updates (updated_at differs from created_at)

    This is a READ-ONLY endpoint.
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        
        async with pool.acquire() as conn:
            # Verify bill exists and get bill data
            bill = await conn.fetchrow(
                """
                SELECT 
                    b.id, b.invoice_number, b.created_at, b.updated_at, 
                    b.voided_at, b.voided_reason, b.created_by,
                    b.status_v2, b.posted_at, b.posted_by, b.amount,
                    u.name as creator_name, u.fullname as creator_fullname
                FROM bills b
                LEFT JOIN "User" u ON b.created_by::text = u.id
                WHERE b.id = $1 AND b.tenant_id = $2
                """,
                bill_id,
                ctx["tenant_id"],
            )
            
            if not bill:
                raise HTTPException(status_code=404, detail="Bill not found")
            
            activities = []
            
            # 1. Bill created activity
            creator_name = bill["creator_fullname"] or bill["creator_name"] or "System"
            activities.append(
                BillActivity(
                    id=f"{bill_id}-created",
                    type="created",
                    description="Faktur dibuat",
                    actor_name=creator_name,
                    timestamp=bill["created_at"].isoformat() if bill["created_at"] else None,
                    amount=bill["amount"],
                    details=f"Invoice #{bill['invoice_number']}"
                )
            )
            
            # 2. Bill posted activity (if status_v2 is posted and posted_at exists)
            if bill["posted_at"] and bill["posted_at"] != bill["created_at"]:
                poster = await conn.fetchrow(
                    """SELECT name, fullname FROM "User" WHERE id = $1""",
                    str(bill["posted_by"]) if bill["posted_by"] else None
                )
                poster_name = "System"
                if poster:
                    poster_name = poster["fullname"] or poster["name"] or "System"
                
                activities.append(
                    BillActivity(
                        id=f"{bill_id}-posted",
                        type="status_changed",
                        description="Faktur diposting",
                        actor_name=poster_name,
                        timestamp=bill["posted_at"].isoformat() if bill["posted_at"] else None,
                        old_value="draft",
                        new_value="posted"
                    )
                )
            
            # 3. Payment activities
            payments = await conn.fetch(
                """
                SELECT 
                    bp.id, bp.amount, bp.payment_date, bp.payment_method,
                    bp.reference, bp.notes, bp.created_at, bp.created_by,
                    u.name as payer_name, u.fullname as payer_fullname
                FROM bill_payments bp
                LEFT JOIN "User" u ON bp.created_by::text = u.id
                WHERE bp.bill_id = $1
                ORDER BY bp.created_at ASC
                """,
                bill_id
            )
            
            for payment in payments:
                payer_name = payment["payer_fullname"] or payment["payer_name"] or "System"
                method_display = {
                    "cash": "tunai",
                    "transfer": "transfer",
                    "check": "cek/giro",
                    "other": "lainnya"
                }.get(payment["payment_method"], payment["payment_method"])
                
                activities.append(
                    BillActivity(
                        id=str(payment["id"]),
                        type="payment",
                        description=f"Pembayaran {method_display}",
                        actor_name=payer_name,
                        timestamp=payment["created_at"].isoformat() if payment["created_at"] else None,
                        amount=payment["amount"],
                        details=payment["reference"] or payment["notes"]
                    )
                )
            
            # 4. Voided activity
            if bill["voided_at"]:
                activities.append(
                    BillActivity(
                        id=f"{bill_id}-voided",
                        type="voided",
                        description="Faktur dibatalkan",
                        actor_name="System",  # void doesn't track who voided
                        timestamp=bill["voided_at"].isoformat(),
                        details=bill["voided_reason"]
                    )
                )
            
            # 5. Updated activity (if updated_at is significantly different from created_at)
            if bill["updated_at"] and bill["created_at"]:
                time_diff = (bill["updated_at"] - bill["created_at"]).total_seconds()
                # Only show update if more than 1 minute after creation
                if time_diff > 60:
                    activities.append(
                        BillActivity(
                            id=f"{bill_id}-updated",
                            type="updated",
                            description="Faktur diperbarui",
                            actor_name="System",
                            timestamp=bill["updated_at"].isoformat()
                        )
                    )
            
            # Sort by timestamp descending (most recent first)
            activities.sort(
                key=lambda x: x.timestamp if x.timestamp else "",
                reverse=True
            )
            
            return BillActivityResponse(activities=activities)
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting bill activity for {bill_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get bill activity")
