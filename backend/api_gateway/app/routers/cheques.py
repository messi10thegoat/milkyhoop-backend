"""
Cheque Management Router - Manajemen Giro/Cek Mundur

Endpoints for managing post-dated cheques received from customers or issued to vendors.
HAS JOURNAL ENTRIES - See journal mappings in comments.

Account Codes:
- 1-10600: Giro Diterima (Cheques Receivable - Asset)
- 2-10500: Giro Diberikan (Cheques Payable - Liability)

Endpoints:
# Cheque CRUD
- GET    /cheques                           - List all cheques
- GET    /cheques/{id}                      - Detail with history
- POST   /cheques/receive                   - Record received cheque
- POST   /cheques/issue                     - Record issued cheque
- PATCH  /cheques/{id}                      - Update (pending only)
- DELETE /cheques/{id}                      - Delete (pending only)

# Status Changes
- POST   /cheques/{id}/deposit              - Deposit to bank
- POST   /cheques/{id}/clear                - Mark as cleared
- POST   /cheques/{id}/bounce               - Mark as bounced
- POST   /cheques/{id}/cancel               - Cancel cheque
- POST   /cheques/{id}/replace              - Replace bounced cheque

# Queries
- GET    /cheques/pending                   - Pending cheques
- GET    /cheques/due-today                 - Due for deposit today
- GET    /cheques/upcoming                  - Upcoming (next 30 days)
- GET    /cheques/bounced                   - Bounced cheques
- GET    /cheques/by-customer/{id}          - Customer's cheques
- GET    /cheques/by-vendor/{id}            - Vendor's cheques

# Reports
- GET    /cheques/summary                   - Summary by status
- GET    /cheques/aging                     - Aging of pending cheques
"""

from fastapi import APIRouter, HTTPException, Request, Query
from typing import Optional, Literal
from uuid import UUID
import logging
import asyncpg
from datetime import date
import uuid as uuid_module

from ..schemas.cheques import (
    ReceiveChequeRequest,
    IssueChequeRequest,
    UpdateChequeRequest,
    DepositChequeRequest,
    ClearChequeRequest,
    BounceChequeRequest,
    CancelChequeRequest,
    ReplaceChequeRequest,
    ChequeListResponse,
    ChequeDetailResponse,
    ChequeSummaryResponse,
    ChequeAgingResponse,
    ChequeActionResponse,
    ChequeResponse,
)
from ..config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

# Connection pool
_pool: Optional[asyncpg.Pool] = None

# Account codes
CHEQUES_RECEIVABLE = "1-10600"  # Giro Diterima
CHEQUES_PAYABLE = "2-10500"    # Giro Diberikan
AR_ACCOUNT = "1-10300"          # Piutang Usaha
AP_ACCOUNT = "2-10100"          # Hutang Usaha
OTHER_INCOME = "4-20100"        # Pendapatan Lain-lain


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
        "user_id": UUID(user_id) if user_id else None
    }


async def get_account_id(conn, tenant_id: str, account_code: str) -> UUID:
    """Get account ID from chart of accounts."""
    account = await conn.fetchrow("""
        SELECT id FROM chart_of_accounts
        WHERE tenant_id = $1 AND account_code = $2
    """, tenant_id, account_code)

    if not account:
        raise HTTPException(status_code=400, detail=f"Account {account_code} not found. Run seed_cheque_accounts first.")

    return account["id"]


async def create_journal_entry(
    conn,
    tenant_id: str,
    journal_date: date,
    description: str,
    source_type: str,
    source_id: UUID,
    lines: list,
    created_by: UUID
) -> UUID:
    """Create a journal entry with lines."""
    journal_id = uuid_module.uuid4()
    trace_id = uuid_module.uuid4()

    total_amount = sum(line["debit"] for line in lines)

    # Generate journal number
    journal_number = await conn.fetchval("""
        SELECT 'CHQ-' || TO_CHAR($1, 'YYMM') || '-' ||
               LPAD(COALESCE(
                   (SELECT COUNT(*) + 1 FROM journal_entries
                    WHERE tenant_id = $2 AND journal_number LIKE 'CHQ-' || TO_CHAR($1, 'YYMM') || '%'),
                   1
               )::TEXT, 4, '0')
    """, journal_date, tenant_id)

    await conn.execute("""
        INSERT INTO journal_entries (
            id, tenant_id, journal_number, journal_date, description,
            source_type, source_id, trace_id,
            total_debit, total_credit, status, posted_at, posted_by, created_by
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $9, 'POSTED', NOW(), $10, $10)
    """,
        journal_id,
        tenant_id,
        journal_number,
        journal_date,
        description,
        source_type,
        source_id,
        str(trace_id),
        total_amount,
        created_by
    )

    # Insert journal lines
    for idx, line in enumerate(lines, 1):
        await conn.execute("""
            INSERT INTO journal_lines (
                id, journal_id, line_number, account_id, debit, credit, memo
            ) VALUES ($1, $2, $3, $4, $5, $6, $7)
        """,
            uuid_module.uuid4(),
            journal_id,
            idx,
            line["account_id"],
            line["debit"],
            line["credit"],
            line.get("memo", "")
        )

    return journal_id


# =============================================================================
# LIST CHEQUES
# =============================================================================

@router.get("", response_model=ChequeListResponse)
async def list_cheques(
    request: Request,
    cheque_type: Optional[Literal["received", "issued"]] = Query(None),
    status: Optional[Literal["pending", "deposited", "cleared", "bounced", "cancelled", "replaced"]] = Query(None),
    from_date: Optional[date] = Query(None),
    to_date: Optional[date] = Query(None),
    search: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
):
    """List all cheques with filters."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            conditions = ["tenant_id = $1"]
            params = [ctx["tenant_id"]]
            param_idx = 2

            if cheque_type:
                conditions.append(f"cheque_type = ${param_idx}")
                params.append(cheque_type)
                param_idx += 1

            if status:
                conditions.append(f"status = ${param_idx}")
                params.append(status)
                param_idx += 1

            if from_date:
                conditions.append(f"cheque_date >= ${param_idx}")
                params.append(from_date)
                param_idx += 1

            if to_date:
                conditions.append(f"cheque_date <= ${param_idx}")
                params.append(to_date)
                param_idx += 1

            if search:
                conditions.append(f"(cheque_number ILIKE ${param_idx} OR party_name ILIKE ${param_idx})")
                params.append(f"%{search}%")
                param_idx += 1

            where_clause = " AND ".join(conditions)

            count_query = f"SELECT COUNT(*) FROM cheques WHERE {where_clause}"
            total = await conn.fetchval(count_query, *params)

            query = f"""
                SELECT id, cheque_number, cheque_date, bank_name, cheque_type,
                       amount, party_name, status, reference_number,
                       (cheque_date - CURRENT_DATE) as days_until_due
                FROM cheques
                WHERE {where_clause}
                ORDER BY cheque_date ASC
                LIMIT ${param_idx} OFFSET ${param_idx + 1}
            """
            params.extend([limit, skip])

            rows = await conn.fetch(query, *params)

            items = [
                {
                    "id": str(row["id"]),
                    "cheque_number": row["cheque_number"],
                    "cheque_date": row["cheque_date"],
                    "bank_name": row["bank_name"],
                    "cheque_type": row["cheque_type"],
                    "amount": row["amount"],
                    "party_name": row["party_name"],
                    "status": row["status"],
                    "reference_number": row["reference_number"],
                    "days_until_due": row["days_until_due"],
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
        logger.error(f"Error listing cheques: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list cheques")


@router.get("/pending", response_model=ChequeListResponse)
async def list_pending_cheques(
    request: Request,
    cheque_type: Optional[Literal["received", "issued"]] = Query(None),
):
    """List pending cheques."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            conditions = ["tenant_id = $1", "status = 'pending'"]
            params = [ctx["tenant_id"]]

            if cheque_type:
                conditions.append("cheque_type = $2")
                params.append(cheque_type)

            where_clause = " AND ".join(conditions)

            rows = await conn.fetch(f"""
                SELECT id, cheque_number, cheque_date, bank_name, cheque_type,
                       amount, party_name, status, reference_number,
                       (cheque_date - CURRENT_DATE) as days_until_due
                FROM cheques
                WHERE {where_clause}
                ORDER BY cheque_date ASC
            """, *params)

            return {
                "items": [
                    {
                        "id": str(row["id"]),
                        "cheque_number": row["cheque_number"],
                        "cheque_date": row["cheque_date"],
                        "bank_name": row["bank_name"],
                        "cheque_type": row["cheque_type"],
                        "amount": row["amount"],
                        "party_name": row["party_name"],
                        "status": row["status"],
                        "reference_number": row["reference_number"],
                        "days_until_due": row["days_until_due"],
                    }
                    for row in rows
                ],
                "total": len(rows),
                "has_more": False
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing pending cheques: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list pending cheques")


@router.get("/due-today")
async def list_due_today(request: Request):
    """List cheques due for deposit today."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT * FROM get_cheques_due_for_deposit($1, CURRENT_DATE)
            """, ctx["tenant_id"])

            return {
                "success": True,
                "data": [
                    {
                        "id": str(row["id"]),
                        "cheque_number": row["cheque_number"],
                        "cheque_date": row["cheque_date"],
                        "bank_name": row["bank_name"],
                        "amount": row["amount"],
                        "party_name": row["party_name"],
                        "customer_id": str(row["customer_id"]) if row["customer_id"] else None,
                        "days_until_due": row["days_until_due"]
                    }
                    for row in rows
                ],
                "count": len(rows)
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing due today: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list cheques due today")


@router.get("/upcoming")
async def list_upcoming_cheques(
    request: Request,
    days: int = Query(30, ge=1, le=365),
    cheque_type: Optional[Literal["received", "issued"]] = Query(None),
):
    """List upcoming cheques."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT * FROM get_upcoming_cheques($1, $2, $3)
            """, ctx["tenant_id"], days, cheque_type)

            return {
                "success": True,
                "data": [
                    {
                        "id": str(row["id"]),
                        "cheque_number": row["cheque_number"],
                        "cheque_date": row["cheque_date"],
                        "cheque_type": row["cheque_type"],
                        "amount": row["amount"],
                        "party_name": row["party_name"],
                        "days_until_due": row["days_until_due"]
                    }
                    for row in rows
                ],
                "days": days
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing upcoming cheques: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list upcoming cheques")


@router.get("/bounced")
async def list_bounced_cheques(request: Request):
    """List bounced cheques."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT id, cheque_number, cheque_date, bank_name, cheque_type,
                       amount, party_name, bounce_reason, bounce_charges,
                       bounced_date, replacement_cheque_id
                FROM cheques
                WHERE tenant_id = $1 AND status = 'bounced'
                ORDER BY bounced_date DESC
            """, ctx["tenant_id"])

            return {
                "success": True,
                "data": [
                    {
                        "id": str(row["id"]),
                        "cheque_number": row["cheque_number"],
                        "cheque_date": row["cheque_date"],
                        "bank_name": row["bank_name"],
                        "cheque_type": row["cheque_type"],
                        "amount": row["amount"],
                        "party_name": row["party_name"],
                        "bounce_reason": row["bounce_reason"],
                        "bounce_charges": row["bounce_charges"],
                        "bounced_date": row["bounced_date"],
                        "replacement_cheque_id": str(row["replacement_cheque_id"]) if row["replacement_cheque_id"] else None
                    }
                    for row in rows
                ],
                "count": len(rows)
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing bounced cheques: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list bounced cheques")


@router.get("/by-customer/{customer_id}")
async def list_customer_cheques(
    request: Request,
    customer_id: UUID,
    status: Optional[str] = Query(None),
):
    """List cheques by customer."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT * FROM get_customer_cheques($1, $2)
            """, customer_id, status)

            return {
                "success": True,
                "data": [
                    {
                        "id": str(row["id"]),
                        "cheque_number": row["cheque_number"],
                        "cheque_date": row["cheque_date"],
                        "bank_name": row["bank_name"],
                        "amount": row["amount"],
                        "status": row["status"],
                        "reference_number": row["reference_number"],
                        "deposited_date": row["deposited_date"],
                        "cleared_date": row["cleared_date"]
                    }
                    for row in rows
                ]
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing customer cheques: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list customer cheques")


@router.get("/by-vendor/{vendor_id}")
async def list_vendor_cheques(
    request: Request,
    vendor_id: UUID,
    status: Optional[str] = Query(None),
):
    """List cheques by vendor."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT * FROM get_vendor_cheques($1, $2)
            """, vendor_id, status)

            return {
                "success": True,
                "data": [
                    {
                        "id": str(row["id"]),
                        "cheque_number": row["cheque_number"],
                        "cheque_date": row["cheque_date"],
                        "bank_name": row["bank_name"],
                        "amount": row["amount"],
                        "status": row["status"],
                        "reference_number": row["reference_number"],
                        "issued_date": row["issued_date"],
                        "cleared_date": row["cleared_date"]
                    }
                    for row in rows
                ]
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing vendor cheques: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list vendor cheques")


# =============================================================================
# GET CHEQUE DETAIL
# =============================================================================

@router.get("/{cheque_id}", response_model=ChequeDetailResponse)
async def get_cheque(request: Request, cheque_id: UUID):
    """Get cheque detail with history."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT c.*,
                       cu.name as customer_name,
                       v.name as vendor_name,
                       ba.account_name as bank_account_name
                FROM cheques c
                LEFT JOIN customers cu ON c.customer_id = cu.id
                LEFT JOIN vendors v ON c.vendor_id = v.id
                LEFT JOIN bank_accounts ba ON c.bank_account_id = ba.id
                WHERE c.id = $1 AND c.tenant_id = $2
            """, cheque_id, ctx["tenant_id"])

            if not row:
                raise HTTPException(status_code=404, detail="Cheque not found")

            # Get status history
            history = await conn.fetch("""
                SELECT id, old_status, new_status, changed_at, changed_by, notes, journal_id
                FROM cheque_status_history
                WHERE cheque_id = $1
                ORDER BY changed_at DESC
            """, cheque_id)

            return {
                "success": True,
                "data": {
                    "id": str(row["id"]),
                    "cheque_number": row["cheque_number"],
                    "cheque_date": row["cheque_date"],
                    "bank_name": row["bank_name"],
                    "bank_branch": row["bank_branch"],
                    "cheque_type": row["cheque_type"],
                    "amount": row["amount"],
                    "customer_id": str(row["customer_id"]) if row["customer_id"] else None,
                    "customer_name": row["customer_name"],
                    "vendor_id": str(row["vendor_id"]) if row["vendor_id"] else None,
                    "vendor_name": row["vendor_name"],
                    "party_name": row["party_name"],
                    "bank_account_id": str(row["bank_account_id"]) if row["bank_account_id"] else None,
                    "bank_account_name": row["bank_account_name"],
                    "reference_type": row["reference_type"],
                    "reference_id": str(row["reference_id"]) if row["reference_id"] else None,
                    "reference_number": row["reference_number"],
                    "status": row["status"],
                    "received_date": row["received_date"],
                    "issued_date": row["issued_date"],
                    "deposited_date": row["deposited_date"],
                    "cleared_date": row["cleared_date"],
                    "bounced_date": row["bounced_date"],
                    "receipt_journal_id": str(row["receipt_journal_id"]) if row["receipt_journal_id"] else None,
                    "deposit_journal_id": str(row["deposit_journal_id"]) if row["deposit_journal_id"] else None,
                    "clear_journal_id": str(row["clear_journal_id"]) if row["clear_journal_id"] else None,
                    "bounce_journal_id": str(row["bounce_journal_id"]) if row["bounce_journal_id"] else None,
                    "replacement_cheque_id": str(row["replacement_cheque_id"]) if row["replacement_cheque_id"] else None,
                    "bounce_charges": row["bounce_charges"],
                    "bounce_reason": row["bounce_reason"],
                    "notes": row["notes"],
                    "created_at": row["created_at"].isoformat(),
                    "updated_at": row["updated_at"].isoformat(),
                    "created_by": str(row["created_by"]) if row["created_by"] else None,
                    "history": [
                        {
                            "id": str(h["id"]),
                            "old_status": h["old_status"],
                            "new_status": h["new_status"],
                            "changed_at": h["changed_at"],
                            "changed_by": str(h["changed_by"]) if h["changed_by"] else None,
                            "notes": h["notes"],
                            "journal_id": str(h["journal_id"]) if h["journal_id"] else None
                        }
                        for h in history
                    ]
                }
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting cheque: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get cheque")


# =============================================================================
# RECEIVE CHEQUE (from Customer)
# =============================================================================

@router.post("/receive", response_model=ChequeActionResponse, status_code=201)
async def receive_cheque(request: Request, body: ReceiveChequeRequest):
    """
    Record a received cheque from customer.

    Journal Entry:
    Dr. Giro Diterima (1-10600)          amount
        Cr. Piutang Usaha (1-10300)          amount
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                # Ensure cheque accounts exist
                await conn.execute("SELECT seed_cheque_accounts($1)", ctx["tenant_id"])

                # Get account IDs
                cheques_receivable_id = await get_account_id(conn, ctx["tenant_id"], CHEQUES_RECEIVABLE)
                ar_account_id = await get_account_id(conn, ctx["tenant_id"], AR_ACCOUNT)

                # Create cheque
                cheque_id = uuid_module.uuid4()

                await conn.execute("""
                    INSERT INTO cheques (
                        id, tenant_id, cheque_number, cheque_date, bank_name, bank_branch,
                        cheque_type, amount, customer_id, party_name,
                        reference_type, reference_id, reference_number,
                        received_date, status, notes, created_by
                    ) VALUES ($1, $2, $3, $4, $5, $6, 'received', $7, $8, $9, $10, $11, $12, $13, 'pending', $14, $15)
                """,
                    cheque_id,
                    ctx["tenant_id"],
                    body.cheque_number,
                    body.cheque_date,
                    body.bank_name,
                    body.bank_branch,
                    body.amount,
                    body.customer_id,
                    body.party_name,
                    body.reference_type,
                    body.reference_id,
                    body.reference_number,
                    body.received_date,
                    body.notes,
                    ctx["user_id"]
                )

                # Create journal entry
                # Dr. Giro Diterima, Cr. Piutang Usaha
                journal_id = await create_journal_entry(
                    conn,
                    ctx["tenant_id"],
                    body.received_date,
                    f"Giro Diterima - {body.cheque_number} dari {body.party_name}",
                    "CHEQUE_RECEIVED",
                    cheque_id,
                    [
                        {"account_id": cheques_receivable_id, "debit": body.amount, "credit": 0, "memo": f"Giro {body.cheque_number}"},
                        {"account_id": ar_account_id, "debit": 0, "credit": body.amount, "memo": f"Pembayaran {body.party_name}"}
                    ],
                    ctx["user_id"]
                )

                # Update cheque with journal reference
                await conn.execute("""
                    UPDATE cheques SET receipt_journal_id = $1 WHERE id = $2
                """, journal_id, cheque_id)

                return {
                    "success": True,
                    "message": "Cheque received successfully",
                    "data": {
                        "id": str(cheque_id),
                        "cheque_number": body.cheque_number,
                        "status": "pending",
                        "receipt_journal_id": str(journal_id)
                    }
                }

    except HTTPException:
        raise
    except asyncpg.UniqueViolationError:
        raise HTTPException(status_code=400, detail="Cheque number already exists")
    except Exception as e:
        logger.error(f"Error receiving cheque: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to receive cheque")


# =============================================================================
# ISSUE CHEQUE (to Vendor)
# =============================================================================

@router.post("/issue", response_model=ChequeActionResponse, status_code=201)
async def issue_cheque(request: Request, body: IssueChequeRequest):
    """
    Record an issued cheque to vendor.

    Journal Entry:
    Dr. Hutang Usaha (2-10100)           amount
        Cr. Giro Diberikan (2-10500)         amount
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                # Ensure cheque accounts exist
                await conn.execute("SELECT seed_cheque_accounts($1)", ctx["tenant_id"])

                # Get account IDs
                cheques_payable_id = await get_account_id(conn, ctx["tenant_id"], CHEQUES_PAYABLE)
                ap_account_id = await get_account_id(conn, ctx["tenant_id"], AP_ACCOUNT)

                # Create cheque
                cheque_id = uuid_module.uuid4()

                await conn.execute("""
                    INSERT INTO cheques (
                        id, tenant_id, cheque_number, cheque_date, bank_name, bank_branch,
                        cheque_type, amount, vendor_id, party_name, bank_account_id,
                        reference_type, reference_id, reference_number,
                        issued_date, status, notes, created_by
                    ) VALUES ($1, $2, $3, $4, $5, $6, 'issued', $7, $8, $9, $10, $11, $12, $13, $14, 'pending', $15, $16)
                """,
                    cheque_id,
                    ctx["tenant_id"],
                    body.cheque_number,
                    body.cheque_date,
                    body.bank_name,
                    body.bank_branch,
                    body.amount,
                    body.vendor_id,
                    body.party_name,
                    body.bank_account_id,
                    body.reference_type,
                    body.reference_id,
                    body.reference_number,
                    body.issued_date,
                    body.notes,
                    ctx["user_id"]
                )

                # Create journal entry
                # Dr. Hutang Usaha, Cr. Giro Diberikan
                journal_id = await create_journal_entry(
                    conn,
                    ctx["tenant_id"],
                    body.issued_date,
                    f"Giro Diberikan - {body.cheque_number} ke {body.party_name}",
                    "CHEQUE_ISSUED",
                    cheque_id,
                    [
                        {"account_id": ap_account_id, "debit": body.amount, "credit": 0, "memo": f"Pembayaran ke {body.party_name}"},
                        {"account_id": cheques_payable_id, "debit": 0, "credit": body.amount, "memo": f"Giro {body.cheque_number}"}
                    ],
                    ctx["user_id"]
                )

                # Update cheque with journal reference
                await conn.execute("""
                    UPDATE cheques SET receipt_journal_id = $1 WHERE id = $2
                """, journal_id, cheque_id)

                return {
                    "success": True,
                    "message": "Cheque issued successfully",
                    "data": {
                        "id": str(cheque_id),
                        "cheque_number": body.cheque_number,
                        "status": "pending",
                        "receipt_journal_id": str(journal_id)
                    }
                }

    except HTTPException:
        raise
    except asyncpg.UniqueViolationError:
        raise HTTPException(status_code=400, detail="Cheque number already exists")
    except Exception as e:
        logger.error(f"Error issuing cheque: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to issue cheque")


# =============================================================================
# DEPOSIT CHEQUE
# =============================================================================

@router.post("/{cheque_id}/deposit", response_model=ChequeActionResponse)
async def deposit_cheque(request: Request, cheque_id: UUID, body: DepositChequeRequest):
    """
    Deposit a received cheque to bank.

    Journal Entry:
    Dr. Bank (1-10200)                   amount
        Cr. Giro Diterima (1-10600)          amount
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                # Get cheque
                cheque = await conn.fetchrow("""
                    SELECT * FROM cheques
                    WHERE id = $1 AND tenant_id = $2 AND status = 'pending' AND cheque_type = 'received'
                """, cheque_id, ctx["tenant_id"])

                if not cheque:
                    raise HTTPException(status_code=404, detail="Pending received cheque not found")

                # Get bank account's COA
                bank_account = await conn.fetchrow("""
                    SELECT coa_id FROM bank_accounts WHERE id = $1
                """, body.bank_account_id)

                if not bank_account:
                    raise HTTPException(status_code=400, detail="Bank account not found")

                cheques_receivable_id = await get_account_id(conn, ctx["tenant_id"], CHEQUES_RECEIVABLE)

                # Create journal entry
                # Dr. Bank, Cr. Giro Diterima
                journal_id = await create_journal_entry(
                    conn,
                    ctx["tenant_id"],
                    body.deposited_date,
                    f"Setoran Giro - {cheque['cheque_number']}",
                    "CHEQUE_DEPOSIT",
                    cheque_id,
                    [
                        {"account_id": bank_account["coa_id"], "debit": cheque["amount"], "credit": 0, "memo": f"Setoran Giro {cheque['cheque_number']}"},
                        {"account_id": cheques_receivable_id, "debit": 0, "credit": cheque["amount"], "memo": f"Giro {cheque['cheque_number']}"}
                    ],
                    ctx["user_id"]
                )

                # Update cheque
                await conn.execute("""
                    UPDATE cheques
                    SET status = 'deposited', deposited_date = $1, deposit_journal_id = $2,
                        bank_account_id = $3, updated_at = NOW()
                    WHERE id = $4
                """, body.deposited_date, journal_id, body.bank_account_id, cheque_id)

                return {
                    "success": True,
                    "message": "Cheque deposited",
                    "data": {
                        "status": "deposited",
                        "deposit_journal_id": str(journal_id)
                    }
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error depositing cheque: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to deposit cheque")


# =============================================================================
# CLEAR CHEQUE
# =============================================================================

@router.post("/{cheque_id}/clear", response_model=ChequeActionResponse)
async def clear_cheque(request: Request, cheque_id: UUID, body: ClearChequeRequest):
    """Mark a cheque as cleared."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                # Get cheque
                cheque = await conn.fetchrow("""
                    SELECT * FROM cheques
                    WHERE id = $1 AND tenant_id = $2 AND status = 'deposited'
                """, cheque_id, ctx["tenant_id"])

                if not cheque:
                    raise HTTPException(status_code=404, detail="Deposited cheque not found")

                # Update cheque (no additional journal for simple flow)
                await conn.execute("""
                    UPDATE cheques
                    SET status = 'cleared', cleared_date = $1, updated_at = NOW()
                    WHERE id = $2
                """, body.cleared_date, cheque_id)

                return {
                    "success": True,
                    "message": "Cheque cleared",
                    "data": {"status": "cleared"}
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error clearing cheque: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to clear cheque")


# =============================================================================
# BOUNCE CHEQUE
# =============================================================================

@router.post("/{cheque_id}/bounce", response_model=ChequeActionResponse)
async def bounce_cheque(request: Request, cheque_id: UUID, body: BounceChequeRequest):
    """
    Mark a cheque as bounced and reverse entries.

    Journal Entries (for received cheque):
    1. Reverse deposit:
       Dr. Giro Diterima (1-10600)          amount
           Cr. Bank (1-10200)                   amount

    2. Reinstate AR:
       Dr. Piutang Usaha (1-10300)          amount
           Cr. Giro Diterima (1-10600)          amount

    3. Bounce charges (if any):
       Dr. Piutang Usaha (1-10300)          bounce_charges
           Cr. Pendapatan Lain (4-20100)        bounce_charges
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                # Get cheque
                cheque = await conn.fetchrow("""
                    SELECT c.*, ba.coa_id as bank_coa_id
                    FROM cheques c
                    LEFT JOIN bank_accounts ba ON c.bank_account_id = ba.id
                    WHERE c.id = $1 AND c.tenant_id = $2 AND c.status IN ('deposited', 'pending')
                """, cheque_id, ctx["tenant_id"])

                if not cheque:
                    raise HTTPException(status_code=404, detail="Cheque not found or invalid status")

                journal_ids = []

                if cheque["cheque_type"] == "received":
                    cheques_receivable_id = await get_account_id(conn, ctx["tenant_id"], CHEQUES_RECEIVABLE)
                    ar_account_id = await get_account_id(conn, ctx["tenant_id"], AR_ACCOUNT)

                    # If deposited, reverse the deposit first
                    if cheque["status"] == "deposited" and cheque["bank_coa_id"]:
                        journal_id = await create_journal_entry(
                            conn,
                            ctx["tenant_id"],
                            body.bounced_date,
                            f"Reversal Setoran Giro Tolak - {cheque['cheque_number']}",
                            "CHEQUE_BOUNCE_REVERSAL",
                            cheque_id,
                            [
                                {"account_id": cheques_receivable_id, "debit": cheque["amount"], "credit": 0, "memo": "Reversal setoran giro tolak"},
                                {"account_id": cheque["bank_coa_id"], "debit": 0, "credit": cheque["amount"], "memo": "Reversal setoran giro tolak"}
                            ],
                            ctx["user_id"]
                        )
                        journal_ids.append(journal_id)

                    # Reinstate AR
                    journal_id = await create_journal_entry(
                        conn,
                        ctx["tenant_id"],
                        body.bounced_date,
                        f"Pemulihan Piutang - Giro Tolak {cheque['cheque_number']}",
                        "CHEQUE_BOUNCE_AR",
                        cheque_id,
                        [
                            {"account_id": ar_account_id, "debit": cheque["amount"], "credit": 0, "memo": f"Giro tolak {cheque['cheque_number']}"},
                            {"account_id": cheques_receivable_id, "debit": 0, "credit": cheque["amount"], "memo": f"Giro tolak {cheque['cheque_number']}"}
                        ],
                        ctx["user_id"]
                    )
                    journal_ids.append(journal_id)

                    # Bounce charges
                    if body.bounce_charges > 0:
                        other_income_id = await get_account_id(conn, ctx["tenant_id"], OTHER_INCOME)
                        journal_id = await create_journal_entry(
                            conn,
                            ctx["tenant_id"],
                            body.bounced_date,
                            f"Biaya Giro Tolak - {cheque['cheque_number']}",
                            "CHEQUE_BOUNCE_CHARGES",
                            cheque_id,
                            [
                                {"account_id": ar_account_id, "debit": body.bounce_charges, "credit": 0, "memo": "Biaya giro tolak"},
                                {"account_id": other_income_id, "debit": 0, "credit": body.bounce_charges, "memo": "Pendapatan biaya giro tolak"}
                            ],
                            ctx["user_id"]
                        )
                        journal_ids.append(journal_id)

                # Update cheque
                await conn.execute("""
                    UPDATE cheques
                    SET status = 'bounced', bounced_date = $1, bounce_reason = $2,
                        bounce_charges = $3, bounce_journal_id = $4, updated_at = NOW()
                    WHERE id = $5
                """, body.bounced_date, body.bounce_reason, body.bounce_charges, journal_ids[-1] if journal_ids else None, cheque_id)

                return {
                    "success": True,
                    "message": "Cheque marked as bounced",
                    "data": {
                        "status": "bounced",
                        "bounce_journal_id": str(journal_ids[-1]) if journal_ids else None
                    }
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error bouncing cheque: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to bounce cheque")


# =============================================================================
# CANCEL CHEQUE
# =============================================================================

@router.post("/{cheque_id}/cancel", response_model=ChequeActionResponse)
async def cancel_cheque(request: Request, cheque_id: UUID, body: CancelChequeRequest):
    """Cancel a pending cheque (reverses initial journal)."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                cheque = await conn.fetchrow("""
                    SELECT * FROM cheques
                    WHERE id = $1 AND tenant_id = $2 AND status = 'pending'
                """, cheque_id, ctx["tenant_id"])

                if not cheque:
                    raise HTTPException(status_code=404, detail="Pending cheque not found")

                # Reverse the initial journal entry
                if cheque["cheque_type"] == "received":
                    cheques_receivable_id = await get_account_id(conn, ctx["tenant_id"], CHEQUES_RECEIVABLE)
                    ar_account_id = await get_account_id(conn, ctx["tenant_id"], AR_ACCOUNT)

                    # Reverse: Dr. AR, Cr. Giro Diterima
                    await create_journal_entry(
                        conn,
                        ctx["tenant_id"],
                        date.today(),
                        f"Pembatalan Giro - {cheque['cheque_number']}",
                        "CHEQUE_CANCEL",
                        cheque_id,
                        [
                            {"account_id": ar_account_id, "debit": cheque["amount"], "credit": 0, "memo": f"Pembatalan giro {cheque['cheque_number']}"},
                            {"account_id": cheques_receivable_id, "debit": 0, "credit": cheque["amount"], "memo": f"Pembatalan giro {cheque['cheque_number']}"}
                        ],
                        ctx["user_id"]
                    )
                else:
                    cheques_payable_id = await get_account_id(conn, ctx["tenant_id"], CHEQUES_PAYABLE)
                    ap_account_id = await get_account_id(conn, ctx["tenant_id"], AP_ACCOUNT)

                    # Reverse: Dr. Giro Diberikan, Cr. AP
                    await create_journal_entry(
                        conn,
                        ctx["tenant_id"],
                        date.today(),
                        f"Pembatalan Giro - {cheque['cheque_number']}",
                        "CHEQUE_CANCEL",
                        cheque_id,
                        [
                            {"account_id": cheques_payable_id, "debit": cheque["amount"], "credit": 0, "memo": f"Pembatalan giro {cheque['cheque_number']}"},
                            {"account_id": ap_account_id, "debit": 0, "credit": cheque["amount"], "memo": f"Pembatalan giro {cheque['cheque_number']}"}
                        ],
                        ctx["user_id"]
                    )

                # Update cheque
                await conn.execute("""
                    UPDATE cheques
                    SET status = 'cancelled', notes = COALESCE(notes, '') || ' | Cancelled: ' || $1, updated_at = NOW()
                    WHERE id = $2
                """, body.reason, cheque_id)

                return {
                    "success": True,
                    "message": "Cheque cancelled",
                    "data": {"status": "cancelled"}
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error cancelling cheque: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to cancel cheque")


# =============================================================================
# REPORTS
# =============================================================================

@router.get("/summary", response_model=ChequeSummaryResponse)
async def get_cheque_summary(request: Request):
    """Get cheque summary by status."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT * FROM get_cheque_summary($1)
            """, ctx["tenant_id"])

            summary = {
                "received_pending": 0,
                "received_pending_amount": 0,
                "received_deposited": 0,
                "received_deposited_amount": 0,
                "issued_pending": 0,
                "issued_pending_amount": 0,
                "bounced_count": 0,
                "bounced_amount": 0,
                "due_today_count": 0,
                "due_today_amount": 0,
            }

            for row in rows:
                if row["cheque_type"] == "received" and row["status"] == "pending":
                    summary["received_pending"] = row["count"]
                    summary["received_pending_amount"] = row["total_amount"]
                elif row["cheque_type"] == "received" and row["status"] == "deposited":
                    summary["received_deposited"] = row["count"]
                    summary["received_deposited_amount"] = row["total_amount"]
                elif row["cheque_type"] == "issued" and row["status"] == "pending":
                    summary["issued_pending"] = row["count"]
                    summary["issued_pending_amount"] = row["total_amount"]
                elif row["status"] == "bounced":
                    summary["bounced_count"] += row["count"]
                    summary["bounced_amount"] += row["total_amount"]

            # Get due today
            due_today = await conn.fetch("""
                SELECT COUNT(*) as count, COALESCE(SUM(amount), 0) as total
                FROM cheques
                WHERE tenant_id = $1 AND status = 'pending' AND cheque_date <= CURRENT_DATE
            """, ctx["tenant_id"])

            if due_today:
                summary["due_today_count"] = due_today[0]["count"]
                summary["due_today_amount"] = due_today[0]["total"]

            return {
                "success": True,
                "data": summary
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting cheque summary: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get cheque summary")


@router.get("/aging", response_model=ChequeAgingResponse)
async def get_cheque_aging(
    request: Request,
    cheque_type: Literal["received", "issued"] = Query("received"),
):
    """Get aging of pending cheques."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT * FROM get_cheque_aging($1, $2)
            """, ctx["tenant_id"], cheque_type)

            return {
                "success": True,
                "data": [
                    {
                        "aging_bucket": row["aging_bucket"],
                        "count": row["count"],
                        "total_amount": row["total_amount"]
                    }
                    for row in rows
                ],
                "cheque_type": cheque_type
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting cheque aging: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get cheque aging")


# =============================================================================
# UPDATE & DELETE (pending only)
# =============================================================================

@router.patch("/{cheque_id}", response_model=ChequeResponse)
async def update_cheque(request: Request, cheque_id: UUID, body: UpdateChequeRequest):
    """Update a pending cheque."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            existing = await conn.fetchrow("""
                SELECT id FROM cheques
                WHERE id = $1 AND tenant_id = $2 AND status = 'pending'
            """, cheque_id, ctx["tenant_id"])

            if not existing:
                raise HTTPException(status_code=404, detail="Pending cheque not found")

            updates = []
            params = []
            param_idx = 1

            if body.cheque_date is not None:
                updates.append(f"cheque_date = ${param_idx}")
                params.append(body.cheque_date)
                param_idx += 1

            if body.bank_name is not None:
                updates.append(f"bank_name = ${param_idx}")
                params.append(body.bank_name)
                param_idx += 1

            if body.bank_branch is not None:
                updates.append(f"bank_branch = ${param_idx}")
                params.append(body.bank_branch)
                param_idx += 1

            if body.party_name is not None:
                updates.append(f"party_name = ${param_idx}")
                params.append(body.party_name)
                param_idx += 1

            if body.notes is not None:
                updates.append(f"notes = ${param_idx}")
                params.append(body.notes)
                param_idx += 1

            if not updates:
                return {"success": True, "message": "No changes", "data": None}

            updates.append("updated_at = NOW()")
            params.extend([cheque_id, ctx["tenant_id"]])

            await conn.execute(f"""
                UPDATE cheques SET {", ".join(updates)}
                WHERE id = ${param_idx} AND tenant_id = ${param_idx + 1}
            """, *params)

            return {
                "success": True,
                "message": "Cheque updated",
                "data": {"id": str(cheque_id)}
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating cheque: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update cheque")


@router.delete("/{cheque_id}", response_model=ChequeResponse)
async def delete_cheque(request: Request, cheque_id: UUID):
    """Delete a pending cheque (hard delete, reverses journal)."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                cheque = await conn.fetchrow("""
                    SELECT * FROM cheques
                    WHERE id = $1 AND tenant_id = $2 AND status = 'pending'
                """, cheque_id, ctx["tenant_id"])

                if not cheque:
                    raise HTTPException(status_code=404, detail="Pending cheque not found")

                # Delete associated journal entries
                if cheque["receipt_journal_id"]:
                    await conn.execute("DELETE FROM journal_lines WHERE journal_id = $1", cheque["receipt_journal_id"])
                    await conn.execute("DELETE FROM journal_entries WHERE id = $1", cheque["receipt_journal_id"])

                # Delete cheque
                await conn.execute("DELETE FROM cheques WHERE id = $1", cheque_id)

                return {
                    "success": True,
                    "message": "Cheque deleted",
                    "data": {"id": str(cheque_id)}
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting cheque: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to delete cheque")
