"""
Vendor Credits Router - Purchase Returns and AP Adjustments

Endpoints for managing vendor credits (kredit vendor).
Vendor credits reduce Accounts Payable and can be applied to bills or refunded by vendor.

Flow:
1. Create draft vendor credit
2. Post to accounting (creates AP reduction journal)
3. Apply to bill(s) OR receive refund from vendor
4. Void if needed (only if unapplied)

Endpoints:
- GET    /vendor-credits              - List vendor credits
- GET    /vendor-credits/summary      - Summary statistics
- GET    /vendor-credits/{id}         - Get vendor credit detail
- POST   /vendor-credits              - Create draft vendor credit
- PATCH  /vendor-credits/{id}         - Update draft vendor credit
- DELETE /vendor-credits/{id}         - Delete draft vendor credit
- POST   /vendor-credits/{id}/post    - Post to accounting
- POST   /vendor-credits/{id}/apply   - Apply to bill(s)
- POST   /vendor-credits/{id}/receive-refund - Record refund from vendor
- POST   /vendor-credits/{id}/void    - Void vendor credit
"""

from fastapi import APIRouter, HTTPException, Request, Query, Response
from pydantic import BaseModel
from typing import Optional, Literal
from uuid import UUID
import logging
import asyncpg
from datetime import date
from decimal import Decimal

from ..services.pihak_helpers import segarkan_cache_hutang_tagihan
from ..services import nota_debit as ND  # U3a (22 Okt 2026): terapkan/lepas/sisa jurnal/void lengkap
from ..services import nota_debit_daftar as NDD  # U3c (23 Okt 2026): daftar/detail/bundel seragam (kembar nota_kredit_daftar)
from ..services import nota_debit_rencana as NDR  # U3d (23 Okt 2026): penentu per aksi SATU untuk tulis + pratinjau
from ..utils.tanggal_tenant import tanggal_dokumen
from ..schemas.vendor_credits import (
    UnapplyVendorCreditRequest,
    CreateVendorCreditRequest,
    UpdateVendorCreditRequest,
    ApplyVendorCreditRequest,
    ReceiveRefundRequest,
    VoidVendorCreditRequest,
    VendorCreditResponse,
    VendorCreditDetailResponse,
    VendorCreditListResponse,
    VendorCreditSummaryResponse,
)
from ..services.resolve_account import resolve_account_id  # noqa: F401  (read-side helpers may still use)
from ..services.role_resolver import (
    AccountRole,
    resolve_account_id_by_role,
    resolve_account_id_by_role_if_pkp,
    PESAN_NON_PKP_PEMBELIAN,
)
from ..services.role_precondition import assert_required_roles_for_path

logger = logging.getLogger(__name__)
router = APIRouter()

# Fase D2.2: required role mappings for vendor_credits posting path.
# - AP_TRADE: settled by the credit (Dr AP reduces liability).
# - VAT_INPUT: PKP-gated reversal (Cr VAT_INPUT reduces PPN Masukan asset).
# - COGS_PURCHASE_RETURN: contra-COGS for returned goods (Cr 5-10300).
#   Physical inventory layer decrement is handled separately by
#   record_inventory_outbound() — journal credit is the COGS reversal side,
#   NOT a second persediaan debit/credit (which would double-count).
VENDOR_CREDIT_REQUIRED_ROLES = [
    AccountRole.AP_TRADE,
    AccountRole.VAT_INPUT,
    AccountRole.COGS_PURCHASE_RETURN,
]

# One-time precondition check flag.
_precondition_checked_tenants: set = set()


async def _ensure_role_preconditions(pool, tenant_id=None):
    """Run role-mapping precondition for vendor_credits.

    Scopes the audit to the ACTING tenant when tenant_id is supplied (cached
    per-tenant); tenant_id=None preserves the legacy all-tenants behavior.
    Fails loud (PreconditionFailedError) if the audited tenant lacks any
    required role mapping.
    """
    if tenant_id is None:
        await assert_required_roles_for_path(
            pool, "vendor_credits", VENDOR_CREDIT_REQUIRED_ROLES
        )
        return
    if tenant_id in _precondition_checked_tenants:
        return
    await assert_required_roles_for_path(
        pool, "vendor_credits", VENDOR_CREDIT_REQUIRED_ROLES, tenant_id=tenant_id
    )
    _precondition_checked_tenants.add(tenant_id)


async def get_pool() -> asyncpg.Pool:
    """Get singleton connection pool (Law 32)."""
    from ..services.db_pool import get_db_pool

    return await get_db_pool()


def get_user_context(request: Request) -> dict:
    """Extract and validate user context from request."""
    if not hasattr(request.state, "user") or not request.state.user:
        raise HTTPException(status_code=401, detail="Silakan masuk terlebih dahulu.")

    user = request.state.user
    tenant_id = user.get("tenant_id")
    user_id = user.get("user_id")

    if not tenant_id:
        raise HTTPException(status_code=401, detail="Sesi tidak valid, silakan masuk lagi.")

    return {"tenant_id": tenant_id, "user_id": UUID(user_id) if user_id else None}


async def get_bill_remaining_from_journal(conn, tenant_id: str, bill_id) -> int:
    """Sisa hutang tagihan dari JURNAL -- compute_ap_rincian (V440/V441), SATU sumber dgn daftar/detail Faktur Pembelian.
    U3a (22 Okt 2026): dulu SQL sendiri yang mengurangkan SELURUH debit AP nota debit ke tiap tagihan bertaut. 0 = tagihan tak aktif."""
    v = await ND.sisa_tagihan_jurnal(conn, tenant_id, bill_id)
    return int(v) if v is not None else 0

def calculate_item_totals(item: dict) -> dict:
    """Calculate item totals with discount and tax."""
    quantity = Decimal(str(item.get("quantity", 0)))
    unit_price = Decimal(str(item.get("unit_price", 0)))
    discount_percent = Decimal(str(item.get("discount_percent", 0)))
    discount_amount = Decimal(str(item.get("discount_amount", 0)))
    tax_rate = Decimal(str(item.get("tax_rate", 0)))

    subtotal = quantity * unit_price

    # Apply discount (percent takes precedence)
    if discount_percent > 0:
        discount = subtotal * discount_percent / 100
    else:
        discount = discount_amount

    after_discount = subtotal - discount

    # Apply tax
    tax_amount = after_discount * tax_rate / 100

    total = after_discount + tax_amount

    return {
        **item,
        "subtotal": int(subtotal),
        "discount_amount": int(discount),
        "tax_amount": int(tax_amount),
        "total": int(total),
    }


# =============================================================================
# LIST VENDOR CREDITS
# =============================================================================


@router.get("", response_model=VendorCreditListResponse)
async def list_vendor_credits(
    request: Request,
    status: Optional[
        Literal["all", "draft", "posted", "partial", "applied", "void"]
    ] = Query("all"),
    vendor_id: Optional[str] = Query(None),
    search: Optional[str] = Query(None, description="Search by number or vendor name"),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    sort_by: Literal[
        "credit_date", "credit_number", "total_amount", "created_at"
    ] = Query("created_at"),
    sort_order: Literal["asc", "desc"] = Query("desc"),
    # U3c (23 Okt 2026, kembar S2-NK): pill AKSI (predikat SAMA dengan /summary) + total atas SEMUA hasil filter (opt-in)
    aksi: Optional[Literal["belum_terpakai"]] = Query(None),
    with_totals: bool = Query(False),
):
    """List vendor credits with filters and pagination."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Build query conditions
            conditions = ["tenant_id = $1"]
            params = [ctx["tenant_id"]]
            param_idx = 2

            if status and status != "all":
                conditions.append(f"status = ${param_idx}")
                params.append(status)
                param_idx += 1

            if vendor_id:
                conditions.append(f"vendor_id = ${param_idx}")
                params.append(UUID(vendor_id))
                param_idx += 1

            if isinstance(search, str) and search.strip():  # U3c: per kata (nomor, vendor, ref, tagihan asal) == /summary?search=
                _c, _p = NDD.syarat_cari(search, param_idx)
                conditions.append(_c)
                params.extend(_p)
                param_idx += len(_p)

            if aksi:
                conditions.append(NDD.sql_aksi(aksi))

            if date_from:
                conditions.append(f"credit_date >= ${param_idx}")
                params.append(date_from)
                param_idx += 1

            if date_to:
                conditions.append(f"credit_date <= ${param_idx}")
                params.append(date_to)
                param_idx += 1

            where_clause = " AND ".join(conditions)

            # Sort
            valid_sorts = {
                "credit_date": "credit_date",
                "credit_number": "credit_number",
                "total_amount": "total_amount",
                "created_at": "created_at",
            }
            sort_field = valid_sorts.get(sort_by, "created_at")
            sort_dir = "DESC" if sort_order == "desc" else "ASC"

            # Count total
            count_query = f"SELECT COUNT(*) FROM vendor_credits WHERE {where_clause}"
            total = await conn.fetchval(count_query, *params)
            jumlah = {}
            if with_totals is True:  # Query(False) truthy pada panggilan langsung -> `is True` (== NK)
                t = await conn.fetchrow(
                    f"""SELECT COALESCE(SUM(total_amount) FILTER (WHERE status <> 'void'), 0) AS sum_total,
                               COALESCE(SUM({ND.sql_sisa_nd()}), 0) AS sum_sisa
                        FROM vendor_credits WHERE {where_clause}""", *params)
                jumlah = {"sum_total": float(t["sum_total"]), "sum_sisa": float(t["sum_sisa"])}

            # Get items
            query = f"""
                SELECT id, credit_number, vendor_id, vendor_name,
                       credit_date, total_amount, amount_applied, amount_received,
                       status, reason, ref_no, created_at, original_bill_id, original_bill_number
                FROM vendor_credits
                WHERE {where_clause}
                ORDER BY {sort_field} {sort_dir}
                LIMIT ${param_idx} OFFSET ${param_idx + 1}
            """
            params.extend([limit, skip])

            rows = await conn.fetch(query, *params)

            items = [
                {
                    "id": str(row["id"]),
                    "vendor_credit_number": row["credit_number"],
                    "vendor_id": str(row["vendor_id"]) if row["vendor_id"] else None,
                    "vendor_name": row["vendor_name"],
                    "vendor_credit_date": row["credit_date"].isoformat(),
                    "total_amount": row["total_amount"],
                    "amount_applied": row["amount_applied"] or 0,
                    "amount_refunded": row["amount_received"] or 0,
                    "remaining_amount": NDD.sisa_lama(row["status"], row["total_amount"], row["amount_applied"], row["amount_received"]),
                    "status": row["status"],
                    "reason": row["reason"],
                    "ref_no": row["ref_no"],
                    "created_at": row["created_at"].isoformat(),
                }
                for row in rows
            ]
            # U3c: server menghitung, FE memformat -- SATU fungsi baris == detail (NDD.medan_baris), kueri ber-batch per halaman
            _medan = await NDD.medan_banyak(conn, ctx["tenant_id"], rows)
            for it, row in zip(items, rows):
                it.update(_medan.get(row["id"], {}))

            return {"items": items, "total": total, "has_more": (skip + limit) < total, **jumlah}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing vendor credits: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list vendor credits")


# =============================================================================
# SUMMARY
# =============================================================================


@router.get("/summary", response_model=VendorCreditSummaryResponse)
async def get_vendor_credits_summary(request: Request, search: Optional[str] = Query(None)):
    """Get summary statistics for vendor credits."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Law 16: Counts from table, amounts from journal (truth)
            query = """
                WITH vc_journal AS (
                    SELECT vc.id,
                           COALESCE(SUM(CASE WHEN coa.account_type = 'PAYABLE' AND jl.debit > 0 THEN jl.debit ELSE 0 END), 0) as journal_value
                    FROM vendor_credits vc
                    JOIN journal_entries je ON je.source_id = vc.id AND je.source_type = 'VENDOR_CREDIT'
                    JOIN journal_lines jl ON jl.journal_id = je.id
                    JOIN chart_of_accounts coa ON coa.id = jl.account_id
                    WHERE vc.tenant_id = $1 AND vc.status != 'void'
                      AND je.status = 'POSTED' AND je.reversed_by_id IS NULL
                    GROUP BY vc.id
                )
                SELECT
                    COUNT(*) as total,
                    COUNT(*) FILTER (WHERE vc.status = 'draft') as draft_count,
                    COUNT(*) FILTER (WHERE vc.status = 'posted') as posted_count,
                    COUNT(*) FILTER (WHERE vc.status = 'partial') as partial_count,
                    COUNT(*) FILTER (WHERE vc.status = 'applied') as applied_count,
                    COALESCE(SUM(vcj.journal_value), 0) as total_value,
                    COALESCE(SUM(vc.amount_applied), 0) as total_applied,
                    COALESCE(SUM(vc.amount_received), 0) as total_refunded,
                    COALESCE(SUM(vcj.journal_value - COALESCE(vc.amount_applied, 0) - COALESCE(vc.amount_received, 0))
                        FILTER (WHERE vc.status IN ('posted', 'partial')), 0) as available_balance
                FROM vendor_credits vc
                LEFT JOIN vc_journal vcj ON vcj.id = vc.id
                WHERE vc.tenant_id = $1 AND vc.status != 'void' AND __CARI__
            """
            # U3c ?search= (predikat SAMA dgn daftar): batasi ke id hasil cari
            _cari_sql, _cari_p = NDD.syarat_cari(search if isinstance(search, str) else "", 2)
            query = query.replace("__CARI__", NDD.syarat_cari(search if isinstance(search, str) else "", 2, a="vc")[0])
            row = await conn.fetchrow(query, ctx["tenant_id"], *_cari_p)
            # U3c pill AKSI belum_terpakai (predikat SAMA dgn ?aksi=) + total_applied = Σ terpakai turunan JURNAL (kembar NK, SATU definisi
            # dengan medan baris `terpakai`)
            aksi_row = await conn.fetchrow(
                f"""SELECT COUNT(*) AS n, COALESCE(SUM({ND.sql_sisa_nd()}), 0) AS v FROM vendor_credits
                    WHERE vendor_credits.tenant_id = $1 AND {NDD.sql_aksi(NDD.AKSI_BELUM_TERPAKAI)} AND {_cari_sql}""", ctx["tenant_id"], *_cari_p)
            _ids = [r["id"] for r in await conn.fetch(
                f"SELECT id FROM vendor_credits WHERE tenant_id = $1 AND status != 'void' AND {_cari_sql}", ctx["tenant_id"], *_cari_p)]
            _total_terpakai = sum((v["terpakai"] for v in (await NDD.rincian_nd_many(conn, ctx["tenant_id"], _ids)).values()), Decimal("0"))

            return {
                "success": True,
                "data": {
                    "total": row["total"] or 0,
                    "draft_count": row["draft_count"] or 0,
                    "posted_count": row["posted_count"] or 0,
                    "partial_count": row["partial_count"] or 0,
                    "applied_count": row["applied_count"] or 0,
                    "total_value": int(row["total_value"] or 0),
                    "total_applied": int(_total_terpakai),  # U3c: Σ terpakai turunan jurnal (== Σ medan baris; kembar NK)
                    "total_refunded": int(row["total_refunded"] or 0),
                    "available_balance": int(row["available_balance"] or 0),
                    f"{NDD.AKSI_BELUM_TERPAKAI}_count": aksi_row["n"] or 0,
                    f"{NDD.AKSI_BELUM_TERPAKAI}_value": float(aksi_row["v"] or 0),
                },
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting vendor credits summary: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get summary")


# =============================================================================
# GET VENDOR CREDIT DETAIL
# =============================================================================


@router.get("/{vendor_credit_id}", response_model=VendorCreditDetailResponse)
async def get_vendor_credit(request: Request, vendor_credit_id: UUID):
    """Get detailed information for a vendor credit."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Get vendor credit
            vc = await conn.fetchrow(
                """
                SELECT * FROM vendor_credits
                WHERE id = $1 AND tenant_id = $2
            """,
                vendor_credit_id,
                ctx["tenant_id"],
            )

            if not vc:
                raise HTTPException(status_code=404, detail="Vendor credit not found")

            # Get items
            items = await conn.fetch(
                """
                SELECT * FROM vendor_credit_items
                WHERE vendor_credit_id = $1
                ORDER BY line_number
            """,
                vendor_credit_id,
            )

            # Get applications with bill numbers
            applications = await conn.fetch(
                """
                SELECT a.*, b.invoice_number as bill_number
                FROM vendor_credit_applications a
                LEFT JOIN bills b ON a.bill_id = b.id
                WHERE a.vendor_credit_id = $1
                ORDER BY a.application_date, a.created_at, (a.reversed_at IS NULL), a.id  -- 23 Okt: deterministik; seri -> yang dilepas dulu, aktif terakhir
            """,
                vendor_credit_id,
            )

            # Get refunds
            refunds = await conn.fetch(
                """
                SELECT * FROM vendor_credit_refunds
                WHERE vendor_credit_id = $1
                ORDER BY refund_date
            """,
                vendor_credit_id,
            )

            # Build response
            remaining = NDD.sisa_lama(vc["status"], vc["total_amount"], vc["amount_applied"], vc["amount_received"])
            _medan = (await NDD.medan_banyak(conn, ctx["tenant_id"], [vc])).get(vc["id"], {})  # U3c: == baris daftar

            return {
                "success": True,
                "data": {
                    "id": str(vc["id"]),
                    "vendor_credit_number": vc["credit_number"],
                    "vendor_id": str(vc["vendor_id"]) if vc["vendor_id"] else None,
                    "vendor_name": vc["vendor_name"],
                    "original_bill_id": str(vc["original_bill_id"])
                    if vc["original_bill_id"]
                    else None,
                    "original_bill_number": vc["original_bill_number"],
                    "subtotal": vc["subtotal"],
                    "discount_percent": float(vc["discount_percent"] or 0),
                    "discount_amount": vc["discount_amount"] or 0,
                    "tax_rate": float(vc["tax_rate"] or 0),
                    "tax_amount": vc["tax_amount"] or 0,
                    "total_amount": vc["total_amount"],
                    "amount_applied": vc["amount_applied"] or 0,
                    "amount_refunded": vc["amount_received"] or 0,
                    "remaining_amount": remaining,
                    "status": vc["status"],
                    "vendor_credit_date": vc["credit_date"].isoformat(),
                    "reason": vc["reason"],
                    "reason_detail": vc["reason_detail"],
                    "ref_no": vc["ref_no"],
                    "notes": vc["notes"],
                    "ap_id": str(vc["ap_id"]) if vc["ap_id"] else None,
                    "journal_id": str(vc["journal_id"]) if vc["journal_id"] else None,
                    "items": [
                        {
                            "id": str(item["id"]),
                            "item_id": str(item["item_id"])
                            if item["item_id"]
                            else None,
                            "item_code": item["item_code"],
                            "description": item["description"],
                            "quantity": float(item["quantity"]),
                            "unit": item["unit"],
                            "unit_price": item["unit_price"],
                            "discount_percent": float(item["discount_percent"] or 0),
                            "discount_amount": item["discount_amount"] or 0,
                            "tax_code": item["tax_code"],
                            "tax_rate": float(item["tax_rate"] or 0),
                            "tax_amount": item["tax_amount"] or 0,
                            "subtotal": item["subtotal"],
                            "total": item["total"],
                            "line_number": item["line_number"],
                        }
                        for item in items
                    ],
                    "applications": [
                        {
                            "id": str(app["id"]),
                            "bill_id": str(app["bill_id"]),
                            "bill_number": app["bill_number"],
                            "amount_applied": app["amount_applied"],
                            "application_date": app["application_date"].isoformat(),
                            "created_at": app["created_at"].isoformat(),
                            # 23 Okt 2026 (aditif, temuan ANDROID): riwayat penerapan V443 append-only -- baris DILEPAS tetap tampil, ditandai
                            "status": app["status"] or "active",
                            "reversed_at": app["reversed_at"].isoformat() if app["reversed_at"] else None,
                            "reversal_reason": app["reversal_reason"],
                        }
                        for app in applications
                    ],
                    "refunds": [
                        {
                            "id": str(ref["id"]),
                            "amount": ref["amount"],
                            "refund_date": ref["refund_date"].isoformat(),
                            "payment_method": ref["payment_method"],
                            "account_id": str(ref["account_id"]),
                            "reference": ref["reference"],
                            "created_at": ref["created_at"].isoformat(),
                        }
                        for ref in refunds
                    ],
                    "posted_at": vc["posted_at"].isoformat()
                    if vc["posted_at"]
                    else None,
                    "posted_by": str(vc["posted_by"]) if vc["posted_by"] else None,
                    "voided_at": vc["voided_at"].isoformat()
                    if vc["voided_at"]
                    else None,
                    "voided_reason": vc["voided_reason"],
                    "created_at": vc["created_at"].isoformat(),
                    "updated_at": vc["updated_at"].isoformat(),
                    "created_by": str(vc["created_by"]) if vc["created_by"] else None,
                    **_medan,
                },
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Error getting vendor credit {vendor_credit_id}: {e}", exc_info=True
        )
        raise HTTPException(status_code=500, detail="Failed to get vendor credit")


@router.get("/{vendor_credit_id}/documents")
async def get_vendor_credit_documents(request: Request, vendor_credit_id: UUID):
    """Bundel panel berjangkar NOTA DEBIT (U3c 23 Okt 2026): skema SAMA dgn bundel tagihan/PO {anchor, default, groups}; pembentuk SATU
    tempat (NDD.kelompok_nd = sumber documents[] baris). Jangkar paid/balance = null untuk bukan-posted (pola bundel bayar keluar);
    posted: paid = terpakai + dikembalikan, balance = sisa jurnal."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        async with pool.acquire() as conn:
            tid = ctx["tenant_id"]
            v = await conn.fetchrow(
                f"""SELECT vendor_credits.*, {ND.sql_sisa_nd()} AS sisa FROM vendor_credits
                    WHERE vendor_credits.tenant_id = $1 AND vendor_credits.id = $2""", tid, vendor_credit_id)
            if not v:
                raise HTTPException(status_code=404, detail="Nota debit tidak ditemukan.")
            kel = (await NDD.kelompok_nd(conn, tid, [{"id": vendor_credit_id}])).get(vendor_credit_id, [])
            r = (await NDD.rincian_nd_many(conn, tid, [vendor_credit_id])).get(str(vendor_credit_id), {})
            kontak = await conn.fetchrow(
                "SELECT COALESCE(mobile_phone, phone) AS phone, email FROM vendors WHERE id = $1 AND tenant_id = $2",
                v["vendor_id"], tid) if v["vendor_id"] else None
            sd = NDD.status_detail_nd(v["status"], v["amount_applied"], v["amount_received"])
            terbit = v["status"] not in ("draft", "void")
            return {
                "anchor": {"kind": "debit_note", "id": str(v["id"]), "number": v["credit_number"], "vendor": v["vendor_name"] or "",
                           "position_text": NDD.status_text_nd(v["status"], sd, v["original_bill_id"]),
                           "total": f"{Decimal(str(v['total_amount'] or 0)):.2f}",
                           "paid": f"{(r.get('terpakai', Decimal(0)) + r.get('dikembalikan', Decimal(0))):.2f}" if terbit else None,
                           "balance": f"{Decimal(str(v['sisa'] or 0)):.2f}" if terbit else None,
                           "vendor_phone": (kontak["phone"] if kontak else None) or None,
                           "vendor_email": (kontak["email"] if kontak else None) or None},
                "default": {"group": "nota_debit", "index": 0},
                "groups": kel,
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting vendor credit documents {vendor_credit_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Gagal memuat dokumen nota debit.")


@router.get("/{vendor_credit_id}/history")
async def get_vendor_credit_history(request: Request, vendor_credit_id: UUID, limit: int = Query(200, ge=1, le=500)):
    """Riwayat NOTA DEBIT (U3c), bentuk SAMA dengan riwayat tagihan/PO/SO (services/nota_debit_riwayat). Terbaru dulu."""
    from ..services.dashboard_izin import boleh_baca
    from ..services.nota_debit_riwayat import riwayat_nota_debit
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        data = await riwayat_nota_debit(conn, ctx["tenant_id"], vendor_credit_id, lambda m: boleh_baca(request, m),
                                        limit if isinstance(limit, int) else 200)
    if data is None:
        raise HTTPException(status_code=404, detail="Nota debit tidak ditemukan.")
    return {"success": True, "data": data}


# =============================================================================
# CREATE VENDOR CREDIT (DRAFT)
# =============================================================================


@router.post("", response_model=VendorCreditResponse, status_code=201)
async def create_vendor_credit(request: Request, body: CreateVendorCreditRequest, response: Response = None):
    """
    Create a new vendor credit in draft status.

    Draft vendor credits can be edited before posting.
    """
    try:
        ctx = get_user_context(request)
        if not ctx["user_id"]:
            raise HTTPException(status_code=401, detail="ID pengguna wajib ada.")

        pool = await get_pool()
        from ..services import idem_buat
        kunci = idem_buat.kunci_dari(request)  # Law 14 (22 Okt 2026): VC_CREATE, pola idem_buat

        async with pool.acquire() as conn:
            async with conn.transaction():
                kp, sidik, lama = await idem_buat.mulai(conn, ctx, kunci, "VC_CREATE", body, "vendor_credits", "vendor_credit", response)
                if lama is not None:
                    return lama
                if body.vendor_id and not await conn.fetchval(
                        "SELECT 1 FROM vendors WHERE id = $1 AND tenant_id = $2", UUID(body.vendor_id), ctx["tenant_id"]):
                    raise HTTPException(status_code=400, detail="Vendor tidak ditemukan.")

                # Generate vendor credit number
                vc_number = await conn.fetchval(
                    "SELECT generate_vendor_credit_number($1, 'VC')", ctx["tenant_id"]
                )

                # Calculate items and totals
                calculated_items = [
                    calculate_item_totals(item.model_dump()) for item in body.items
                ]
                subtotal = sum(item["subtotal"] for item in calculated_items)
                total_tax = sum(item["tax_amount"] for item in calculated_items)

                # Apply overall discount
                if body.discount_percent > 0:
                    overall_discount = int(
                        subtotal * Decimal(str(body.discount_percent)) / 100
                    )
                else:
                    overall_discount = body.discount_amount

                # Apply overall tax if specified
                after_discount = subtotal - overall_discount
                if body.tax_rate > 0:
                    overall_tax = int(
                        after_discount * Decimal(str(body.tax_rate)) / 100
                    )
                else:
                    overall_tax = total_tax

                total_amount = after_discount + overall_tax

                # Get original bill number if provided
                original_bill_number = None
                if body.original_bill_id:
                    # U3a: tagihan WAJIB milik tenant ini & vendor yang sama (dulu tanpa filter tenant -> nomor tagihan tenant lain bocor)
                    bill = await ND.tagihan_milik(conn, ctx["tenant_id"], body.original_bill_id,
                                                  UUID(body.vendor_id) if body.vendor_id else None)
                    if bill:
                        original_bill_number = bill["invoice_number"]

                # Insert vendor credit
                vc_id = await conn.fetchval(
                    """
                    INSERT INTO vendor_credits (
                        tenant_id, credit_number, vendor_id, vendor_name,
                        original_bill_id, original_bill_number,
                        subtotal, discount_percent, discount_amount,
                        tax_rate, tax_amount, total_amount,
                        status, credit_date, reason, reason_detail,
                        ref_no, notes, created_by
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12,
                              'draft', $13, $14, $15, $16, $17, $18)
                    RETURNING id
                """,
                    ctx["tenant_id"],
                    vc_number,
                    UUID(body.vendor_id) if body.vendor_id else None,
                    body.vendor_name,
                    UUID(body.original_bill_id) if body.original_bill_id else None,
                    original_bill_number,
                    subtotal,
                    body.discount_percent,
                    overall_discount,
                    body.tax_rate,
                    overall_tax,
                    total_amount,
                    body.vendor_credit_date,
                    body.reason,
                    body.reason_detail,
                    body.ref_no,
                    body.notes,
                    ctx["user_id"],
                )

                # Insert items
                for idx, item in enumerate(calculated_items, 1):
                    await conn.execute(
                        """
                        INSERT INTO vendor_credit_items (
                            vendor_credit_id, item_id, item_code, description,
                            quantity, unit, unit_price,
                            discount_percent, discount_amount,
                            tax_code, tax_rate, tax_amount,
                            subtotal, total, line_number
                        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
                    """,
                        vc_id,
                        UUID(item["item_id"]) if item.get("item_id") else None,
                        item.get("item_code"),
                        item["description"],
                        item["quantity"],
                        item.get("unit"),
                        item["unit_price"],
                        item.get("discount_percent", 0),
                        item.get("discount_amount", 0),
                        item.get("tax_code"),
                        item.get("tax_rate", 0),
                        item.get("tax_amount", 0),
                        item["subtotal"],
                        item["total"],
                        idx,
                    )

                logger.info(f"Vendor credit created: {vc_id}, number={vc_number}")

                return await idem_buat.simpan(conn, ctx, kp, sidik, "VC_CREATE", {
                    "success": True,
                    "message": "Vendor credit created successfully",
                    "data": {
                        "id": str(vc_id),
                        "vendor_credit_number": vc_number,
                        "total_amount": total_amount,
                        "status": "draft",
                    },
                }, vc_id)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating vendor credit: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create vendor credit")


# =============================================================================
# UPDATE VENDOR CREDIT (DRAFT ONLY)
# =============================================================================


@router.patch("/{vendor_credit_id}", response_model=VendorCreditResponse)
async def update_vendor_credit(
    request: Request, vendor_credit_id: UUID, body: UpdateVendorCreditRequest, response: Response = None
):
    """
    Update a draft vendor credit.

    Only draft vendor credits can be updated.
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        from ..services import idem_buat as _ib
        from ..services.optimistic_concurrency import assert_if_match

        async with pool.acquire() as conn:
            async with conn.transaction():
                # P2P-3a (25 Okt): Law 14 kunci klien (VC_UPDATE) PERTAMA di tx; lalu kunci posting yang SAMA (VENDOR_CREDIT) + FOR UPDATE ->
                # ubah||posting berurutan; If-Match + status dibaca DI BAWAH kunci (dulu If-Match di koneksi lain, status tanpa kunci).
                _kp, _sd, _lama = await _ib.mulai_aksi(conn, ctx, _ib.kunci_dari(request), "VC_UPDATE", vendor_credit_id,
                                                       lambda: body.model_dump(mode="json", exclude_unset=True), response)
                if _lama is not None:
                    return _lama

                async def _jawab(r):
                    return (await _ib.simpan(conn, ctx, _kp, _sd, "VC_UPDATE", r, vendor_credit_id)) if _kp else r

                await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"VENDOR_CREDIT:{vendor_credit_id}")
                vc = await conn.fetchrow(
                    "SELECT id, status, updated_at FROM vendor_credits WHERE id = $1 AND tenant_id = $2 FOR UPDATE",
                    vendor_credit_id,
                    ctx["tenant_id"],
                )
                if vc:
                    assert_if_match(request, vc["updated_at"])

                if not vc:
                    raise HTTPException(
                        status_code=404, detail="Vendor credit not found"
                    )

                if vc["status"] != "draft":
                    raise HTTPException(
                        status_code=400,
                        detail="Only draft vendor credits can be updated",
                    )

                # Build update data
                update_data = body.model_dump(exclude_unset=True)

                if not update_data:
                    return await _jawab({
                        "success": True,
                        "message": "No changes provided",
                        "data": {"id": str(vendor_credit_id)},
                    })

                # Handle items if provided
                if "items" in update_data and update_data["items"]:
                    # Delete existing items
                    await conn.execute(
                        "DELETE FROM vendor_credit_items WHERE vendor_credit_id = $1",
                        vendor_credit_id,
                    )

                    # Calculate and insert new items
                    calculated_items = [
                        calculate_item_totals(item.model_dump()) for item in body.items
                    ]

                    subtotal = sum(item["subtotal"] for item in calculated_items)
                    total_tax = sum(item["tax_amount"] for item in calculated_items)

                    # Recalculate totals
                    discount_percent = update_data.get("discount_percent", 0)
                    discount_amount = update_data.get("discount_amount", 0)
                    tax_rate = update_data.get("tax_rate", 0)

                    if discount_percent > 0:
                        overall_discount = int(
                            subtotal * Decimal(str(discount_percent)) / 100
                        )
                    else:
                        overall_discount = discount_amount

                    after_discount = subtotal - overall_discount

                    if tax_rate > 0:
                        overall_tax = int(after_discount * Decimal(str(tax_rate)) / 100)
                    else:
                        overall_tax = total_tax

                    total_amount = after_discount + overall_tax

                    # Update totals
                    await conn.execute(
                        """
                        UPDATE vendor_credits
                        SET subtotal = $2, discount_amount = $3, tax_amount = $4, total_amount = $5
                        WHERE id = $1
                    """,
                        vendor_credit_id,
                        subtotal,
                        overall_discount,
                        overall_tax,
                        total_amount,
                    )

                    # Insert new items
                    for idx, item in enumerate(calculated_items, 1):
                        await conn.execute(
                            """
                            INSERT INTO vendor_credit_items (
                                vendor_credit_id, item_id, item_code, description,
                                quantity, unit, unit_price,
                                discount_percent, discount_amount,
                                tax_code, tax_rate, tax_amount,
                                subtotal, total, line_number
                            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
                        """,
                            vendor_credit_id,
                            UUID(item["item_id"]) if item.get("item_id") else None,
                            item.get("item_code"),
                            item["description"],
                            item["quantity"],
                            item.get("unit"),
                            item["unit_price"],
                            item.get("discount_percent", 0),
                            item.get("discount_amount", 0),
                            item.get("tax_code"),
                            item.get("tax_rate", 0),
                            item.get("tax_amount", 0),
                            item["subtotal"],
                            item["total"],
                            idx,
                        )

                    del update_data["items"]

                # U3a: medan skema `vendor_credit_date` -> kolom `credit_date` (dulu SET kolom hantu -> 500)
                if "vendor_credit_date" in update_data:
                    update_data["credit_date"] = update_data.pop("vendor_credit_date")
                if update_data.get("original_bill_id"):
                    _vid = update_data.get("vendor_id") or await conn.fetchval(
                        "SELECT vendor_id FROM vendor_credits WHERE id = $1 AND tenant_id = $2", vendor_credit_id, ctx["tenant_id"])
                    _b = await ND.tagihan_milik(conn, ctx["tenant_id"], update_data["original_bill_id"], UUID(str(_vid)) if _vid else None)
                    update_data["original_bill_number"] = _b["invoice_number"]

                # Update other fields
                if update_data:
                    excluded = {"items"}
                    updates = []
                    params = []
                    param_idx = 1

                    for field, value in update_data.items():
                        if field in excluded:
                            continue
                        updates.append(f"{field} = ${param_idx}")
                        if field in ("vendor_id", "original_bill_id") and value:
                            params.append(UUID(value))
                        else:
                            params.append(value)
                        param_idx += 1

                    if updates:
                        params.extend([vendor_credit_id, ctx["tenant_id"]])
                        query = f"""
                            UPDATE vendor_credits
                            SET {', '.join(updates)}, updated_at = NOW()
                            WHERE id = ${param_idx} AND tenant_id = ${param_idx + 1}
                        """
                        await conn.execute(query, *params)

                logger.info(f"Vendor credit updated: {vendor_credit_id}")

                return await _jawab({
                    "success": True,
                    "message": "Vendor credit updated successfully",
                    "data": {"id": str(vendor_credit_id)},
                })

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Error updating vendor credit {vendor_credit_id}: {e}", exc_info=True
        )
        raise HTTPException(status_code=500, detail="Failed to update vendor credit")


# =============================================================================
# DELETE VENDOR CREDIT (DRAFT ONLY)
# =============================================================================


@router.delete("/{vendor_credit_id}", response_model=VendorCreditResponse)
async def delete_vendor_credit(request: Request, vendor_credit_id: UUID, response: Response = None):
    """
    Delete a draft vendor credit.

    Only draft vendor credits can be deleted. Use void for posted vendor credits.
    P2P-3a (25 Okt): satu transaksi -- idem VC_DELETE pertama, kunci posting yang SAMA (VENDOR_CREDIT:) + FOR UPDATE, DELETE bersyarat
    status draf + tenant (dulu tanpa transaksi: hapus||posting bisa menghapus ND yang baru diposting; DELETE tanpa tenant).
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        from ..services import idem_buat as _ib

        async with pool.acquire() as conn:
            async with conn.transaction():
                _kp, _sd, _lama = await _ib.mulai_aksi(conn, ctx, _ib.kunci_dari(request), "VC_DELETE", vendor_credit_id, {}, response)
                if _lama is not None:
                    return _lama
                await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"VENDOR_CREDIT:{vendor_credit_id}")
                vc = await conn.fetchrow(
                    "SELECT id, status, credit_number FROM vendor_credits WHERE id = $1 AND tenant_id = $2 FOR UPDATE",
                    vendor_credit_id,
                    ctx["tenant_id"],
                )

                if not vc:
                    raise HTTPException(status_code=404, detail="Vendor credit not found")

                if vc["status"] != "draft":
                    raise HTTPException(
                        status_code=400,
                        detail="Only draft vendor credits can be deleted. Use void for posted.",
                    )

                # Delete (cascade will delete items) -- bersyarat: status berubah di sela = 409, bukan menghapus dokumen terbit
                if not await conn.fetchval(
                    "DELETE FROM vendor_credits WHERE id = $1 AND tenant_id = $2 AND status = 'draft' RETURNING id",
                    vendor_credit_id, ctx["tenant_id"],
                ):
                    raise HTTPException(status_code=409, detail="Nota debit sudah berubah status. Muat ulang halaman.")

                logger.info(f"Vendor credit deleted: {vendor_credit_id}")

                resp = {
                    "success": True,
                    "message": "Vendor credit deleted successfully",
                    "data": {
                        "id": str(vendor_credit_id),
                        "vendor_credit_number": vc["credit_number"],
                    },
                }
                return (await _ib.simpan(conn, ctx, _kp, _sd, "VC_DELETE", resp, vendor_credit_id)) if _kp else resp

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Error deleting vendor credit {vendor_credit_id}: {e}", exc_info=True
        )
        raise HTTPException(status_code=500, detail="Failed to delete vendor credit")


# =============================================================================
# POST VENDOR CREDIT TO ACCOUNTING
# =============================================================================


@router.post("/{vendor_credit_id}/post", response_model=VendorCreditResponse)
async def post_vendor_credit(request: Request, vendor_credit_id: UUID, response: Response = None):
    """
    Post vendor credit to accounting.

    Creates journal entry:
    - Dr. Accounts Payable
    - Cr. Purchase Returns (Persediaan)
    - Cr. VAT Receivable (if tax)

    Changes status from 'draft' to 'posted'.
    """
    try:
        ctx = get_user_context(request)
        if not ctx["user_id"]:
            raise HTTPException(status_code=401, detail="ID pengguna wajib ada.")

        pool = await get_pool()
        await _ensure_role_preconditions(pool, ctx["tenant_id"])

        async with pool.acquire() as conn:
            async with conn.transaction():
                # Law 14 (23 Okt 2026, putusan pemilik LANGSUNG; kembar NK CN_*): X-Idempotency-Key klien saja -- kirim ulang = respons
                # pertama (SATU tulisan), isi beda = 409; tanpa kunci = perilaku lama.
                from ..services import idem_buat as _ib
                _kp, _sd, _lama = await _ib.mulai_aksi(conn, ctx, _ib.kunci_dari(request), "VC_POST", vendor_credit_id,
                                                       {}, response)
                if _lama is not None:
                    return _lama
                # Law 13 (U3a): kunci DULU, baru baca (dulu dibaca sebelum kunci -> dua posting serentak lolos cek status)
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1))",
                    f"VENDOR_CREDIT:{vendor_credit_id}",
                )
                # Get vendor credit
                vc = await conn.fetchrow(
                    """
                    SELECT * FROM vendor_credits
                    WHERE id = $1 AND tenant_id = $2
                """,
                    vendor_credit_id,
                    ctx["tenant_id"],
                )

                if not vc:
                    raise HTTPException(
                        status_code=404, detail="Nota debit tidak ditemukan."
                    )

                # U3d: PENENTU SATU dgn POST /{id}/post/preview (NDR.rencana_posting; urutan + pesan == kode lama): kaitan tagihan draf
                # (dibukukan? <= sisa) · status draf · akun peran · pagar potongan PPN (409) · akun dipetakan · periode
                _r = await NDR.rencana_posting(conn, ctx, vc)
                ap_account_id, purchase_return_account_id, potongan = _r["ap"], _r["akun_kredit"], _r["potongan"]

                # Generate journal number
                import uuid as uuid_module

                journal_id = uuid_module.uuid4()

                journal_number = await conn.fetchval(
                    """
                    SELECT get_next_journal_number($1, 'VC')
                """,
                    ctx["tenant_id"],
                )

                if not journal_number:
                    journal_number = f"VC-{vc['credit_number']}"

                # Calculate amounts
                total_amount = vc["total_amount"]
                tax_amount = vc["tax_amount"] or 0
                subtotal = total_amount - tax_amount

                # Law 20: Create journal entry as DRAFT (Law 25: no float())
                await conn.execute(
                    """
                    INSERT INTO journal_entries (
                        id, tenant_id, journal_number, journal_date,
                        description, source_type, source_id,
                        status, total_debit, total_credit, created_by
                    ) VALUES ($1, $2, $3, $4, $5, 'VENDOR_CREDIT', $6, 'DRAFT', $7, $7, $8)
                """,
                    journal_id,
                    ctx["tenant_id"],
                    journal_number,
                    vc["credit_date"],
                    f"Vendor Credit {vc['credit_number']} - {vc['vendor_name']}",
                    vendor_credit_id,
                    total_amount,
                    ctx["user_id"],
                )

                # Baris = NDR.baris_posting_nd (SATU sumber dgn pratinjau; Law 25: Decimal langsung): Dr Hutang · Cr Retur/Diskon · Cr PPN Masukan
                tax_jl_id = None
                for line_number, (_akun, _d, _k, _memo, _peran) in enumerate(
                        await NDR.baris_posting_nd(conn, ctx["tenant_id"], vc, ap_account_id, purchase_return_account_id, potongan), 1):
                    _jl = uuid_module.uuid4()
                    if _peran == "pajak":
                        tax_jl_id = _jl
                    await conn.execute(
                        """
                        INSERT INTO journal_lines (
                            id, journal_id, line_number, account_id, debit, credit, memo
                        ) VALUES ($1, $2, $3, $4, $5, $6, $7)
                    """,
                        _jl,
                        journal_id,
                        line_number,
                        _akun,
                        _d,
                        _k,
                        _memo,
                    )

                # Law 20: Post after all lines
                await conn.execute(
                    "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1",
                    journal_id,
                )

                # ── Inventory decrease for goods returned to vendor ──
                vc_items = await conn.fetch(
                    "SELECT * FROM vendor_credit_items WHERE vendor_credit_id = $1",
                    vendor_credit_id,
                )
                # U3b: potongan/koreksi harga TIDAK menggerakkan stok (barang tak kembali ke vendor; dulu semua alasan mengeluarkan stok)
                for item in ([] if potongan else vc_items):
                    if not item["item_id"]:
                        continue
                    product = await conn.fetchrow(
                        "SELECT id, item_code, nama_produk, track_inventory FROM products WHERE id = $1",
                        item["item_id"],
                    )
                    if not product or not product["track_inventory"]:
                        continue

                    wh_id = await conn.fetchval(
                        "SELECT id FROM warehouses WHERE tenant_id = $1 ORDER BY created_at LIMIT 1",
                        ctx["tenant_id"],
                    )
                    if not wh_id:
                        continue

                    from ..services.inventory_helpers import record_inventory_outbound

                    await record_inventory_outbound(
                        conn=conn,
                        tenant_id=ctx["tenant_id"],
                        product_id=product["id"],
                        product_code=product["item_code"],
                        product_name=product["nama_produk"],
                        warehouse_id=wh_id,
                        quantity=float(item["quantity"]),
                        source_type="VENDOR_CREDIT",
                        source_id=vendor_credit_id,
                        source_number=vc["credit_number"],
                        user_id=ctx["user_id"],
                        notes=f"Return to vendor - Vendor Credit {vc['credit_number']}",
                        receipt_date=vc["credit_date"],
                    )

                # Wave 3: Write document_tax_lines (PPN reversal on VC)
                if tax_amount > 0 and tax_jl_id:
                    ppn_tc_id = await conn.fetchval(
                        """
                        SELECT id FROM tax_codes
                        WHERE tenant_id = $1 AND tax_type = 'ppn' AND rate > 0 AND is_active = true
                        LIMIT 1
                        """,
                        ctx["tenant_id"],
                    )
                    if ppn_tc_id:
                        tc_coa = await conn.fetchval(
                            "SELECT coa_id FROM tax_codes WHERE id = $1",
                            ppn_tc_id,
                        )
                        dpp_val = float(subtotal)
                        await conn.execute(
                            """
                            INSERT INTO document_tax_lines (
                                id, tenant_id, document_type, document_id,
                                tax_code_id, direction, base_amount, tax_amount,
                                coa_id, journal_line_id
                            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                            """,
                            uuid_module.uuid4(),
                            ctx["tenant_id"],
                            "VENDOR_CREDIT",
                            vendor_credit_id,
                            ppn_tc_id,
                            "input",
                            dpp_val,
                            float(tax_amount),
                            tc_coa,
                            tax_jl_id,
                        )

                # Update vendor credit status
                await conn.execute(
                    """
                    UPDATE vendor_credits
                    SET status = 'posted', journal_id = $2,
                        posted_at = NOW(), posted_by = $3, updated_at = NOW()
                    WHERE id = $1
                """,
                    vendor_credit_id,
                    journal_id,
                    ctx["user_id"],
                )

                logger.info(
                    f"Vendor credit posted: {vendor_credit_id}, journal={journal_id}"
                )

                return await _ib.simpan(conn, ctx, _kp, _sd, "VC_POST", {
                    "success": True,
                    "message": "Vendor credit posted to accounting",
                    "data": {
                        "id": str(vendor_credit_id),
                        "journal_id": str(journal_id),
                        "journal_number": journal_number,
                        "status": "posted",
                    },
                }, vendor_credit_id)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Error posting vendor credit {vendor_credit_id}: {e}", exc_info=True
        )
        raise HTTPException(status_code=500, detail="Failed to post vendor credit")


# =============================================================================
# APPLY VENDOR CREDIT TO BILL(S)
# =============================================================================


@router.post("/{vendor_credit_id}/apply", response_model=VendorCreditResponse)
async def apply_vendor_credit(
    request: Request, vendor_credit_id: UUID, body: ApplyVendorCreditRequest, response: Response = None
):
    """Terapkan nota debit ke SATU tagihan, seluruh nilainya (U3a, putusan pemilik 22 Okt; kembar Nota Kredit, pola NetSuite apply credit).
    TANPA jurnal: hutang sudah didebit saat posting; terapkan hanya mengatribusikan (V441 cabang 4 lewat original_bill_id, pagar V443)."""
    try:
        ctx = get_user_context(request)
        if not ctx["user_id"]:
            raise HTTPException(status_code=401, detail="ID pengguna wajib ada.")
        if len(body.applications) != 1:
            raise HTTPException(status_code=400, detail="Nota debit hanya bisa diterapkan ke satu tagihan.")
        app = body.applications[0]

        pool = await get_pool()
        await _ensure_role_preconditions(pool, ctx["tenant_id"])

        async with pool.acquire() as conn:
            async with conn.transaction():
                # Law 14 (23 Okt 2026, putusan pemilik LANGSUNG; kembar NK CN_*): X-Idempotency-Key klien saja -- kirim ulang = respons
                # pertama (SATU tulisan), isi beda = 409; tanpa kunci = perilaku lama.
                from ..services import idem_buat as _ib
                _kp, _sd, _lama = await _ib.mulai_aksi(conn, ctx, _ib.kunci_dari(request), "VC_APPLY", vendor_credit_id,
                                                       lambda: body.model_dump(mode="json"), response)
                if _lama is not None:
                    return _lama
                tanggal = body.application_date or await tanggal_dokumen(conn, ctx["tenant_id"])  # t10-tanggal-bisnis
                hasil = await ND.terapkan_nota_debit(conn, ctx, vendor_credit_id, app.bill_id, app.amount, tanggal)
                logger.info(f"Nota debit diterapkan: {vendor_credit_id} -> tagihan {hasil['bill_id']}")
                return await _ib.simpan(conn, ctx, _kp, _sd, "VC_APPLY", {
                    "success": True,
                    "message": f"Nota debit diterapkan ke tagihan {hasil['bill_number']}.",
                    "data": {"id": str(vendor_credit_id), "applications": [hasil]},
                }, vendor_credit_id)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error applying vendor credit {vendor_credit_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Gagal menerapkan nota debit.")


@router.post("/{vendor_credit_id}/unapply", response_model=VendorCreditResponse)
async def unapply_vendor_credit(
    request: Request, vendor_credit_id: UUID, body: UnapplyVendorCreditRequest, response: Response = None
):
    """Lepas penerapan nota debit dari tagihan (U3a; kembar /credit-notes/{id}/unapply). TANPA jurnal; riwayat aplikasi append-only."""
    try:
        ctx = get_user_context(request)
        if not ctx["user_id"]:
            raise HTTPException(status_code=401, detail="ID pengguna wajib ada.")
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                # Law 14 (23 Okt 2026, putusan pemilik LANGSUNG; kembar NK CN_*): X-Idempotency-Key klien saja -- kirim ulang = respons
                # pertama (SATU tulisan), isi beda = 409; tanpa kunci = perilaku lama.
                from ..services import idem_buat as _ib
                _kp, _sd, _lama = await _ib.mulai_aksi(conn, ctx, _ib.kunci_dari(request), "VC_UNAPPLY", vendor_credit_id,
                                                       lambda: body.model_dump(mode="json"), response)
                if _lama is not None:
                    return _lama
                hasil = await ND.lepas_nota_debit(conn, ctx, vendor_credit_id, body.reason)
                return await _ib.simpan(conn, ctx, _kp, _sd, "VC_UNAPPLY", {"success": True, "message": "Penerapan nota debit dilepas.",
                                        "data": {"id": str(vendor_credit_id), **hasil}}, vendor_credit_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error unapplying vendor credit {vendor_credit_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Gagal melepas penerapan nota debit.")


# =============================================================================
# RECEIVE REFUND FROM VENDOR
# =============================================================================


@router.post("/{vendor_credit_id}/receive-refund", response_model=VendorCreditResponse)
async def receive_refund(
    request: Request, vendor_credit_id: UUID, body: ReceiveRefundRequest, response: Response = None
):
    """
    Record cash refund received from vendor.

    Creates journal entry:
    - Dr. Cash/Bank
    - Cr. Accounts Payable

    Vendor credit must be in 'posted' or 'partial' status.
    """
    try:
        ctx = get_user_context(request)
        if not ctx["user_id"]:
            raise HTTPException(status_code=401, detail="ID pengguna wajib ada.")

        pool = await get_pool()
        await _ensure_role_preconditions(pool, ctx["tenant_id"])

        async with pool.acquire() as conn:
            async with conn.transaction():
                # Law 14 (23 Okt 2026, putusan pemilik LANGSUNG; kembar NK CN_*): X-Idempotency-Key klien saja -- kirim ulang = respons
                # pertama (SATU tulisan), isi beda = 409; tanpa kunci = perilaku lama.
                from ..services import idem_buat as _ib
                _kp, _sd, _lama = await _ib.mulai_aksi(conn, ctx, _ib.kunci_dari(request), "VC_REFUND", vendor_credit_id,
                                                       lambda: body.model_dump(mode="json"), response)
                if _lama is not None:
                    return _lama
                # U3d: PENENTU SATU dgn POST /{id}/refund/preview (NDR.rencana_kembali; urutan + pesan == kode lama)
                _r = await NDR.rencana_kembali(conn, ctx, vendor_credit_id, body)
                vc, resolved_account_id = _r["vc"], _r["akun_id"]

                # Law 13: Advisory lock
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1))",
                    f"VENDOR_CREDIT_REFUND:{vendor_credit_id}",
                )

                import uuid as uuid_module

                refund_id = uuid_module.uuid4()
                journal_id = uuid_module.uuid4()

                # Create refund journal
                journal_number = (
                    await conn.fetchval(
                        "SELECT get_next_journal_number($1, 'RR')", ctx["tenant_id"]
                    )
                    or f"RR-{vc['credit_number']}"
                )

                # Law 27 + Fase D2.2: Resolve AP via role.
                ap_account_id = await resolve_account_id_by_role(
                    conn, ctx["tenant_id"], AccountRole.AP_TRADE
                )

                # Law 20: Create journal as DRAFT (Law 25: no float())
                await conn.execute(
                    """
                    INSERT INTO journal_entries (
                        id, tenant_id, journal_number, journal_date,
                        description, source_type, source_id,
                        status, total_debit, total_credit, created_by
                    ) VALUES ($1, $2, $3, $4, $5, 'VENDOR_CREDIT_REFUND', $6, 'DRAFT', $7, $7, $8)
                """,
                    journal_id,
                    ctx["tenant_id"],
                    journal_number,
                    body.refund_date,
                    f"Refund Received {vc['credit_number']} - {vc['vendor_name']}",
                    vendor_credit_id,
                    body.amount,
                    ctx["user_id"],
                )

                # Baris = NDR.baris_refund_nd (SATU sumber dgn pratinjau; Law 25: no float()): Dr Kas-Bank / Cr Hutang
                for _n, (_akun, _d, _k, _memo) in enumerate(NDR.baris_refund_nd(resolved_account_id, ap_account_id, body.amount, vc["credit_number"]), 1):
                    await conn.execute(
                        """
                        INSERT INTO journal_lines (
                            id, journal_id, line_number, account_id, debit, credit, memo
                        ) VALUES ($1, $2, $3, $4, $5, $6, $7)
                    """,
                        uuid_module.uuid4(),
                        journal_id,
                        _n,
                        _akun,
                        _d,
                        _k,
                        _memo,
                    )

                # Law 20: Post after all lines
                await conn.execute(
                    "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1",
                    journal_id,
                )

                # Create refund record
                await conn.execute(
                    """
                    INSERT INTO vendor_credit_refunds (
                        id, tenant_id, vendor_credit_id, amount, refund_date,
                        payment_method, account_id, bank_account_id,
                        reference, notes, journal_id, created_by
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                """,
                    refund_id,
                    ctx["tenant_id"],
                    vendor_credit_id,
                    body.amount,
                    body.refund_date,
                    body.payment_method,
                    resolved_account_id,
                    UUID(body.bank_account_id) if body.bank_account_id else None,
                    body.reference,
                    body.notes,
                    journal_id,
                    ctx["user_id"],
                )

                # U3a (banksync Rule 1 / Law 31 gerbang 4): uang masuk rekening bank -> bank_transactions di transaksi yang SAMA
                if body.bank_account_id:
                    await conn.execute(
                        """
                        INSERT INTO bank_transactions (
                            id, tenant_id, bank_account_id, transaction_date, transaction_type, amount, running_balance,
                            reference_type, reference_id, reference_number, description, journal_id, status, origin_type,
                            source_module, created_by, posted_by, posted_at
                        ) VALUES ($1, $2, $3, $4, 'deposit', $5, 0, 'vendor_credit_refund', $6, $7, $8, $9, 'POSTED', 'SYSTEM',
                                  'vendor_credit', $10, $10, NOW())
                        """,
                        uuid_module.uuid4(), ctx["tenant_id"], UUID(body.bank_account_id), body.refund_date, body.amount,
                        refund_id, vc["credit_number"], f"Pengembalian dana nota debit {vc['credit_number']}", journal_id, ctx["user_id"],
                    )

                # Status will be updated by trigger
                logger.info(
                    f"Vendor credit refund received: {vendor_credit_id}, amount={body.amount}"
                )

                return await _ib.simpan(conn, ctx, _kp, _sd, "VC_REFUND", {
                    "success": True,
                    "message": "Refund received successfully",
                    "data": {
                        "id": str(vendor_credit_id),
                        "refund_id": str(refund_id),
                        "journal_id": str(journal_id),
                        "amount": body.amount,
                    },
                }, vendor_credit_id)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Error receiving refund for vendor credit {vendor_credit_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="Failed to receive refund")


# =============================================================================
# VOID VENDOR CREDIT
# =============================================================================


@router.post("/{vendor_credit_id}/void", response_model=VendorCreditResponse)
async def void_vendor_credit(
    request: Request, vendor_credit_id: UUID, body: VoidVendorCreditRequest, response: Response = None
):
    """
    Void a vendor credit.

    Creates reversal journal entry.
    Vendor credit must have no applications or refunds.
    """
    try:
        ctx = get_user_context(request)
        if not ctx["user_id"]:
            raise HTTPException(status_code=401, detail="ID pengguna wajib ada.")

        pool = await get_pool()
        await _ensure_role_preconditions(pool, ctx["tenant_id"])

        async with pool.acquire() as conn:
            async with conn.transaction():
                # Law 14 (23 Okt 2026, putusan pemilik LANGSUNG; kembar NK CN_*): X-Idempotency-Key klien saja -- kirim ulang = respons
                # pertama (SATU tulisan), isi beda = 409; tanpa kunci = perilaku lama.
                from ..services import idem_buat as _ib
                _kp, _sd, _lama = await _ib.mulai_aksi(conn, ctx, _ib.kunci_dari(request), "VC_VOID", vendor_credit_id,
                                                       lambda: body.model_dump(mode="json"), response)
                if _lama is not None:
                    return _lama
                # Law 13: Advisory lock
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1))",
                    f"VENDOR_CREDIT_VOID:{vendor_credit_id}",
                )

                # U3d: PENENTU SATU dgn POST /{id}/void/preview (NDR.rencana_batal; urutan + pesan == kode lama)
                _r = await NDR.rencana_batal(conn, ctx, vendor_credit_id, body.reason)
                vc = _r["vc"]
                if _r["hapus"]:
                    # Just delete draft
                    await conn.execute(
                        "DELETE FROM vendor_credits WHERE id = $1", vendor_credit_id
                    )
                    return await _ib.simpan(conn, ctx, _kp, _sd, "VC_VOID", {
                        "success": True,
                        "message": "Draft vendor credit deleted",
                        "data": {"id": str(vendor_credit_id)},
                    }, vendor_credit_id)

                # Create reversal journal if original was posted
                if vc["journal_id"]:
                    import uuid as uuid_module

                    reversal_journal_id = uuid_module.uuid4()

                    hari_ini = _r["hari_ini"]  # t10-tanggal-bisnis; periode (Law 5) sudah dicek di NDR.rencana_batal

                    # Get original journal lines
                    original_lines = await conn.fetch(
                        """
                        SELECT * FROM journal_lines WHERE journal_id = $1
                    """,
                        vc["journal_id"],
                    )

                    journal_number = (
                        await conn.fetchval(
                            "SELECT get_next_journal_number($1, 'RV')", ctx["tenant_id"]
                        )
                        or f"RV-{vc['credit_number']}"
                    )

                    # Law 20: Create reversal header as DRAFT (Law 25: no float())
                    await conn.execute(
                        """
                        INSERT INTO journal_entries (
                            id, tenant_id, journal_number, journal_date,
                            description, source_type, source_id, reversal_of_id,
                            status, total_debit, total_credit, created_by
                        ) VALUES ($1, $2, $3, $9, $4, 'VENDOR_CREDIT', $5, $6, 'DRAFT', $7, $7, $8)
                    """,
                        reversal_journal_id,
                        ctx["tenant_id"],
                        journal_number,
                        f"Void {vc['credit_number']} - {vc['vendor_name']}",
                        vendor_credit_id,
                        vc["journal_id"],
                        vc["total_amount"],
                        ctx["user_id"],
                        hari_ini,  # t10-tanggal-bisnis
                    )

                    # Create reversed lines (swap debit/credit)
                    for idx, line in enumerate(original_lines, 1):
                        await conn.execute(
                            """
                            INSERT INTO journal_lines (
                                id, journal_id, line_number, account_id, debit, credit, memo
                            ) VALUES ($1, $2, $3, $4, $5, $6, $7)
                        """,
                            uuid_module.uuid4(),
                            reversal_journal_id,
                            idx,
                            line["account_id"],
                            line["credit"],  # Swap
                            line["debit"],  # Swap
                            f"Reversal - {line['memo'] or ''}",
                        )

                    # Law 20: Post reversal after all lines
                    await conn.execute(
                        "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1",
                        reversal_journal_id,
                    )

                    # Mark original journal as reversed (Law 26)
                    await conn.execute(
                        """
                        UPDATE journal_entries
                        SET reversed_by_id = $2, reversed_at = NOW()  -- Law 2: status asli TETAP POSTED (lihat backend/docs/TEMUAN-rantai-nomor-ganda-20260912.md)
                        WHERE id = $1
                    """,
                        vc["journal_id"],
                        reversal_journal_id,
                    )

                    # U3a (Law 31 gerbang 5): balik SEMUA lapisan posting -- jurnal HPP, kartu stok, baris pajak
                    await ND.balik_hpp_stok_pajak(conn, ctx, vc, await tanggal_dokumen(conn, ctx["tenant_id"]))

                # Update vendor credit status
                await conn.execute(
                    """
                    UPDATE vendor_credits
                    SET status = 'void', voided_at = NOW(),
                        voided_by = $2, voided_reason = $3, updated_at = NOW()
                    WHERE id = $1
                """,
                    vendor_credit_id,
                    ctx["user_id"],
                    body.reason,
                )

                logger.info(f"Vendor credit voided: {vendor_credit_id}")

                return await _ib.simpan(conn, ctx, _kp, _sd, "VC_VOID", {
                    "success": True,
                    "message": "Vendor credit voided successfully",
                    "data": {"id": str(vendor_credit_id), "status": "void"},
                }, vendor_credit_id)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Error voiding vendor credit {vendor_credit_id}: {e}", exc_info=True
        )
        raise HTTPException(status_code=500, detail="Failed to void vendor credit")


# =============================================================================
# PRATINJAU AKSI (U3d, 23 Okt 2026): penentu = services/nota_debit_rencana (SAMA dgn tulis). READ ONLY tanpa kunci/sequence (BUKAN pola
# tulis-lalu-rollback pratinjau NK). Bentuk == pratinjau UM vendor: {vendor_credit_id, action, can_proceed, blocks[], before, after,
# journals[] (journal_number null), bank_transactions[], bills[], stock[], payload, preview:true}. 200 walau diblok; 404 bila tak ada.
# =============================================================================


class _PratinjauAlasan(BaseModel):
    reason: Optional[str] = None


async def _keadaan_nd(conn, tid: str, vc_id) -> dict:
    """before/after: medan U3c (NDD) + sisa jurnal; exists false bila tak ada."""
    v = await conn.fetchrow("SELECT * FROM vendor_credits WHERE id = $1 AND tenant_id = $2", vc_id, tid)
    if not v:
        return {"exists": False}
    m = (await NDD.medan_banyak(conn, tid, [v])).get(v["id"], {})
    return {"exists": True, "status": v["status"], "sisa_keadaan": m.get("sisa_keadaan"), "status_text": m.get("status_text"),
            "remaining": float(await ND.sisa_nd(conn, tid, vc_id)), "terpakai": m.get("terpakai"), "dikembalikan": m.get("dikembalikan"),
            "original_bill_id": m.get("original_bill_id"), "original_bill_number": m.get("original_bill_number")}


def _sesudah(sebelum: dict, **ubah) -> dict:
    return {**sebelum, **ubah} if sebelum.get("exists") else sebelum


async def _pratinjau_nd(request: Request, vendor_credit_id: UUID, aksi: str, isi, payload) -> dict:
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        tr = conn.transaction(readonly=True)
        await tr.start()
        try:
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])
            sebelum = await _keadaan_nd(conn, ctx["tenant_id"], vendor_credit_id)
            if not sebelum["exists"]:
                raise HTTPException(status_code=404, detail="Nota debit tidak ditemukan.")
            r, sesudah = await isi(conn, ctx, sebelum)
        finally:
            await tr.rollback()  # pratinjau tak pernah menulis
    bl = r["blocks"]
    return {"success": True, "data": {
        "vendor_credit_id": str(vendor_credit_id), "action": aksi, "can_proceed": not bl.daftar, "blocks": bl.respons(),
        "before": sebelum, "after": sesudah, "journals": r.get("journals", []), "bank_transactions": r.get("bank_transactions", []),
        "bills": r.get("bills", []), "stock": r.get("stock", []), "payload": payload, "preview": True}}


@router.post("/{vendor_credit_id}/post/preview")
async def preview_post_vendor_credit(request: Request, vendor_credit_id: UUID):
    """Pratinjau POSTING: jurnal VENDOR_CREDIT (baris_posting_nd == tulis) + HPP & stok retur barang dilacak (fungsi baca record_inventory_outbound)."""
    async def isi(conn, ctx, sebelum):
        vc = await conn.fetchrow("SELECT * FROM vendor_credits WHERE id = $1 AND tenant_id = $2", vendor_credit_id, ctx["tenant_id"])
        r = await NDR.rencana_posting(conn, ctx, vc, pratinjau=True)
        bertaut = vc["original_bill_id"] is not None
        return r, _sesudah(sebelum, status="posted", sisa_keadaan="terpakai" if bertaut else "utuh",
                           remaining=0.0 if bertaut else float(vc["total_amount"]), terpakai=float(vc["total_amount"]) if bertaut else 0.0)
    return await _pratinjau_nd(request, vendor_credit_id, "post", isi, {})


@router.post("/{vendor_credit_id}/apply/preview")
async def preview_apply_vendor_credit(request: Request, vendor_credit_id: UUID, body: ApplyVendorCreditRequest):
    """Pratinjau TERAPKAN ke satu tagihan (tanpa jurnal): sisa tagihan sebelum -> sesudah."""
    async def isi(conn, ctx, sebelum):
        if len(body.applications) != 1:
            bl = NDR.Blok(True)
            bl.galat("SATU_TAGIHAN", 400, "Nota debit hanya bisa diterapkan ke satu tagihan.")
            return {"blocks": bl}, sebelum
        a = body.applications[0]
        r = await NDR.rencana_terapkan(conn, ctx["tenant_id"], vendor_credit_id, a.bill_id, a.amount, pratinjau=True)
        b = r.get("bill")
        return r, _sesudah(sebelum, sisa_keadaan="terpakai", remaining=0.0, terpakai=float(r.get("total") or 0),
                           original_bill_id=str(b["id"]) if b else None, original_bill_number=b["invoice_number"] if b else None)
    return await _pratinjau_nd(request, vendor_credit_id, "apply", isi, body.model_dump(mode="json"))


@router.post("/{vendor_credit_id}/unapply/preview")
async def preview_unapply_vendor_credit(request: Request, vendor_credit_id: UUID, body: Optional[_PratinjauAlasan] = None):
    """Pratinjau LEPAS penerapan (tanpa jurnal): sisa tagihan sebelum -> sesudah."""
    alasan = body.reason if body else None
    async def isi(conn, ctx, sebelum):
        r = await NDR.rencana_lepas(conn, ctx["tenant_id"], vendor_credit_id, alasan, pratinjau=True)
        app = r.get("app")
        jml = float(app["amount_applied"]) if app else 0.0
        return r, _sesudah(sebelum, sisa_keadaan="utuh", remaining=jml, terpakai=0.0, original_bill_id=None, original_bill_number=None)
    return await _pratinjau_nd(request, vendor_credit_id, "unapply", isi, {"reason": alasan})


async def _pratinjau_refund(request: Request, vendor_credit_id: UUID, body: ReceiveRefundRequest):
    async def isi(conn, ctx, sebelum):
        r = await NDR.rencana_kembali(conn, ctx, vendor_credit_id, body, pratinjau=True)
        sisa = (r.get("sisa") or Decimal("0")) - Decimal(str(body.amount))
        return r, _sesudah(sebelum, remaining=float(max(sisa, Decimal("0"))),
                           dikembalikan=float(Decimal(str(sebelum.get("dikembalikan") or 0)) + Decimal(str(body.amount))),
                           sisa_keadaan="dikembalikan" if sisa <= 0 else "sebagian_dikembalikan")
    return await _pratinjau_nd(request, vendor_credit_id, "refund", isi, body.model_dump(mode="json"))


@router.post("/{vendor_credit_id}/receive-refund/preview")
async def preview_receive_refund_vendor_credit(request: Request, vendor_credit_id: UUID, body: ReceiveRefundRequest):
    """Pratinjau TERIMA PENGEMBALIAN DANA (nama rute == rute tulis /receive-refund; putusan MASTER 23 Okt: tanpa alias /refund/preview):
    jurnal Dr Kas-Bank / Cr Hutang (baris_refund_nd == tulis) + cermin bank."""
    return await _pratinjau_refund(request, vendor_credit_id, body)


@router.post("/{vendor_credit_id}/void/preview")
async def preview_void_vendor_credit(request: Request, vendor_credit_id: UUID, body: Optional[_PratinjauAlasan] = None):
    """Pratinjau BATAL: cermin jurnal posting + cermin tiap jurnal HPP. Draf = akan DIHAPUS (after.exists false)."""
    alasan = body.reason if body else None
    async def isi(conn, ctx, sebelum):
        r = await NDR.rencana_batal(conn, ctx, vendor_credit_id, alasan, pratinjau=True)
        if r.get("hapus"):
            return r, {"exists": False}
        return r, _sesudah(sebelum, status="void", sisa_keadaan="batal", remaining=0.0)
    return await _pratinjau_nd(request, vendor_credit_id, "void", isi, {"reason": alasan})


@router.post("/{vendor_credit_id}/create-tax-invoice")
async def create_tax_invoice_from_vc(request: Request, vendor_credit_id: str, response: Response = None):
    """Create faktur pajak retur masukan dari vendor credit.
    P2P-3a: idem VC_TAX_INV + kunci per sumber DIPEGANG sampai panggilan internal selesai (klik ganda = berurutan, yang kedua melihat
    tautan yang sudah ada -> 400); respons tercatat untuk replay. Gagal -> transaksi batal, kunci klien tak terpakai."""
    from ..services import idem_buat as _ib
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn, conn.transaction():
        _kp, _sd, _lama = await _ib.mulai_aksi(conn, ctx, _ib.kunci_dari(request), "VC_TAX_INV", vendor_credit_id, {}, response)
        if _lama is not None:
            return _lama
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"TAX_INV_SRC:vendor_credit:{vendor_credit_id}")
        tid = ctx["tenant_id"]
        await conn.execute(f"SET LOCAL app.tenant_id = '{tid}'")
        vc = await conn.fetchrow(
            "SELECT id, status, EXISTS (SELECT 1 FROM tax_invoice_sources tis JOIN tax_invoices ti ON ti.id = tis.tax_invoice_id "
            "WHERE tis.source_type = 'vendor_credit' AND tis.source_id = vendor_credits.id AND ti.tenant_id = $2 "
            "AND ti.status NOT IN ('cancelled', 'replaced')) AS tax_invoice_id FROM vendor_credits "  # U3a: kolom hantu -> tautan nyata
            "WHERE id = $1 AND tenant_id = $2",
            vendor_credit_id,
            ctx["tenant_id"],
        )
        if not vc:
            raise HTTPException(404, "Vendor credit tidak ditemukan")
        if vc["status"] == "draft":
            raise HTTPException(400, "Vendor credit belum di-post")
        if vc["tax_invoice_id"]:
            raise HTTPException(400, "Vendor credit sudah punya faktur pajak")

        # Delegate to main tax-invoices create endpoint via internal HTTP (kunci per sumber tetap dipegang transaksi ini)
        import httpx

        auth_header = request.headers.get("authorization", "")
        async with httpx.AsyncClient(verify=False) as client:
            resp = await client.post(
                "http://localhost:8000/api/tax-invoices",
                json={"source_type": "vendor_credit", "source_ids": [vendor_credit_id]},
                headers={"Authorization": auth_header, "Content-Type": "application/json"},
                timeout=30.0,
            )
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("detail", resp.text)
            except Exception:
                detail = resp.text
            raise HTTPException(resp.status_code, detail)
        hasil = resp.json()
        return (await _ib.simpan(conn, ctx, _kp, _sd, "VC_TAX_INV", hasil, vendor_credit_id)) if _kp else hasil
