"""
Bank Transfers Router - Transfer Antar Bank

Endpoints for managing inter-bank transfers with optional transfer fees.

Flow:
1. Create draft transfer
2. Post to accounting (creates journal entry + 2 bank transactions)
3. Void if needed (creates reversal)

Endpoints:
- GET    /bank-transfers              - List transfers
- GET    /bank-transfers/summary      - Summary statistics
- GET    /bank-transfers/{id}         - Detail
- POST   /bank-transfers              - Create draft
- PATCH  /bank-transfers/{id}         - Update draft
- DELETE /bank-transfers/{id}         - Delete draft
- POST   /bank-transfers/{id}/post    - Post transfer
- POST   /bank-transfers/{id}/void    - Void with reversal
"""

from fastapi import APIRouter, HTTPException, Request, Query
from typing import Optional, Literal
from uuid import UUID
import logging
import asyncpg
from datetime import date
import uuid as uuid_module

from ..schemas.bank_transfers import (
    CreateBankTransferRequest,
    UpdateBankTransferRequest,
    VoidBankTransferRequest,
    BankTransferResponse,
    BankTransferDetailResponse,
    BankTransferListResponse,
    BankTransferSummaryResponse,
)
from ..config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

# Connection pool
_pool: Optional[asyncpg.Pool] = None

# Account codes
BANK_FEE_ACCOUNT = "5-20950"  # Biaya Transfer Bank


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


# =============================================================================
# LIST BANK TRANSFERS
# =============================================================================

@router.get("", response_model=BankTransferListResponse)
async def list_bank_transfers(
    request: Request,
    status: Optional[Literal["all", "draft", "posted", "void"]] = Query("all"),
    from_bank_id: Optional[str] = Query(None),
    to_bank_id: Optional[str] = Query(None),
    search: Optional[str] = Query(None, description="Search by transfer number"),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    sort_by: Literal["transfer_date", "transfer_number", "amount", "created_at"] = Query("created_at"),
    sort_order: Literal["asc", "desc"] = Query("desc"),
):
    """List bank transfers with filters and pagination."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Build query conditions
            conditions = ["bt.tenant_id = $1"]
            params = [ctx["tenant_id"]]
            param_idx = 2

            if status and status != "all":
                conditions.append(f"bt.status = ${param_idx}")
                params.append(status)
                param_idx += 1

            if from_bank_id:
                conditions.append(f"bt.from_bank_id = ${param_idx}")
                params.append(UUID(from_bank_id))
                param_idx += 1

            if to_bank_id:
                conditions.append(f"bt.to_bank_id = ${param_idx}")
                params.append(UUID(to_bank_id))
                param_idx += 1

            if search:
                conditions.append(f"bt.transfer_number ILIKE ${param_idx}")
                params.append(f"%{search}%")
                param_idx += 1

            if date_from:
                conditions.append(f"bt.transfer_date >= ${param_idx}")
                params.append(date_from)
                param_idx += 1

            if date_to:
                conditions.append(f"bt.transfer_date <= ${param_idx}")
                params.append(date_to)
                param_idx += 1

            where_clause = " AND ".join(conditions)

            # Sort
            valid_sorts = {
                "transfer_date": "bt.transfer_date",
                "transfer_number": "bt.transfer_number",
                "amount": "bt.amount",
                "created_at": "bt.created_at"
            }
            sort_field = valid_sorts.get(sort_by, "bt.created_at")
            sort_dir = "DESC" if sort_order == "desc" else "ASC"

            # Count total
            count_query = f"SELECT COUNT(*) FROM bank_transfers bt WHERE {where_clause}"
            total = await conn.fetchval(count_query, *params)

            # Get items with bank info
            query = f"""
                SELECT bt.id, bt.transfer_number, bt.from_bank_id, bt.to_bank_id,
                       bt.amount, bt.fee_amount, bt.total_amount,
                       bt.transfer_date, bt.status, bt.ref_no, bt.created_at,
                       fb.account_name as from_bank_name,
                       tb.account_name as to_bank_name
                FROM bank_transfers bt
                LEFT JOIN bank_accounts fb ON bt.from_bank_id = fb.id
                LEFT JOIN bank_accounts tb ON bt.to_bank_id = tb.id
                WHERE {where_clause}
                ORDER BY {sort_field} {sort_dir}
                LIMIT ${param_idx} OFFSET ${param_idx + 1}
            """
            params.extend([limit, skip])

            rows = await conn.fetch(query, *params)

            items = [
                {
                    "id": str(row["id"]),
                    "transfer_number": row["transfer_number"],
                    "from_bank_id": str(row["from_bank_id"]),
                    "from_bank_name": row["from_bank_name"],
                    "to_bank_id": str(row["to_bank_id"]),
                    "to_bank_name": row["to_bank_name"],
                    "amount": row["amount"],
                    "fee_amount": row["fee_amount"] or 0,
                    "total_amount": row["total_amount"],
                    "transfer_date": row["transfer_date"].isoformat(),
                    "status": row["status"],
                    "ref_no": row["ref_no"],
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
        logger.error(f"Error listing bank transfers: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list bank transfers")


# =============================================================================
# SUMMARY
# =============================================================================

@router.get("/summary", response_model=BankTransferSummaryResponse)
async def get_bank_transfers_summary(request: Request):
    """Get summary statistics for bank transfers."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            query = """
                SELECT
                    COUNT(*) as total,
                    COUNT(*) FILTER (WHERE status = 'draft') as draft_count,
                    COUNT(*) FILTER (WHERE status = 'posted') as posted_count,
                    COUNT(*) FILTER (WHERE status = 'void') as void_count,
                    COALESCE(SUM(amount) FILTER (WHERE status = 'posted'), 0) as total_transferred,
                    COALESCE(SUM(fee_amount) FILTER (WHERE status = 'posted'), 0) as total_fees
                FROM bank_transfers
                WHERE tenant_id = $1
            """
            row = await conn.fetchrow(query, ctx["tenant_id"])

            return {
                "success": True,
                "data": {
                    "total": row["total"] or 0,
                    "draft_count": row["draft_count"] or 0,
                    "posted_count": row["posted_count"] or 0,
                    "void_count": row["void_count"] or 0,
                    "total_transferred": int(row["total_transferred"] or 0),
                    "total_fees": int(row["total_fees"] or 0),
                }
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting bank transfers summary: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get summary")


# =============================================================================
# GET BANK TRANSFER DETAIL
# =============================================================================

@router.get("/{transfer_id}", response_model=BankTransferDetailResponse)
async def get_bank_transfer(request: Request, transfer_id: UUID):
    """Get detailed information for a bank transfer."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Get transfer with bank info
            query = """
                SELECT bt.*,
                       fb.account_name as from_account_name, fb.account_number as from_account_number,
                       fb.bank_name as from_bank_name, fb.current_balance as from_balance,
                       tb.account_name as to_account_name, tb.account_number as to_account_number,
                       tb.bank_name as to_bank_name, tb.current_balance as to_balance,
                       fa.account_code as fee_account_code, fa.name as fee_account_name,
                       je.journal_number
                FROM bank_transfers bt
                LEFT JOIN bank_accounts fb ON bt.from_bank_id = fb.id
                LEFT JOIN bank_accounts tb ON bt.to_bank_id = tb.id
                LEFT JOIN chart_of_accounts fa ON bt.fee_account_id = fa.id
                LEFT JOIN journal_entries je ON bt.journal_id = je.id
                WHERE bt.id = $1 AND bt.tenant_id = $2
            """
            row = await conn.fetchrow(query, transfer_id, ctx["tenant_id"])

            if not row:
                raise HTTPException(status_code=404, detail="Bank transfer not found")

            return {
                "success": True,
                "data": {
                    "id": str(row["id"]),
                    "transfer_number": row["transfer_number"],
                    "from_bank": {
                        "id": str(row["from_bank_id"]),
                        "account_name": row["from_account_name"],
                        "account_number": row["from_account_number"],
                        "bank_name": row["from_bank_name"],
                        "current_balance": row["from_balance"] or 0,
                    },
                    "to_bank": {
                        "id": str(row["to_bank_id"]),
                        "account_name": row["to_account_name"],
                        "account_number": row["to_account_number"],
                        "bank_name": row["to_bank_name"],
                        "current_balance": row["to_balance"] or 0,
                    },
                    "amount": row["amount"],
                    "fee_amount": row["fee_amount"] or 0,
                    "total_amount": row["total_amount"],
                    "fee_account_id": str(row["fee_account_id"]) if row["fee_account_id"] else None,
                    "fee_account_code": row["fee_account_code"],
                    "fee_account_name": row["fee_account_name"],
                    "status": row["status"],
                    "transfer_date": row["transfer_date"].isoformat(),
                    "ref_no": row["ref_no"],
                    "notes": row["notes"],
                    "journal_id": str(row["journal_id"]) if row["journal_id"] else None,
                    "journal_number": row["journal_number"],
                    "from_transaction_id": str(row["from_transaction_id"]) if row["from_transaction_id"] else None,
                    "to_transaction_id": str(row["to_transaction_id"]) if row["to_transaction_id"] else None,
                    "posted_at": row["posted_at"].isoformat() if row["posted_at"] else None,
                    "posted_by": str(row["posted_by"]) if row["posted_by"] else None,
                    "voided_at": row["voided_at"].isoformat() if row["voided_at"] else None,
                    "voided_reason": row["voided_reason"],
                    "created_at": row["created_at"].isoformat(),
                    "updated_at": row["updated_at"].isoformat(),
                    "created_by": str(row["created_by"]) if row["created_by"] else None,
                }
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting bank transfer {transfer_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get bank transfer")


# =============================================================================
# CREATE BANK TRANSFER
# =============================================================================

@router.post("", response_model=BankTransferResponse, status_code=201)
async def create_bank_transfer(request: Request, body: CreateBankTransferRequest):
    """
    Create a new bank transfer (draft).

    If auto_post=True, immediately posts to accounting.
    """
    try:
        ctx = get_user_context(request)
        if not ctx["user_id"]:
            raise HTTPException(status_code=401, detail="User ID required")

        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                # Validate from bank
                from_bank = await conn.fetchrow("""
                    SELECT id, account_name, current_balance, coa_id, is_active
                    FROM bank_accounts
                    WHERE id = $1 AND tenant_id = $2
                """, UUID(body.from_bank_id), ctx["tenant_id"])

                if not from_bank:
                    raise HTTPException(status_code=400, detail="Source bank account not found")

                if not from_bank["is_active"]:
                    raise HTTPException(status_code=400, detail="Source bank account is inactive")

                # Validate to bank
                to_bank = await conn.fetchrow("""
                    SELECT id, account_name, current_balance, coa_id, is_active
                    FROM bank_accounts
                    WHERE id = $1 AND tenant_id = $2
                """, UUID(body.to_bank_id), ctx["tenant_id"])

                if not to_bank:
                    raise HTTPException(status_code=400, detail="Destination bank account not found")

                if not to_bank["is_active"]:
                    raise HTTPException(status_code=400, detail="Destination bank account is inactive")

                # Calculate total
                total_amount = body.amount + body.fee_amount

                # Generate transfer number
                transfer_number = await conn.fetchval(
                    "SELECT generate_bank_transfer_number($1, 'TRF')",
                    ctx["tenant_id"]
                )

                # Create transfer
                transfer_id = uuid_module.uuid4()

                await conn.execute("""
                    INSERT INTO bank_transfers (
                        id, tenant_id, transfer_number, from_bank_id, to_bank_id,
                        amount, fee_amount, total_amount, transfer_date,
                        ref_no, notes, status, created_by
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, 'draft', $12)
                """,
                    transfer_id,
                    ctx["tenant_id"],
                    transfer_number,
                    UUID(body.from_bank_id),
                    UUID(body.to_bank_id),
                    body.amount,
                    body.fee_amount,
                    total_amount,
                    body.transfer_date,
                    body.ref_no,
                    body.notes,
                    ctx["user_id"]
                )

                logger.info(f"Bank transfer created: {transfer_id}, number={transfer_number}")

                result = {
                    "success": True,
                    "message": "Bank transfer created successfully",
                    "data": {
                        "id": str(transfer_id),
                        "transfer_number": transfer_number,
                        "status": "draft"
                    }
                }

                # Auto-post if requested
                if body.auto_post:
                    post_result = await _post_transfer(conn, ctx, transfer_id)
                    result["data"].update(post_result)
                    result["data"]["status"] = "posted"
                    result["message"] = "Bank transfer created and posted"

                return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating bank transfer: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create bank transfer")


# =============================================================================
# UPDATE BANK TRANSFER
# =============================================================================

@router.patch("/{transfer_id}", response_model=BankTransferResponse)
async def update_bank_transfer(request: Request, transfer_id: UUID, body: UpdateBankTransferRequest):
    """Update a draft bank transfer."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                # Get existing transfer
                bt = await conn.fetchrow("""
                    SELECT * FROM bank_transfers
                    WHERE id = $1 AND tenant_id = $2
                """, transfer_id, ctx["tenant_id"])

                if not bt:
                    raise HTTPException(status_code=404, detail="Bank transfer not found")

                if bt["status"] != "draft":
                    raise HTTPException(
                        status_code=400,
                        detail="Only draft transfers can be updated"
                    )

                # Build update
                updates = []
                params = []
                param_idx = 1

                if body.from_bank_id is not None:
                    # Validate bank
                    exists = await conn.fetchval("""
                        SELECT id FROM bank_accounts
                        WHERE id = $1 AND tenant_id = $2 AND is_active = true
                    """, UUID(body.from_bank_id), ctx["tenant_id"])
                    if not exists:
                        raise HTTPException(status_code=400, detail="Source bank account not found or inactive")
                    updates.append(f"from_bank_id = ${param_idx}")
                    params.append(UUID(body.from_bank_id))
                    param_idx += 1

                if body.to_bank_id is not None:
                    exists = await conn.fetchval("""
                        SELECT id FROM bank_accounts
                        WHERE id = $1 AND tenant_id = $2 AND is_active = true
                    """, UUID(body.to_bank_id), ctx["tenant_id"])
                    if not exists:
                        raise HTTPException(status_code=400, detail="Destination bank account not found or inactive")
                    updates.append(f"to_bank_id = ${param_idx}")
                    params.append(UUID(body.to_bank_id))
                    param_idx += 1

                if body.amount is not None:
                    updates.append(f"amount = ${param_idx}")
                    params.append(body.amount)
                    param_idx += 1

                if body.fee_amount is not None:
                    updates.append(f"fee_amount = ${param_idx}")
                    params.append(body.fee_amount)
                    param_idx += 1

                if body.transfer_date is not None:
                    updates.append(f"transfer_date = ${param_idx}")
                    params.append(body.transfer_date)
                    param_idx += 1

                if body.ref_no is not None:
                    updates.append(f"ref_no = ${param_idx}")
                    params.append(body.ref_no)
                    param_idx += 1

                if body.notes is not None:
                    updates.append(f"notes = ${param_idx}")
                    params.append(body.notes)
                    param_idx += 1

                if not updates:
                    return {
                        "success": True,
                        "message": "No changes provided",
                        "data": {"id": str(transfer_id)}
                    }

                # Recalculate total_amount
                new_amount = body.amount if body.amount is not None else bt["amount"]
                new_fee = body.fee_amount if body.fee_amount is not None else (bt["fee_amount"] or 0)
                updates.append(f"total_amount = ${param_idx}")
                params.append(new_amount + new_fee)
                param_idx += 1

                updates.append("updated_at = NOW()")
                params.extend([transfer_id, ctx["tenant_id"]])

                query = f"""
                    UPDATE bank_transfers
                    SET {", ".join(updates)}
                    WHERE id = ${param_idx} AND tenant_id = ${param_idx + 1}
                """
                await conn.execute(query, *params)

                logger.info(f"Bank transfer updated: {transfer_id}")

                return {
                    "success": True,
                    "message": "Bank transfer updated successfully",
                    "data": {"id": str(transfer_id)}
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating bank transfer {transfer_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update bank transfer")


# =============================================================================
# DELETE BANK TRANSFER
# =============================================================================

@router.delete("/{transfer_id}", response_model=BankTransferResponse)
async def delete_bank_transfer(request: Request, transfer_id: UUID):
    """Delete a draft bank transfer."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            bt = await conn.fetchrow("""
                SELECT id, transfer_number, status FROM bank_transfers
                WHERE id = $1 AND tenant_id = $2
            """, transfer_id, ctx["tenant_id"])

            if not bt:
                raise HTTPException(status_code=404, detail="Bank transfer not found")

            if bt["status"] != "draft":
                raise HTTPException(
                    status_code=400,
                    detail="Only draft transfers can be deleted. Use void for posted."
                )

            await conn.execute(
                "DELETE FROM bank_transfers WHERE id = $1",
                transfer_id
            )

            logger.info(f"Bank transfer deleted: {transfer_id}")

            return {
                "success": True,
                "message": "Bank transfer deleted",
                "data": {
                    "id": str(transfer_id),
                    "transfer_number": bt["transfer_number"]
                }
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting bank transfer {transfer_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to delete bank transfer")


# =============================================================================
# POST BANK TRANSFER
# =============================================================================

async def _post_transfer(conn, ctx: dict, transfer_id: UUID) -> dict:
    """Internal function to post a transfer. Returns journal info."""
    # Get transfer with bank info
    bt = await conn.fetchrow("""
        SELECT bt.*,
               fb.account_name as from_name, fb.coa_id as from_coa_id, fb.current_balance as from_balance,
               tb.account_name as to_name, tb.coa_id as to_coa_id
        FROM bank_transfers bt
        LEFT JOIN bank_accounts fb ON bt.from_bank_id = fb.id
        LEFT JOIN bank_accounts tb ON bt.to_bank_id = tb.id
        WHERE bt.id = $1 AND bt.tenant_id = $2
    """, transfer_id, ctx["tenant_id"])

    if not bt:
        raise HTTPException(status_code=404, detail="Bank transfer not found")

    if bt["status"] != "draft":
        raise HTTPException(
            status_code=400,
            detail=f"Cannot post transfer with status '{bt['status']}'"
        )

    # Check sufficient balance
    if bt["from_balance"] < bt["total_amount"]:
        raise HTTPException(
            status_code=400,
            detail=f"Insufficient balance. Available: {bt['from_balance']}, Required: {bt['total_amount']}"
        )

    # Get fee account ID if fee > 0
    fee_account_id = None
    if bt["fee_amount"] and bt["fee_amount"] > 0:
        fee_account_id = await conn.fetchval("""
            SELECT id FROM chart_of_accounts
            WHERE tenant_id = $1 AND account_code = $2
        """, ctx["tenant_id"], BANK_FEE_ACCOUNT)

    # Create journal entry
    journal_id = uuid_module.uuid4()
    trace_id = uuid_module.uuid4()
    journal_number = f"TRF-{bt['transfer_number']}"

    await conn.execute("""
        INSERT INTO journal_entries (
            id, tenant_id, journal_number, journal_date,
            description, source_type, source_id, trace_id,
            status, total_debit, total_credit, created_by
        ) VALUES ($1, $2, $3, $4, $5, 'BANK_TRANSFER', $6, $7, 'POSTED', $8, $8, $9)
    """,
        journal_id,
        ctx["tenant_id"],
        journal_number,
        bt["transfer_date"],
        f"Transfer {bt['transfer_number']} - {bt['from_name']} ke {bt['to_name']}",
        transfer_id,
        str(trace_id),
        float(bt["total_amount"]),
        ctx["user_id"]
    )

    line_number = 1

    # Dr. Destination Bank
    await conn.execute("""
        INSERT INTO journal_lines (
            id, journal_id, line_number, account_id, debit, credit, memo
        ) VALUES ($1, $2, $3, $4, $5, 0, $6)
    """,
        uuid_module.uuid4(),
        journal_id,
        line_number,
        bt["to_coa_id"],
        float(bt["amount"]),
        f"Transfer masuk dari {bt['from_name']}"
    )
    line_number += 1

    # Dr. Transfer Fee (if any)
    if bt["fee_amount"] and bt["fee_amount"] > 0 and fee_account_id:
        await conn.execute("""
            INSERT INTO journal_lines (
                id, journal_id, line_number, account_id, debit, credit, memo
            ) VALUES ($1, $2, $3, $4, $5, 0, $6)
        """,
            uuid_module.uuid4(),
            journal_id,
            line_number,
            fee_account_id,
            float(bt["fee_amount"]),
            f"Biaya transfer - {bt['transfer_number']}"
        )
        line_number += 1

    # Cr. Source Bank
    await conn.execute("""
        INSERT INTO journal_lines (
            id, journal_id, line_number, account_id, debit, credit, memo
        ) VALUES ($1, $2, $3, $4, 0, $5, $6)
    """,
        uuid_module.uuid4(),
        journal_id,
        line_number,
        bt["from_coa_id"],
        float(bt["total_amount"]),
        f"Transfer keluar ke {bt['to_name']}"
    )

    # Create bank transactions
    # Source bank (outgoing)
    from_new_balance = bt["from_balance"] - bt["total_amount"]
    from_tx_id = uuid_module.uuid4()

    await conn.execute("""
        INSERT INTO bank_transactions (
            id, tenant_id, bank_account_id, transaction_date, transaction_type,
            amount, running_balance, reference_type, reference_id, reference_number,
            description, payee_payer, journal_id, created_by
        ) VALUES ($1, $2, $3, $4, 'transfer_out', $5, $6, 'transfer', $7, $8, $9, $10, $11, $12)
    """,
        from_tx_id,
        ctx["tenant_id"],
        bt["from_bank_id"],
        bt["transfer_date"],
        -bt["total_amount"],  # Negative for outgoing
        from_new_balance,
        transfer_id,
        bt["transfer_number"],
        f"Transfer ke {bt['to_name']}",
        bt["to_name"],
        journal_id,
        ctx["user_id"]
    )

    # Destination bank (incoming)
    to_balance = await conn.fetchval(
        "SELECT current_balance FROM bank_accounts WHERE id = $1",
        bt["to_bank_id"]
    )
    to_new_balance = (to_balance or 0) + bt["amount"]
    to_tx_id = uuid_module.uuid4()

    await conn.execute("""
        INSERT INTO bank_transactions (
            id, tenant_id, bank_account_id, transaction_date, transaction_type,
            amount, running_balance, reference_type, reference_id, reference_number,
            description, payee_payer, journal_id, created_by
        ) VALUES ($1, $2, $3, $4, 'transfer_in', $5, $6, 'transfer', $7, $8, $9, $10, $11, $12)
    """,
        to_tx_id,
        ctx["tenant_id"],
        bt["to_bank_id"],
        bt["transfer_date"],
        bt["amount"],  # Positive for incoming (no fee)
        to_new_balance,
        transfer_id,
        bt["transfer_number"],
        f"Transfer dari {bt['from_name']}",
        bt["from_name"],
        journal_id,
        ctx["user_id"]
    )

    # Update transfer status
    await conn.execute("""
        UPDATE bank_transfers
        SET status = 'posted', journal_id = $2, fee_account_id = $3,
            from_transaction_id = $4, to_transaction_id = $5,
            posted_at = NOW(), posted_by = $6, updated_at = NOW()
        WHERE id = $1
    """, transfer_id, journal_id, fee_account_id, from_tx_id, to_tx_id, ctx["user_id"])

    logger.info(f"Bank transfer posted: {transfer_id}, journal={journal_id}")

    return {
        "journal_id": str(journal_id),
        "journal_number": journal_number,
        "from_transaction_id": str(from_tx_id),
        "to_transaction_id": str(to_tx_id)
    }


@router.post("/{transfer_id}/post", response_model=BankTransferResponse)
async def post_bank_transfer(request: Request, transfer_id: UUID):
    """
    Post bank transfer to accounting.

    Creates journal entry:
    - Dr. Bank Tujuan (destination coa)       amount
    - Dr. Biaya Transfer (if fee > 0)         fee_amount
    - Cr. Bank Asal (source coa)              total_amount

    Also creates bank_transactions for both accounts.
    """
    try:
        ctx = get_user_context(request)
        if not ctx["user_id"]:
            raise HTTPException(status_code=401, detail="User ID required")

        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                result = await _post_transfer(conn, ctx, transfer_id)

                return {
                    "success": True,
                    "message": "Bank transfer posted to accounting",
                    "data": {
                        "id": str(transfer_id),
                        "status": "posted",
                        **result
                    }
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error posting bank transfer {transfer_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to post bank transfer")


# =============================================================================
# VOID BANK TRANSFER
# =============================================================================

@router.post("/{transfer_id}/void", response_model=BankTransferResponse)
async def void_bank_transfer(request: Request, transfer_id: UUID, body: VoidBankTransferRequest):
    """
    Void a posted bank transfer.

    Creates reversal journal entry and reversal bank transactions.
    """
    try:
        ctx = get_user_context(request)
        if not ctx["user_id"]:
            raise HTTPException(status_code=401, detail="User ID required")

        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                # Get transfer
                bt = await conn.fetchrow("""
                    SELECT bt.*,
                           fb.account_name as from_name, fb.coa_id as from_coa_id, fb.current_balance as from_balance,
                           tb.account_name as to_name, tb.coa_id as to_coa_id, tb.current_balance as to_balance
                    FROM bank_transfers bt
                    LEFT JOIN bank_accounts fb ON bt.from_bank_id = fb.id
                    LEFT JOIN bank_accounts tb ON bt.to_bank_id = tb.id
                    WHERE bt.id = $1 AND bt.tenant_id = $2
                """, transfer_id, ctx["tenant_id"])

                if not bt:
                    raise HTTPException(status_code=404, detail="Bank transfer not found")

                if bt["status"] == "void":
                    raise HTTPException(status_code=400, detail="Transfer already voided")

                if bt["status"] == "draft":
                    # Just delete draft
                    await conn.execute(
                        "DELETE FROM bank_transfers WHERE id = $1",
                        transfer_id
                    )
                    return {
                        "success": True,
                        "message": "Draft transfer deleted",
                        "data": {"id": str(transfer_id)}
                    }

                # Create reversal journal
                reversal_journal_id = uuid_module.uuid4()
                reversal_number = f"RV-{bt['transfer_number']}"

                # Get original journal lines
                original_lines = await conn.fetch("""
                    SELECT * FROM journal_lines WHERE journal_id = $1
                """, bt["journal_id"])

                await conn.execute("""
                    INSERT INTO journal_entries (
                        id, tenant_id, journal_number, journal_date,
                        description, source_type, source_id, reversal_of_id,
                        status, total_debit, total_credit, created_by
                    ) VALUES ($1, $2, $3, CURRENT_DATE, $4, 'BANK_TRANSFER', $5, $6, 'POSTED', $7, $7, $8)
                """,
                    reversal_journal_id,
                    ctx["tenant_id"],
                    reversal_number,
                    f"Void Transfer {bt['transfer_number']} - {body.reason}",
                    transfer_id,
                    bt["journal_id"],
                    float(bt["total_amount"]),
                    ctx["user_id"]
                )

                # Create reversed lines (swap debit/credit)
                for idx, line in enumerate(original_lines, 1):
                    await conn.execute("""
                        INSERT INTO journal_lines (
                            id, journal_id, line_number, account_id, debit, credit, memo
                        ) VALUES ($1, $2, $3, $4, $5, $6, $7)
                    """,
                        uuid_module.uuid4(),
                        reversal_journal_id,
                        idx,
                        line["account_id"],
                        line["credit"],  # Swap
                        line["debit"],   # Swap
                        f"Reversal - {line['memo'] or ''}"
                    )

                # Mark original journal as reversed
                await conn.execute("""
                    UPDATE journal_entries
                    SET reversed_by_id = $2, status = 'VOID'
                    WHERE id = $1
                """, bt["journal_id"], reversal_journal_id)

                # Create reversal bank transactions
                # Reverse source bank (get money back)
                from_new_balance = bt["from_balance"] + bt["total_amount"]
                await conn.execute("""
                    INSERT INTO bank_transactions (
                        id, tenant_id, bank_account_id, transaction_date, transaction_type,
                        amount, running_balance, reference_type, reference_id, reference_number,
                        description, journal_id, created_by
                    ) VALUES ($1, $2, $3, CURRENT_DATE, 'transfer_in', $4, $5, 'transfer_void', $6, $7, $8, $9, $10)
                """,
                    uuid_module.uuid4(),
                    ctx["tenant_id"],
                    bt["from_bank_id"],
                    bt["total_amount"],  # Positive (money back)
                    from_new_balance,
                    transfer_id,
                    f"VOID-{bt['transfer_number']}",
                    f"Void transfer - {body.reason}",
                    reversal_journal_id,
                    ctx["user_id"]
                )

                # Reverse destination bank (money out)
                to_new_balance = bt["to_balance"] - bt["amount"]
                await conn.execute("""
                    INSERT INTO bank_transactions (
                        id, tenant_id, bank_account_id, transaction_date, transaction_type,
                        amount, running_balance, reference_type, reference_id, reference_number,
                        description, journal_id, created_by
                    ) VALUES ($1, $2, $3, CURRENT_DATE, 'transfer_out', $4, $5, 'transfer_void', $6, $7, $8, $9, $10)
                """,
                    uuid_module.uuid4(),
                    ctx["tenant_id"],
                    bt["to_bank_id"],
                    -bt["amount"],  # Negative (money out)
                    to_new_balance,
                    transfer_id,
                    f"VOID-{bt['transfer_number']}",
                    f"Void transfer - {body.reason}",
                    reversal_journal_id,
                    ctx["user_id"]
                )

                # Update transfer status
                await conn.execute("""
                    UPDATE bank_transfers
                    SET status = 'void', voided_at = NOW(), voided_by = $2,
                        voided_reason = $3, updated_at = NOW()
                    WHERE id = $1
                """, transfer_id, ctx["user_id"], body.reason)

                logger.info(f"Bank transfer voided: {transfer_id}")

                return {
                    "success": True,
                    "message": "Bank transfer voided successfully",
                    "data": {
                        "id": str(transfer_id),
                        "status": "void",
                        "reversal_journal_id": str(reversal_journal_id)
                    }
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error voiding bank transfer {transfer_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to void bank transfer")
