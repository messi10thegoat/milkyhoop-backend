"""
Proformas (Faktur Proforma / Tagihan Uang Muka) Router — T200.

Proforma = dokumen PENAGIH UANG MUKA yang merujuk sebuah Sales Order.

MUTLAK NON-POSTING. Berkas ini TIDAK PERNAH menyentuh journal_entries /
journal_lines. Uang tetap masuk lewat customer_deposits (yang menjurnal).

Dua aturan yang menopang kebenarannya:
  1. WHERE tenant_id = $1 di SETIAP query. Gateway konek dengan peran BYPASSRLS,
     jadi RLS TIDAK melindungi jalur ini — klausa inilah penjaga sebenarnya.
  2. TERBAYAR = TURUNAN. Tidak ada kolom terbayar yang disimpan. Angka dihitung
     dari customer_deposits.proforma_id. Atribusi TIDAK PERNAH memakai tanggal
     (tanggal pecah pada cicilan / dua proforma di hari yang sama).
"""

import base64
import logging
import uuid as uuid_module
from datetime import date, datetime
from decimal import Decimal
from io import BytesIO
from pathlib import Path as _Path
from typing import Optional

import asyncpg
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter()
so_router = APIRouter()  # dipasang di /api/sales-orders

# Status SO yang boleh ditagih lewat proforma: 'confirmed' ke atas.
SO_BILLABLE_STATUSES = (
    "confirmed",
    "partial_shipped",
    "shipped",
    "partial_invoiced",
    "invoiced",
    "completed",
)

VALID_PURPOSES = ("DP", "TERMIN", "PELUNASAN")


async def get_pool() -> asyncpg.Pool:
    """Get singleton connection pool (Law 32)."""
    from ..services.db_pool import get_db_pool

    return await get_db_pool()


def get_user_context(request: Request) -> dict:
    """Extract user context from request."""
    if not hasattr(request.state, "user") or not request.state.user:
        raise HTTPException(status_code=401, detail="Authentication required")

    user = request.state.user
    tenant_id = user.get("tenant_id")
    user_id = user.get("user_id") or user.get("id")

    if not tenant_id:
        raise HTTPException(status_code=401, detail="Invalid user context")

    return {
        "tenant_id": tenant_id,
        "user_id": uuid_module.UUID(user_id) if user_id else None,
    }


def _uuid_or_404(value: str, what: str = "Proforma") -> uuid_module.UUID:
    try:
        return uuid_module.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=404, detail=f"{what} not found")


def _f(value) -> Optional[float]:
    """Decimal -> float. Aturan repo: RESPONS pakai float, JANGAN Decimal
    (pydantic v2 menyerialkan Decimal sebagai STRING -> merusak matematika FE)."""
    if value is None:
        return None
    return float(value)


# ============================================================================
# SCHEMAS
# ============================================================================


class CreateProformaRequest(BaseModel):
    sales_order_id: str
    purpose: str = "DP"
    percent_of_order: Optional[Decimal] = None
    amount: Optional[Decimal] = None
    proforma_date: Optional[date] = None
    due_date: Optional[date] = None
    terms: Optional[str] = None
    notes: Optional[str] = None
    currency: Optional[str] = "IDR"
    payment_bank_name: Optional[str] = None
    payment_account_number: Optional[str] = None
    payment_account_holder: Optional[str] = None


class UpdateProformaRequest(BaseModel):
    purpose: Optional[str] = None
    percent_of_order: Optional[Decimal] = None
    amount: Optional[Decimal] = None
    proforma_date: Optional[date] = None
    due_date: Optional[date] = None
    terms: Optional[str] = None
    notes: Optional[str] = None
    payment_bank_name: Optional[str] = None
    payment_account_number: Optional[str] = None
    payment_account_holder: Optional[str] = None


class CancelProformaRequest(BaseModel):
    reason: str = Field(..., min_length=1)


# ============================================================================
# HELPERS (pagar)
# ============================================================================


async def compute_paid_amount(conn, tenant_id: str, proforma_id) -> float:
    """TERBAYAR = TURUNAN. Dihitung dari customer_deposits yang MENUNJUK proforma
    ini lewat proforma_id. BUKAN dari kolom tersimpan, BUKAN dari tanggal."""
    row = await conn.fetchrow(
        """
        SELECT COALESCE(SUM(amount), 0) AS paid, COUNT(*) AS n
        FROM customer_deposits
        WHERE proforma_id = $1 AND tenant_id = $2 AND status <> 'void'
        """,
        proforma_id,
        tenant_id,
    )
    return float(row["paid"] or 0)


async def issued_total_for_order(
    conn, tenant_id: str, sales_order_id, exclude_id=None
) -> float:
    """Jumlah amount proforma berstatus 'issued' SAJA untuk satu SO.
    'draft', 'cancelled', dan 'expired' DIKECUALIKAN — kalau 'expired' ikut
    dihitung, SO terkunci dari penagihan ulang."""
    row = await conn.fetchrow(
        """
        SELECT COALESCE(SUM(amount), 0) AS total
        FROM proformas
        WHERE tenant_id = $1
          AND sales_order_id = $2
          AND status = 'issued'
          AND ($3::uuid IS NULL OR id <> $3::uuid)
        """,
        tenant_id,
        sales_order_id,
        exclude_id,
    )
    return float(row["total"] or 0)


async def assert_within_order_total(
    conn, tenant_id: str, sales_order_id, order_total: float, amount: float, exclude_id=None
):
    """Pagar total: issued yang sudah ada + amount ini tidak boleh melebihi
    nilai Sales Order. Ditolak dengan pesan yang MENYEBUT sisa yang bisa ditagih."""
    already = await issued_total_for_order(
        conn, tenant_id, sales_order_id, exclude_id=exclude_id
    )
    sisa = round(order_total - already, 2)
    if round(amount, 2) > sisa + 0.005:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Nilai proforma {amount:,.2f} melebihi sisa yang bisa ditagih. "
                f"Nilai Sales Order {order_total:,.2f}, sudah ditagih (issued) "
                f"{already:,.2f}, sisa yang bisa ditagih {sisa:,.2f}."
            ),
        )


async def fetch_order_or_404(conn, tenant_id: str, sales_order_id):
    order = await conn.fetchrow(
        """
        SELECT id, order_number, customer_id, customer_name, total_amount, status,
               payment_bank_name, payment_account_number, payment_account_holder
        FROM sales_orders
        WHERE id = $1 AND tenant_id = $2
        """,
        sales_order_id,
        tenant_id,
    )
    if not order:
        raise HTTPException(status_code=404, detail="Sales Order not found")
    return order


def serialize_proforma(row, order_number=None, paid_amount=None) -> dict:
    amount = _f(row["amount"]) or 0.0
    data = {
        "id": str(row["id"]),
        "proforma_number": row["proforma_number"],
        "proforma_date": row["proforma_date"].isoformat() if row["proforma_date"] else None,
        "due_date": row["due_date"].isoformat() if row["due_date"] else None,
        "sales_order_id": str(row["sales_order_id"]),
        "sales_order_number": order_number,
        "customer_id": str(row["customer_id"]) if row["customer_id"] else None,
        "customer_name": row["customer_name"],
        "purpose": row["purpose"],
        "percent_of_order": _f(row["percent_of_order"]),
        "amount": amount,
        "currency": row["currency"],
        "terms": row["terms"],
        "notes": row["notes"],
        "payment_bank_name": row["payment_bank_name"],
        "payment_account_number": row["payment_account_number"],
        "payment_account_holder": row["payment_account_holder"],
        "status": row["status"],
        "issued_at": row["issued_at"].isoformat() if row["issued_at"] else None,
        "cancelled_at": row["cancelled_at"].isoformat() if row["cancelled_at"] else None,
        "cancelled_reason": row["cancelled_reason"],
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
    }
    if paid_amount is not None:
        data["paid_amount"] = float(paid_amount)
        data["outstanding_amount"] = round(amount - float(paid_amount), 2)
        data["is_fully_paid"] = float(paid_amount) + 0.005 >= amount
    return data


# ============================================================================
# READ
# ============================================================================


@router.get("")
async def list_proformas(
    request: Request,
    status: Optional[str] = Query("all"),
    customer_id: Optional[str] = Query(None),
    sales_order_id: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
):
    """List proformas with filters."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # tenant_id SELALU kondisi pertama dan tertulis LITERAL di tiap SQL.
            extras = []
            params = [ctx["tenant_id"]]
            idx = 2

            if status and status != "all":
                extras.append(f"p.status = ${idx}")
                params.append(status)
                idx += 1
            if customer_id:
                extras.append(f"p.customer_id = ${idx}::uuid")
                params.append(_uuid_or_404(customer_id, "Customer"))
                idx += 1
            if sales_order_id:
                extras.append(f"p.sales_order_id = ${idx}::uuid")
                params.append(_uuid_or_404(sales_order_id, "Sales Order"))
                idx += 1

            extra = ("".join(f" AND {c}" for c in extras)) if extras else ""

            total = await conn.fetchval(
                f"SELECT COUNT(*) FROM proformas p WHERE p.tenant_id = $1{extra}",
                *params,
            )

            rows = await conn.fetch(
                f"""
                SELECT p.*, so.order_number,
                       COALESCE((
                           SELECT SUM(cd.amount) FROM customer_deposits cd
                           WHERE cd.proforma_id = p.id
                             AND cd.tenant_id = p.tenant_id
                             AND cd.status <> 'void'
                       ), 0) AS paid_amount
                FROM proformas p
                LEFT JOIN sales_orders so
                       ON so.id = p.sales_order_id AND so.tenant_id = p.tenant_id
                WHERE p.tenant_id = $1{extra}
                ORDER BY p.proforma_date DESC, p.created_at DESC
                LIMIT ${idx} OFFSET ${idx + 1}
                """,
                *params,
                limit,
                skip,
            )

            items = [
                serialize_proforma(r, r["order_number"], _f(r["paid_amount"]) or 0.0)
                for r in rows
            ]

            page = (skip // limit) + 1 if limit > 0 else 1
            total_pages = (total + limit - 1) // limit if limit > 0 else 1

            return {
                "items": items,
                "total": total,
                "has_more": (skip + limit) < total,
                "page": page,
                "limit": limit,
                "total_pages": total_pages,
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing proformas: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list proformas")


@router.get("/{proforma_id}")
async def get_proforma_detail(request: Request, proforma_id: str):
    """Get one proforma. `paid_amount` adalah TURUNAN dari customer_deposits."""
    try:
        ctx = get_user_context(request)
        pid = _uuid_or_404(proforma_id)
        pool = await get_pool()

        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT p.*, so.order_number, so.total_amount AS order_total_amount
                FROM proformas p
                LEFT JOIN sales_orders so
                       ON so.id = p.sales_order_id AND so.tenant_id = p.tenant_id
                WHERE p.id = $1 AND p.tenant_id = $2
                """,
                pid,
                ctx["tenant_id"],
            )
            if not row:
                raise HTTPException(status_code=404, detail="Proforma not found")

            paid = await compute_paid_amount(conn, ctx["tenant_id"], pid)
            deposits = await conn.fetch(
                """
                SELECT id, deposit_number, deposit_date, amount, status
                FROM customer_deposits
                WHERE proforma_id = $1 AND tenant_id = $2 AND status <> 'void'
                ORDER BY deposit_date, created_at
                """,
                pid,
                ctx["tenant_id"],
            )

            data = serialize_proforma(row, row["order_number"], paid)
            data["order_total_amount"] = _f(row["order_total_amount"])
            data["deposits"] = [
                {
                    "id": str(d["id"]),
                    "deposit_number": d["deposit_number"],
                    "deposit_date": d["deposit_date"].isoformat()
                    if d["deposit_date"]
                    else None,
                    "amount": _f(d["amount"]),
                    "status": d["status"],
                }
                for d in deposits
            ]

            return {"success": True, "data": data}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting proforma {proforma_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get proforma")


@so_router.get("/{order_id}/proformas")
async def list_proformas_for_order(request: Request, order_id: str):
    """Semua proforma milik satu Sales Order."""
    try:
        ctx = get_user_context(request)
        oid = _uuid_or_404(order_id, "Sales Order")
        pool = await get_pool()

        async with pool.acquire() as conn:
            order = await fetch_order_or_404(conn, ctx["tenant_id"], oid)

            rows = await conn.fetch(
                """
                SELECT p.*,
                       COALESCE((
                           SELECT SUM(cd.amount) FROM customer_deposits cd
                           WHERE cd.proforma_id = p.id
                             AND cd.tenant_id = p.tenant_id
                             AND cd.status <> 'void'
                       ), 0) AS paid_amount
                FROM proformas p
                WHERE p.tenant_id = $1 AND p.sales_order_id = $2
                ORDER BY p.proforma_date, p.created_at
                """,
                ctx["tenant_id"],
                oid,
            )

            items = [
                serialize_proforma(
                    r, order["order_number"], _f(r["paid_amount"]) or 0.0
                )
                for r in rows
            ]
            issued_total = await issued_total_for_order(conn, ctx["tenant_id"], oid)
            order_total = _f(order["total_amount"]) or 0.0

            return {
                "items": items,
                "total": len(items),
                "has_more": False,
                "order_total_amount": order_total,
                "issued_total": issued_total,
                "billable_remaining": round(order_total - issued_total, 2),
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing proformas for order {order_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list proformas")


# ============================================================================
# WRITE (NON-POSTING — nol sentuhan journal_entries)
# ============================================================================


@router.post("", status_code=201)
async def create_proforma(request: Request, body: CreateProformaRequest):
    """Buat proforma (status draft) dari sebuah Sales Order."""
    try:
        ctx = get_user_context(request)
        oid = _uuid_or_404(body.sales_order_id, "Sales Order")

        if body.purpose not in VALID_PURPOSES:
            raise HTTPException(
                status_code=400,
                detail=f"purpose harus salah satu dari {list(VALID_PURPOSES)}",
            )

        pool = await get_pool()
        async with pool.acquire() as conn:
            order = await fetch_order_or_404(conn, ctx["tenant_id"], oid)

            if order["status"] not in SO_BILLABLE_STATUSES:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Sales Order berstatus '{order['status']}' tidak bisa ditagih "
                        f"dengan proforma. Harus 'confirmed' ke atas."
                    ),
                )

            order_total = _f(order["total_amount"]) or 0.0

            percent = _f(body.percent_of_order)
            amount = _f(body.amount)
            if percent is None and amount is None:
                raise HTTPException(
                    status_code=400,
                    detail="Wajib mengisi salah satu: percent_of_order atau amount.",
                )
            if percent is not None:
                if percent <= 0 or percent > 100:
                    raise HTTPException(
                        status_code=400, detail="percent_of_order harus di antara 0 dan 100."
                    )
                amount = round(order_total * percent / 100.0, 2)
            if amount is None or amount <= 0:
                raise HTTPException(status_code=400, detail="amount harus lebih besar dari 0.")

            await assert_within_order_total(
                conn, ctx["tenant_id"], oid, order_total, amount
            )

            number = await conn.fetchval(
                "SELECT generate_proforma_number($1)", ctx["tenant_id"]
            )

            row = await conn.fetchrow(
                """
                INSERT INTO proformas (
                    tenant_id, proforma_number, proforma_date, due_date,
                    sales_order_id, customer_id, customer_name,
                    purpose, percent_of_order, amount, currency, terms, notes,
                    payment_bank_name, payment_account_number, payment_account_holder,
                    status, created_by
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13,
                    $14, $15, $16, 'draft', $17
                )
                RETURNING *
                """,
                ctx["tenant_id"],
                number,
                body.proforma_date or date.today(),
                body.due_date,
                oid,
                order["customer_id"],
                order["customer_name"],
                body.purpose,
                Decimal(str(percent)) if percent is not None else None,
                Decimal(str(amount)),
                body.currency or "IDR",
                body.terms,
                body.notes,
                body.payment_bank_name or order["payment_bank_name"],
                body.payment_account_number or order["payment_account_number"],
                body.payment_account_holder or order["payment_account_holder"],
                ctx["user_id"],
            )

            return {
                "success": True,
                "data": serialize_proforma(row, order["order_number"], 0.0),
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating proforma: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create proforma")


@router.patch("/{proforma_id}")
async def update_proforma(request: Request, proforma_id: str, body: UpdateProformaRequest):
    """Ubah proforma. HANYA saat status 'draft'."""
    try:
        ctx = get_user_context(request)
        pid = _uuid_or_404(proforma_id)
        pool = await get_pool()

        async with pool.acquire() as conn:
            cur = await conn.fetchrow(
                "SELECT * FROM proformas WHERE id = $1 AND tenant_id = $2",
                pid,
                ctx["tenant_id"],
            )
            if not cur:
                raise HTTPException(status_code=404, detail="Proforma not found")
            if cur["status"] != "draft":
                raise HTTPException(
                    status_code=400,
                    detail=f"Proforma berstatus '{cur['status']}' tidak bisa diubah. Hanya 'draft'.",
                )

            order = await fetch_order_or_404(conn, ctx["tenant_id"], cur["sales_order_id"])
            order_total = _f(order["total_amount"]) or 0.0

            if body.purpose is not None and body.purpose not in VALID_PURPOSES:
                raise HTTPException(
                    status_code=400,
                    detail=f"purpose harus salah satu dari {list(VALID_PURPOSES)}",
                )

            percent = _f(body.percent_of_order)
            amount = _f(body.amount)
            if percent is not None:
                if percent <= 0 or percent > 100:
                    raise HTTPException(
                        status_code=400, detail="percent_of_order harus di antara 0 dan 100."
                    )
                amount = round(order_total * percent / 100.0, 2)
            if amount is not None:
                if amount <= 0:
                    raise HTTPException(
                        status_code=400, detail="amount harus lebih besar dari 0."
                    )
                await assert_within_order_total(
                    conn, ctx["tenant_id"], cur["sales_order_id"], order_total, amount,
                    exclude_id=pid,
                )

            row = await conn.fetchrow(
                """
                UPDATE proformas SET
                    purpose = COALESCE($3, purpose),
                    percent_of_order = CASE WHEN $4::numeric IS NOT NULL THEN $4::numeric
                                            WHEN $5::numeric IS NOT NULL THEN NULL
                                            ELSE percent_of_order END,
                    amount = COALESCE($6::numeric, amount),
                    proforma_date = COALESCE($7::date, proforma_date),
                    due_date = COALESCE($8::date, due_date),
                    terms = COALESCE($9, terms),
                    notes = COALESCE($10, notes),
                    payment_bank_name = COALESCE($11, payment_bank_name),
                    payment_account_number = COALESCE($12, payment_account_number),
                    payment_account_holder = COALESCE($13, payment_account_holder)
                WHERE id = $1 AND tenant_id = $2
                RETURNING *
                """,
                pid,
                ctx["tenant_id"],
                body.purpose,
                Decimal(str(percent)) if percent is not None else None,
                Decimal(str(body.amount)) if body.amount is not None else None,
                Decimal(str(amount)) if amount is not None else None,
                body.proforma_date,
                body.due_date,
                body.terms,
                body.notes,
                body.payment_bank_name,
                body.payment_account_number,
                body.payment_account_holder,
            )

            paid = await compute_paid_amount(conn, ctx["tenant_id"], pid)
            return {
                "success": True,
                "data": serialize_proforma(row, order["order_number"], paid),
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating proforma {proforma_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update proforma")


@router.post("/{proforma_id}/issue")
async def issue_proforma(request: Request, proforma_id: str):
    """draft -> issued. NON-POSTING: tidak ada jurnal yang dibuat."""
    try:
        ctx = get_user_context(request)
        pid = _uuid_or_404(proforma_id)
        pool = await get_pool()

        async with pool.acquire() as conn:
            cur = await conn.fetchrow(
                "SELECT * FROM proformas WHERE id = $1 AND tenant_id = $2",
                pid,
                ctx["tenant_id"],
            )
            if not cur:
                raise HTTPException(status_code=404, detail="Proforma not found")
            if cur["status"] != "draft":
                raise HTTPException(
                    status_code=400,
                    detail=f"Hanya proforma 'draft' yang bisa diterbitkan (sekarang '{cur['status']}').",
                )

            order = await fetch_order_or_404(conn, ctx["tenant_id"], cur["sales_order_id"])
            if order["status"] not in SO_BILLABLE_STATUSES:
                raise HTTPException(
                    status_code=400,
                    detail=f"Sales Order berstatus '{order['status']}' tidak bisa ditagih.",
                )

            await assert_within_order_total(
                conn,
                ctx["tenant_id"],
                cur["sales_order_id"],
                _f(order["total_amount"]) or 0.0,
                _f(cur["amount"]) or 0.0,
                exclude_id=pid,
            )

            row = await conn.fetchrow(
                """
                UPDATE proformas SET status = 'issued', issued_at = NOW()
                WHERE id = $1 AND tenant_id = $2 AND status = 'draft'
                RETURNING *
                """,
                pid,
                ctx["tenant_id"],
            )
            if not row:
                raise HTTPException(status_code=409, detail="Proforma sudah berubah status.")

            paid = await compute_paid_amount(conn, ctx["tenant_id"], pid)
            return {
                "success": True,
                "data": serialize_proforma(row, order["order_number"], paid),
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error issuing proforma {proforma_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to issue proforma")


@router.post("/{proforma_id}/cancel")
async def cancel_proforma(request: Request, proforma_id: str, body: CancelProformaRequest):
    """Batalkan proforma. DITOLAK bila sudah ada deposit yang menunjuk padanya."""
    try:
        ctx = get_user_context(request)
        pid = _uuid_or_404(proforma_id)
        pool = await get_pool()

        async with pool.acquire() as conn:
            cur = await conn.fetchrow(
                "SELECT * FROM proformas WHERE id = $1 AND tenant_id = $2",
                pid,
                ctx["tenant_id"],
            )
            if not cur:
                raise HTTPException(status_code=404, detail="Proforma not found")
            if cur["status"] == "cancelled":
                raise HTTPException(status_code=400, detail="Proforma sudah dibatalkan.")

            paid = await compute_paid_amount(conn, ctx["tenant_id"], pid)
            if paid > 0:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Proforma sudah menerima pembayaran {paid:,.2f}. "
                        f"Tidak bisa dibatalkan — lakukan refund uang muka terlebih dahulu."
                    ),
                )

            order = await fetch_order_or_404(conn, ctx["tenant_id"], cur["sales_order_id"])

            row = await conn.fetchrow(
                """
                UPDATE proformas
                SET status = 'cancelled', cancelled_at = NOW(), cancelled_reason = $3
                WHERE id = $1 AND tenant_id = $2
                RETURNING *
                """,
                pid,
                ctx["tenant_id"],
                body.reason,
            )
            return {
                "success": True,
                "data": serialize_proforma(row, order["order_number"], 0.0),
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error cancelling proforma {proforma_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to cancel proforma")


# ============================================================================
# PDF
# ============================================================================


@router.get("/{proforma_id}/pdf")
async def get_proforma_pdf(request: Request, proforma_id: str):
    """PDF tagihan uang muka. BUKAN faktur pajak."""
    try:
        ctx = get_user_context(request)
        pid = _uuid_or_404(proforma_id)
        pool = await get_pool()

        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT p.*, so.order_number, so.order_date, so.total_amount AS order_total_amount
                FROM proformas p
                LEFT JOIN sales_orders so
                       ON so.id = p.sales_order_id AND so.tenant_id = p.tenant_id
                WHERE p.id = $1 AND p.tenant_id = $2
                """,
                pid,
                ctx["tenant_id"],
            )
            if not row:
                raise HTTPException(status_code=404, detail="Proforma not found")

            paid = await compute_paid_amount(conn, ctx["tenant_id"], pid)

            tenant_row = await conn.fetchrow(
                'SELECT display_name, address, phone, logo_url FROM "Tenant" WHERE id = $1',
                ctx["tenant_id"],
            )
            if tenant_row:
                tenant_info = {
                    "name": tenant_row["display_name"],
                    "address": tenant_row["address"],
                    "phone": tenant_row["phone"],
                    "logo_url": tenant_row["logo_url"],
                }
            else:
                tenant_info = {
                    "name": ctx["tenant_id"],
                    "address": None,
                    "phone": None,
                    "logo_url": None,
                }

            _logo_data = None
            _logo_filename = tenant_info.get("logo_url")
            if _logo_filename:
                _logo_path = (
                    _Path(__file__).parent.parent / "static" / "logos" / _logo_filename
                )
                if _logo_path.exists():
                    with open(_logo_path, "rb") as _lf:
                        _logo_data = (
                            "data:image/png;base64,"
                            + base64.b64encode(_lf.read()).decode()
                        )
            tenant_info["logo_data"] = _logo_data

            amount = _f(row["amount"]) or 0.0
            proforma_data = {
                "id": str(row["id"]),
                "proforma_number": row["proforma_number"],
                "proforma_date": row["proforma_date"].isoformat()
                if row["proforma_date"]
                else None,
                "due_date": row["due_date"].isoformat() if row["due_date"] else None,
                "sales_order_number": row["order_number"],
                "sales_order_date": row["order_date"].isoformat()
                if row["order_date"]
                else None,
                "order_total_amount": _f(row["order_total_amount"]),
                "customer_name": row["customer_name"],
                "purpose": row["purpose"],
                "percent_of_order": _f(row["percent_of_order"]),
                "amount": amount,
                "paid_amount": paid,
                "outstanding_amount": round(amount - paid, 2),
                "currency": row["currency"],
                "terms": row["terms"],
                "notes": row["notes"],
                "payment_bank_name": row["payment_bank_name"],
                "payment_account_number": row["payment_account_number"],
                "payment_account_holder": row["payment_account_holder"],
                "status": row["status"],
            }

        from ..services.pdf_service import get_pdf_service

        pdf_bytes = get_pdf_service().generate_proforma_pdf(proforma_data, tenant_info)

        filename = f"Proforma-{row['proforma_number'] or str(proforma_id)[:8]}.pdf"
        return StreamingResponse(
            BytesIO(pdf_bytes),
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'inline; filename="{filename}"',
                "Cache-Control": "no-store",
            },
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating PDF for proforma {proforma_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate PDF")
