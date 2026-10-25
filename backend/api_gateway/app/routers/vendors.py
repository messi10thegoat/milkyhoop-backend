"""
Vendors Router - Vendor/Supplier Master Data Management

CRUD endpoints for managing vendors with search and pagination.
Integrates with bills module for purchase invoice management.
"""

from fastapi import APIRouter, HTTPException, Request, Query, Response
from pydantic import BaseModel
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Optional, Literal
from uuid import UUID
import json
import logging
import asyncpg

from ..utils.tanggal_tenant import tanggal_dokumen
from ..services import idem_buat  # VENDOR-IDEM (25 Okt 2026): X-Idempotency-Key buat/status/hapus/gabung
from ..schemas.vendors import (
    VendorActivity,
    VendorActivityResponse,
    CreateVendorRequest,
    UpdateVendorRequest,
    VendorResponse,
    VendorListResponse,
    VendorDetailResponse,
    VendorAutocompleteResponse,
    VendorBalanceResponse,
    VendorDuplicateCheckResponse,
    MergeVendorRequest,
    MergeVendorResponse,
)
from ..services.role_resolver import (
    AccountRole,
    resolve_account_id_by_role,
)
from ..services.role_precondition import assert_required_roles_for_path

logger = logging.getLogger(__name__)
router = APIRouter()


# Fase C1.6: required role mappings for vendors.py per-vendor
# opening-balance posting path.
#
# AP_TRADE                -- debit leg (Hutang Usaha) of the per-vendor
#                            OB journal.
# EQUITY_OPENING_BALANCE  -- credit (balancing) leg (Modal Saldo Awal).
VENDORS_REQUIRED_ROLES = [
    AccountRole.AP_TRADE,
    AccountRole.EQUITY_OPENING_BALANCE,
]

# One-time precondition flag (mirrors Fase C1.2/C1.3/C1.4/C1.5 pattern).
_precondition_checked_tenants: set = set()


async def _ensure_role_preconditions(pool, tenant_id=None) -> None:
    """Run role-mapping precondition once per process for vendors.

    Fails loud (PreconditionFailedError) if any tenant lacks any required
    role mapping. After first successful check the audit is skipped; a
    tenant added later without mapping will still fail loud at
    resolve_account_id_by_role(...) via AccountRoleUnmappedError.
    """
    if tenant_id is None:
        # legacy/global fallback (unchanged behavior)
        await assert_required_roles_for_path(pool, "vendors", VENDORS_REQUIRED_ROLES)
        return
    if tenant_id in _precondition_checked_tenants:
        return
    await assert_required_roles_for_path(
        pool, "vendors", VENDORS_REQUIRED_ROLES, tenant_id=tenant_id
    )
    _precondition_checked_tenants.add(tenant_id)


async def _resolve_role_account(conn, tenant_id: str, role_key: str) -> dict:
    """Resolve role -> full account row (id, account_code, name).

    Used by per-vendor OB posting to preserve existing response shape
    (which surfaces account_code + name for the journal_entry preview).
    Raises HTTPException(500) if the role mapping points to a missing /
    inactive CoA row (data integrity bug).
    """
    account_id = await resolve_account_id_by_role(conn, tenant_id, role_key)
    row = await conn.fetchrow(
        """
        SELECT id, account_code, name
        FROM chart_of_accounts
        WHERE tenant_id = $1 AND id = $2 AND is_active = true
        """,
        tenant_id,
        account_id,
    )
    if not row:
        raise HTTPException(
            status_code=500,
            detail=(
                f"Role {role_key!r} for tenant {tenant_id!r} points to "
                f"account_id={account_id} which is missing or inactive "
                f"in chart_of_accounts. Fix account_roles mapping."
            ),
        )
    return dict(row)


# Connection pool (initialized on first request)


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


# =============================================================================
# HEALTH CHECK (must be before /{vendor_id} to avoid route conflict)
# =============================================================================
@router.get("/health")
async def health_check():
    """Health check endpoint for the vendors service."""
    return {"status": "ok", "service": "vendors"}


# =============================================================================
# AUTOCOMPLETE (for quick search in forms)
# =============================================================================
@router.get("/autocomplete", response_model=VendorAutocompleteResponse)
async def autocomplete_vendors(
    request: Request,
    q: str = Query(..., min_length=1, description="Search query"),
    limit: int = Query(10, ge=1, le=50),
):
    """
    Quick vendor search for autocomplete in forms.
    Returns minimal data for fast response.
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Multi-word AND splitting (same pattern as items search)
            words = q.strip().split()
            params: list = [ctx["tenant_id"]]
            word_clauses = []
            for word in words:
                idx = len(params) + 1
                word_clauses.append(
                    f"(name ILIKE ${idx} OR code ILIKE ${idx} OR company_name ILIKE ${idx} OR display_name ILIKE ${idx} OR phone ILIKE ${idx})"
                )
                params.append(f"%{word}%")
            where_search = " AND ".join(word_clauses) if word_clauses else "TRUE"
            limit_idx = len(params) + 1
            params.append(limit)

            query = f"""
                SELECT id, name, code, phone, company_name, display_name
                FROM vendors
                WHERE tenant_id = $1
                  AND is_active = true
                  AND {where_search}
                ORDER BY name ASC
                LIMIT ${limit_idx}
            """
            rows = await conn.fetch(query, *params)

            # Fuzzy fallback: if ILIKE returns nothing, try trigram similarity
            if not rows:
                fuzzy_query = """
                    SELECT id, name, code, phone, company_name, display_name,
                           similarity(name, $2) AS sim
                    FROM vendors
                    WHERE tenant_id = $1
                      AND is_active = true
                      AND similarity(name, $2) > 0.25
                    ORDER BY sim DESC
                    LIMIT $3
                """
                rows = await conn.fetch(fuzzy_query, ctx["tenant_id"], q, limit)

            items = [
                {
                    "id": str(row["id"]),
                    "name": row["name"],
                    "company_name": row["company_name"],
                    "display_name": row["display_name"],
                    "code": row["code"],
                    "phone": row["phone"],
                }
                for row in rows
            ]

            return {"items": items}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in vendor autocomplete: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Autocomplete failed")


# =============================================================================
# DUPLICATE CHECK (for form validation)
# =============================================================================
@router.get("/check-duplicate", response_model=VendorDuplicateCheckResponse)
async def check_duplicate(
    request: Request,
    nama: Optional[str] = Query(None, description="Vendor name to check"),
    npwp: Optional[str] = Query(None, description="NPWP to check"),
    excludeId: Optional[str] = Query(
        None, description="Vendor ID to exclude (for edit mode)"
    ),
):
    """
    Check for potential duplicate vendors by name or NPWP.

    Used for form validation before creating/updating vendors.

    **Returns:**
    - `byName`: Vendors with similar names (case-insensitive, partial match)
    - `byNpwp`: Vendors with exact NPWP match
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        by_name = []
        by_npwp = []

        async with pool.acquire() as conn:
            # Check by name (partial match, case-insensitive)
            if nama and nama.strip():
                name_query = """
                    SELECT id, name, company_name, tax_id
                    FROM vendors
                    WHERE tenant_id = $1
                      AND is_active = true
                      AND name ILIKE $2
                """
                params = [ctx["tenant_id"], f"%{nama.strip()}%"]

                if excludeId:
                    name_query += " AND id != $3"
                    params.append(UUID(excludeId))

                name_query += " LIMIT 10"

                rows = await conn.fetch(name_query, *params)
                by_name = [
                    {
                        "id": str(row["id"]),
                        "name": row["name"],
                        "company_name": row["company_name"],
                        "display_name": row["display_name"],
                        "company": row["company_name"],
                        "npwp": row["tax_id"],
                    }
                    for row in rows
                ]

            # Check by NPWP (exact match after normalization)
            if npwp and npwp.strip():
                # Normalize NPWP: remove dots and dashes
                normalized_npwp = npwp.replace(".", "").replace("-", "").strip()

                npwp_query = """
                    SELECT id, name, company_name, tax_id
                    FROM vendors
                    WHERE tenant_id = $1
                      AND is_active = true
                      AND REPLACE(REPLACE(tax_id, '.', ''), '-', '') = $2
                """
                params = [ctx["tenant_id"], normalized_npwp]

                if excludeId:
                    npwp_query += " AND id != $3"
                    params.append(UUID(excludeId))

                npwp_query += " LIMIT 10"

                rows = await conn.fetch(npwp_query, *params)
                by_npwp = [
                    {
                        "id": str(row["id"]),
                        "name": row["name"],
                        "company_name": row["company_name"],
                        "display_name": row["display_name"],
                        "company": row["company_name"],
                        "npwp": row["tax_id"],
                    }
                    for row in rows
                ]

        return {"byName": by_name, "byNpwp": by_npwp}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error checking vendor duplicates: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Duplicate check failed")


# =============================================================================
# LIST VENDORS
# =============================================================================
@router.get("", response_model=VendorListResponse)
async def list_vendors(
    request: Request,
    skip: int = Query(0, ge=0, description="Offset for pagination"),
    limit: int = Query(20, ge=1, le=100, description="Items per page"),
    search: Optional[str] = Query(None, description="Search name, code, or contact"),
    is_active: Optional[bool] = Query(
        None,
        description="Filter by active status (None=all, True=active, False=inactive)",
    ),
    has_balance: Optional[bool] = Query(
        None, description="Filter vendors with ap_balance > 0"
    ),
    is_pkp: Optional[bool] = Query(None, description="Filter PKP vendors"),
    has_overdue: Optional[bool] = Query(
        None, description="Filter vendors with overdue invoices"
    ),
    sort_by: Literal[
        "name", "code", "created_at", "updated_at", "ap_balance", "company_name"
    ] = Query("created_at", description="Sort field"),
    sort_order: Literal["asc", "desc"] = Query("desc", description="Sort order"),
):
    """
    List vendors with search, filtering, and pagination.

    **Search:** Matches name, code, contact_person, or phone.
    **Filter:** Use is_active=true to show only active vendors.
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Build query conditions.
            # SEMUA kueri di bawah memakai alias tabel `v` (FROM vendors v),
            # jadi setiap kondisi ditulis EKSPLISIT ber-prefiks v. — dulu
            # WHERE dialias lewat str.replace("tenant_id", "v.tenant_id")
            # yang juga merusak fragmen has_overdue (vendors.tenant_id ->
            # vendors.v.tenant_id) -> 500 saat has_overdue+sort ap_balance.
            conditions = ["v.tenant_id = $1"]
            params = [ctx["tenant_id"]]
            param_idx = 2

            if search:
                # Split search into words for fuzzy matching
                words = search.strip().split()
                if len(words) == 1:
                    conditions.append(
                        f"(v.name ILIKE ${param_idx} OR v.code ILIKE ${param_idx} "
                        f"OR v.company_name ILIKE ${param_idx} OR v.display_name ILIKE ${param_idx} "
                        f"OR v.contact_person ILIKE ${param_idx} OR v.phone ILIKE ${param_idx} OR v.search_text ILIKE ${param_idx})"
                    )
                    params.append(f"%{words[0]}%")
                    param_idx += 1
                else:
                    word_conds = []
                    for word in words:
                        word_conds.append(
                            f"(v.name ILIKE ${param_idx} OR v.code ILIKE ${param_idx} "
                            f"OR v.company_name ILIKE ${param_idx} OR v.display_name ILIKE ${param_idx} "
                            f"OR v.contact_person ILIKE ${param_idx} OR v.phone ILIKE ${param_idx} OR v.search_text ILIKE ${param_idx})"
                        )
                        params.append(f"%{word}%")
                        param_idx += 1
                    conditions.append(f"({' AND '.join(word_conds)})")

            if is_active is not None:
                conditions.append(f"v.is_active = ${param_idx}")
                params.append(is_active)
                param_idx += 1

            # Filter by has_balance (vendors with opening_balance > 0)
            if has_balance is True:
                conditions.append("COALESCE(v.opening_balance, 0) > 0")

            # Filter by is_pkp (PKP vendors only)
            if is_pkp is True:
                conditions.append("v.is_pkp = true")

            # Pure Ledger: has_overdue filter using compute_ap_outstanding() DB function
            if has_overdue is True:
                # #10b-3a: hari ini = tanggal bisnis tenant, bukan UTC
                hari_ini = await tanggal_dokumen(conn, ctx["tenant_id"])
                conditions.append(
                    f"""EXISTS (
                    SELECT 1 FROM compute_ap_outstanding(v.tenant_id) ap_fn
                    WHERE ap_fn.vendor_id = v.id
                      AND ap_fn.due_date < ${param_idx}::date
                      AND ap_fn.outstanding > 0 AND ap_fn.bill_id IS NOT NULL  -- V457: lewat tempo = tagihan
                )"""
                )
                params.append(hari_ini)
                param_idx += 1

            where_clause = " AND ".join(conditions)

            # Validate sort field
            valid_sorts = {
                "name": "name",
                "code": "code",
                "created_at": "created_at",
                "updated_at": "updated_at",
                "company_name": "company_name",
            }
            sort_field = valid_sorts.get(sort_by, "name")
            sort_dir = "DESC" if sort_order == "desc" else "ASC"
            use_ap_balance_sort = sort_by == "ap_balance"

            # Get total count
            count_query = f"SELECT COUNT(*) FROM vendors v WHERE {where_clause}"
            total = await conn.fetchval(count_query, *params)

            # Get items - handle ap_balance sorting with subquery
            if use_ap_balance_sort:
                query = f"""
                    SELECT v.id, v.code, v.name, v.company_name, v.display_name, v.contact_person, v.phone, v.email,
                           v.payment_terms_days, v.is_active, v.created_at,
                           COALESCE(ap.ap_balance, 0) as ap_balance
                    FROM vendors v
                    LEFT JOIN (
                        -- Pure Ledger: AP balance from compute_ap_outstanding() DB function
                        SELECT vendor_id, COALESCE(SUM(outstanding), 0) as ap_balance
                        FROM compute_ap_outstanding($1)
                        GROUP BY vendor_id
                    ) ap ON ap.vendor_id = v.id
                    WHERE {where_clause}
                    ORDER BY COALESCE(ap.ap_balance, 0) {sort_dir}
                    LIMIT ${param_idx} OFFSET ${param_idx + 1}
                """
            else:
                query = f"""
                    SELECT id, code, name, company_name, display_name, contact_person, phone, email,
                           payment_terms_days, is_active, created_at
                    FROM vendors v
                    WHERE {where_clause}
                    ORDER BY {sort_field} {sort_dir}
                    LIMIT ${param_idx} OFFSET ${param_idx + 1}
                """
            params.extend([limit, skip])

            rows = await conn.fetch(query, *params)

            # Pure Ledger: batch-fetch AP balances from compute_ap_outstanding() DB function
            vendor_ids = [str(row["id"]) for row in rows]
            ap_balances = {}
            if vendor_ids:
                ap_rows = await conn.fetch(
                    """
                    SELECT vendor_id::text as vid,
                           COALESCE(SUM(outstanding), 0) as ap_balance
                    FROM compute_ap_outstanding($1)
                    WHERE vendor_id::text = ANY($2)
                    GROUP BY vendor_id
                """,
                    ctx["tenant_id"],
                    vendor_ids,
                )
                ap_balances = {row["vid"]: float(row["ap_balance"]) for row in ap_rows}

            items = [
                {
                    "id": str(row["id"]),
                    "code": row["code"],
                    "name": row["name"],
                    "company_name": row["company_name"],
                    "display_name": row["display_name"],
                    "contact_person": row["contact_person"],
                    "phone": row["phone"],
                    "email": row["email"],
                    "payment_terms_days": row["payment_terms_days"],
                    "is_active": row["is_active"],
                    "created_at": row["created_at"].isoformat(),
                    "ap_balance": ap_balances.get(str(row["id"]), 0),
                }
                for row in rows
            ]

            return {"items": items, "total": total, "has_more": (skip + limit) < total}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing vendors: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list vendors")


# =============================================================================
# GET VENDOR DETAIL
# =============================================================================
@router.get("/next-code")
async def get_next_vendor_code(request: Request):
    """Get the next available vendor code for auto-generation."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT code FROM vendors WHERE tenant_id = $1 AND code IS NOT NULL ORDER BY created_at DESC LIMIT 1",
                ctx["tenant_id"],
            )
            import re

            if row and row["code"]:
                match = re.match(r"^([A-Z]*)([0-9]+)$", row["code"])
                if match:
                    prefix = match.group(1) or "V"
                    num = int(match.group(2)) + 1
                    next_code = f"{prefix}{num:04d}"
                else:
                    next_code = "V0001"
            else:
                next_code = "V0001"
            return {"success": True, "next_code": next_code}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting next vendor code: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get next code")


def _alamat_terstruktur(jalan, kota, provinsi, kodepos):
    """Bentuk objek yang dibaca FE (useVendorForm: v.tax_address.street ...). Semua kosong -> None."""
    if not any((jalan, kota, provinsi, kodepos)):
        return None
    return {"street": jalan, "city": kota, "province": provinsi, "postal_code": kodepos}


async def _baca_anak_pemasok(conn, tenant_id: str, vendor_id):
    """Alamat pengiriman (vendor_addresses 'shipping') + kontak (vendor_contacts). Filter tenant eksplisit (BYPASSRLS)."""
    a = await conn.fetchrow(
        """SELECT street, city, province, postal_code FROM vendor_addresses
           WHERE vendor_id = $1 AND tenant_id = $2 AND address_type = 'shipping'
           ORDER BY is_default DESC NULLS LAST, created_at LIMIT 1""",
        vendor_id, tenant_id)
    kontak = await conn.fetch(
        """SELECT id, name, position, email, phone, mobile, is_primary FROM vendor_contacts
           WHERE vendor_id = $1 AND tenant_id = $2 ORDER BY is_primary DESC NULLS LAST, created_at, id""",
        vendor_id, tenant_id)
    return (
        _alamat_terstruktur(a["street"], a["city"], a["province"], a["postal_code"]) if a else None,
        [{"id": str(k["id"]), "name": k["name"], "position": k["position"] or "", "email": k["email"] or "",
          "phone": k["phone"] or "", "mobile": k["mobile"] or "", "is_primary": bool(k["is_primary"])} for k in kontak],
    )


async def _tulis_anak_pemasok(conn, tenant_id: str, vendor_id, shipping, contacts, sent: set, replace: bool = False) -> list:
    """Tulis anak dalam transaksi pemanggil. HANYA kunci yang DIKIRIM (sent) disentuh; null = tak ada perubahan;
    alamat kosong = hapus baris shipping; contacts: id = ubah, tanpa id = tambah (persis sama = dilewati), baris kosong dilewati,
    kontak yang tak ada di daftar dihapus HANYA bila replace=True (penanda eksplisit contacts_replace). Mengembalikan yang berubah."""
    berubah = []
    if "shipping_address" in sent and shipping is not None:
        await conn.execute(
            "DELETE FROM vendor_addresses WHERE vendor_id = $1 AND tenant_id = $2 AND address_type = 'shipping'",
            vendor_id, tenant_id)
        if not shipping.kosong():
            await conn.execute(
                """INSERT INTO vendor_addresses (tenant_id, vendor_id, address_type, street, city, province, postal_code, is_default)
                   VALUES ($1, $2, 'shipping', $3, $4, $5, $6, true)""",
                tenant_id, vendor_id, shipping.street, shipping.city, shipping.province, shipping.postal_code)
        berubah.append("shipping_address")
    if "contacts" in sent and contacts is not None:
        ada = {
            r["id"]: r for r in await conn.fetch(
                """SELECT id, name, position, email, phone, mobile FROM vendor_contacts
                   WHERE vendor_id = $1 AND tenant_id = $2""", vendor_id, tenant_id)
        }
        dipertahankan = []
        for k in contacts:
            if k.kosong():
                continue
            if k.id is not None:
                if k.id not in ada:
                    raise HTTPException(status_code=422, detail=f"Kontak {k.id} bukan milik pemasok ini")
                await conn.execute(
                    """UPDATE vendor_contacts SET name = $1, position = $2, email = $3, phone = $4, mobile = $5, is_primary = $6
                       WHERE id = $7 AND vendor_id = $8 AND tenant_id = $9""",
                    k.name, k.position, k.email, k.phone, k.mobile, k.is_primary, k.id, vendor_id, tenant_id)
                dipertahankan.append(k.id)
                continue
            sama = next((i for i, r in ada.items() if (r["name"], r["position"], r["email"], r["phone"], r["mobile"])
                         == (k.name, k.position, k.email, k.phone, k.mobile)), None)
            if sama is not None:
                dipertahankan.append(sama)  # persis sama = bukan kontak baru (hindari ganda)
                continue
            baru = await conn.fetchval(
                """INSERT INTO vendor_contacts (tenant_id, vendor_id, name, position, email, phone, mobile, is_primary, created_at)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, clock_timestamp()) RETURNING id""",
                tenant_id, vendor_id, k.name, k.position, k.email, k.phone, k.mobile, k.is_primary)
            dipertahankan.append(baru)
        if replace:
            await conn.execute(
                """DELETE FROM vendor_contacts WHERE vendor_id = $1 AND tenant_id = $2 AND id <> ALL($3::uuid[])""",
                vendor_id, tenant_id, dipertahankan)
        berubah.append("contacts")
    return berubah


@router.get("/{vendor_id}", response_model=VendorDetailResponse)
async def get_vendor(request: Request, vendor_id: UUID):
    """Get detailed information for a single vendor."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Query only columns that exist in the vendors table
            query = """
                SELECT id, code, name, company_name, display_name, contact_person, phone, email,
                       address, city, province, postal_code, tax_id,
                       payment_terms_days, credit_limit, notes,
                       vendor_type, nik, is_pkp, nitku, negara, account_number,
                       tax_address, tax_city, tax_province, tax_postal_code,
                       default_tax_code, default_pph_type, default_pph_rate,
                       company_name, display_name, mobile_phone, website,
                       currency, opening_balance, opening_balance_date,
                       bank_name, bank_account_number, bank_account_holder,
                       is_active, created_at, updated_at
                FROM vendors
                WHERE id = $1 AND tenant_id = $2
            """
            row = await conn.fetchrow(query, vendor_id, ctx["tenant_id"])

            if not row:
                raise HTTPException(status_code=404, detail="Vendor not found")

            # Check if vendor has any transactions
            tx_count = await conn.fetchval(
                """
                SELECT COUNT(*) FROM (
                    SELECT 1 FROM bills WHERE vendor_id = $1 AND tenant_id = $2
                    UNION ALL
                    SELECT 1 FROM purchase_orders WHERE vendor_id = $1 AND tenant_id = $2
                    UNION ALL
                    SELECT 1 FROM expenses WHERE vendor_id = $1 AND tenant_id = $2
                    UNION ALL
                    SELECT 1 FROM bill_payments_v2 WHERE vendor_id = $1 AND tenant_id = $2
                    LIMIT 1
                ) t
            """,
                vendor_id,
                ctx["tenant_id"],
            )
            has_transactions = tx_count > 0

            # Pure Ledger: AP balance from compute_vendor_ap() DB function
            ap_balance_val = await conn.fetchval(
                """
                SELECT COALESCE(SUM(outstanding), 0)
                FROM compute_vendor_ap($1, $2)
            """,
                ctx["tenant_id"],
                vendor_id,
            )

            shipping_address, contacts = await _baca_anak_pemasok(conn, ctx["tenant_id"], vendor_id)

            return {
                "success": True,
                "data": {
                    "id": str(row["id"]),
                    "code": row["code"],
                    "name": row["name"],
                    "company_name": row["company_name"],
                    "display_name": row["display_name"],
                    "contact_person": row["contact_person"],
                    "phone": row["phone"],
                    "email": row["email"],
                    "address": row["address"],
                    "city": row["city"],
                    "province": row["province"],
                    "postal_code": row["postal_code"],
                    "tax_id": row["tax_id"],
                    "payment_terms_days": row["payment_terms_days"],
                    "credit_limit": row["credit_limit"],
                    "notes": row["notes"],
                    # Extended fields from actual table
                    "vendor_type": row["vendor_type"],
                    "nik": row["nik"],
                    "is_pkp": row["is_pkp"],
                    "default_tax_code": row["default_tax_code"],
                    "default_pph_type": row["default_pph_type"],
                    "default_pph_rate": float(row["default_pph_rate"])
                    if row["default_pph_rate"]
                    else None,
                    "mobile_phone": row["mobile_phone"],
                    "mobile": row["mobile_phone"],
                    "nitku": row["nitku"],
                    "negara": row["negara"],
                    "account_number": row["account_number"],
                    "tax_address": _alamat_terstruktur(
                        row["tax_address"], row["tax_city"], row["tax_province"], row["tax_postal_code"]),
                    "shipping_address": shipping_address,
                    "contacts": contacts,
                    "website": row["website"],
                    "currency": row["currency"],
                    "opening_balance": row["opening_balance"],
                    "opening_balance_date": row["opening_balance_date"].isoformat()
                    if row["opening_balance_date"]
                    else None,
                    # Bank details
                    "bank_name": row["bank_name"],
                    "bank_account_number": row["bank_account_number"],
                    "bank_account_holder": row["bank_account_holder"],
                    # Status and timestamps
                    "is_active": row["is_active"],
                    "created_at": row["created_at"].isoformat(),
                    "updated_at": row["updated_at"].isoformat(),
                    # Transaction check
                    "has_transactions": has_transactions,
                    "ap_balance": float(ap_balance_val) if ap_balance_val else 0,
                },
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting vendor {vendor_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get vendor")


# =============================================================================
# GET VENDOR BALANCE (AP Balance)
# =============================================================================
@router.get("/{vendor_id}/balance", response_model=VendorBalanceResponse)
async def get_vendor_balance(request: Request, vendor_id: UUID):
    """
    Get vendor's accounts payable balance.

    Returns the total outstanding balance from unpaid bills, plus summary stats.

    **Returns:**
    - `total_balance`: Outstanding amount (unpaid + partial bills)
    - `unpaid_bills`: Count of bills with no payment
    - `partial_bills`: Count of partially paid bills
    - `overdue_bills`: Count of overdue bills
    - `overdue_amount`: Total overdue amount
    - `total_billed`: Total amount billed (historical)
    - `total_paid`: Total amount paid (historical)
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Check if vendor exists
            vendor = await conn.fetchrow(
                "SELECT id, name FROM vendors WHERE id = $1 AND tenant_id = $2",
                vendor_id,
                ctx["tenant_id"],
            )
            if not vendor:
                raise HTTPException(status_code=404, detail="Vendor not found")

            # Pure Ledger: AP balance from compute_vendor_ap() DB function
            balance_query = """
                SELECT
                    COALESCE(SUM(outstanding), 0) as total_balance,
                    -- V457: hitungan per TAGIHAN (bill_id ada); total_balance tetap SEMUA baris (= saldo vendor = GL)
                    COUNT(*) FILTER (WHERE paid_amount = 0 AND outstanding > 0 AND bill_id IS NOT NULL) as unpaid_count,
                    COUNT(*) FILTER (WHERE paid_amount > 0 AND outstanding > 0 AND bill_id IS NOT NULL) as partial_count,
                    COUNT(*) FILTER (WHERE due_date < $3::date AND outstanding > 0 AND bill_id IS NOT NULL) as overdue_count,
                    COALESCE(SUM(CASE WHEN due_date < $3::date AND outstanding > 0 AND bill_id IS NOT NULL THEN outstanding ELSE 0 END), 0) as overdue_amount,
                    COALESCE(SUM(bill_total), 0) as total_billed,
                    COALESCE(SUM(paid_amount), 0) as total_paid
                FROM compute_vendor_ap($1, $2)
            """
            # #10b-3a: hari ini = tanggal bisnis tenant, bukan UTC
            hari_ini = await tanggal_dokumen(conn, ctx["tenant_id"])
            balance = await conn.fetchrow(
                balance_query, ctx["tenant_id"], vendor_id, hari_ini
            )

            return {
                "success": True,
                "data": {
                    "vendor_id": str(vendor_id),
                    "vendor_name": vendor["name"],
                    "total_balance": int(balance["total_balance"] or 0),
                    "unpaid_bills": balance["unpaid_count"] or 0,
                    "partial_bills": balance["partial_count"] or 0,
                    "overdue_bills": balance["overdue_count"] or 0,
                    "overdue_amount": int(balance["overdue_amount"] or 0),
                    "total_billed": int(balance["total_billed"] or 0),
                    "total_paid": int(balance["total_paid"] or 0),
                },
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting vendor balance {vendor_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get vendor balance")


# =============================================================================
# CREATE VENDOR
# =============================================================================
async def _kunci_vendor(conn, tenant_id: str, *vendor_ids, saldo_awal: bool = False) -> None:
    """VENDOR-IDEM (25 Okt 2026, Law 13): kunci per vendor VENDOR:{tenant}:{id} (URUT bila lebih dari satu). saldo_awal=True -> ikut kunci
    VENDOR_OPENING:{id} (kunci yang SAMA dengan POST /{id}/opening-balance) SESUDAH semua kunci VENDOR, supaya hapus tak berpacu dengan
    pembuatan tagihan pembuka."""
    ids = sorted({str(v) for v in vendor_ids})
    for v in ids:
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"VENDOR:{tenant_id}:{v}")
    if saldo_awal:
        for v in ids:
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"VENDOR_OPENING:{v}")


@router.post("", response_model=VendorResponse, status_code=201)
async def create_vendor(request: Request, body: CreateVendorRequest, response: Response = None):
    """
    Create a new vendor.

    VENDOR-IDEM (25 Okt 2026): idem VENDOR_CREATE (X-Idempotency-Key opsional; ulang ber-kunci = jawaban pertama, bukan 400 "sudah ada")
    + kunci nama (dan kode) per tenant SEBELUM cek ganda -- dua klik bersamaan dengan nama sama tak lagi sama-sama lolos cek.

    **Constraints:**
    - Vendor name must be unique within tenant
    - Vendor code (if provided) must be unique within tenant

    **Extended fields (QB/Xero/Zoho aligned):**
    - company_name, display_name: Legal and display names
    - mobile_phone, website: Additional contact info
    - is_pkp, currency: Tax and currency settings
    - bank_name, bank_account_number, bank_account_holder: Payment details
    """
    from datetime import datetime, date

    def parse_date(date_str):
        """Parse date string to date object."""
        if not date_str:
            return None
        if isinstance(date_str, date):
            return date_str
        try:
            return datetime.strptime(date_str, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return None

    try:
        ctx = get_user_context(request)
        # Audit F1 (10 Okt 2026, Law 1/16): hutang awal HANYA lewat POST /vendors/{id}/opening-balance (jurnal Dr Hutang /
        # Cr Modal Saldo Awal). Angka di kolom tanpa jurnal = angka tak berbuku.
        if (getattr(body, "opening_balance", 0) or 0) > 0:
            raise HTTPException(
                status_code=422,
                detail="Saldo awal hutang tidak bisa diisi saat membuat vendor. "
                       "Simpan vendor dulu, lalu isi lewat Saldo awal di halaman vendor.",
            )
        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                kp, sidik, lama = await idem_buat.mulai(conn, ctx, idem_buat.kunci_dari(request), "VENDOR_CREATE", body, "vendors", "vendor",
                                                        response)
                if lama is not None:
                    return lama
                # VENDOR-IDEM: kunci nama/kode SEBELUM cek ganda (cek tanpa kunci = dua permintaan bersamaan sama-sama lolos)
                await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"VENDOR_NAMA:{ctx['tenant_id']}:{body.name.strip().lower()}")
                if body.code:
                    await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"VENDOR_KODE:{ctx['tenant_id']}:{body.code.strip().lower()}")
                # Check for duplicate name
                existing = await conn.fetchval(
                    "SELECT id FROM vendors WHERE tenant_id = $1 AND name = $2",
                    ctx["tenant_id"],
                    body.name,
                )
                if existing:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Vendor with name '{body.name}' already exists",
                    )

                # Check for duplicate code if provided
                if body.code:
                    existing_code = await conn.fetchval(
                        "SELECT id FROM vendors WHERE tenant_id = $1 AND code = $2",
                        ctx["tenant_id"],
                        body.code,
                    )
                    if existing_code:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Vendor with code '{body.code}' already exists",
                        )

                # Parse opening_balance_date from string to date
                opening_date = parse_date(getattr(body, "opening_balance_date", None))

                # Insert vendor with ALL fields including extended columns
                vendor_id = await conn.fetchval(
                    """
                INSERT INTO vendors (
                    tenant_id, code, name, contact_person, phone, email,
                    address, city, province, postal_code, tax_id,
                    payment_terms_days, credit_limit, notes,
                    vendor_type, opening_balance, opening_balance_date,
                    company_name, display_name, mobile_phone, website,
                    is_pkp, currency,
                    bank_name, bank_account_number, bank_account_holder,
                    created_by,
                    nik, account_number, tax_address, tax_city, tax_province, tax_postal_code,
                    nitku, negara, default_tax_code, default_pph_type, default_pph_rate
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14,
                    $15, $16, $17, $18, $19, $20, $21, $22, $23, $24, $25, $26, $27,
                    $28, $29, $30, $31, $32, $33, $34, $35, $36, $37, COALESCE($38::numeric, 0)
                )
                RETURNING id
                """,
                    ctx["tenant_id"],
                    body.code,
                    body.name,
                    getattr(body, "contact_person", None),
                    body.phone,
                    body.email,
                    getattr(body, "address", None),
                    getattr(body, "city", None),
                    getattr(body, "province", None),
                    getattr(body, "postal_code", None),
                    getattr(body, "tax_id", None),
                    getattr(body, "payment_terms_days", 30),
                    getattr(body, "credit_limit", None),
                    getattr(body, "notes", None),
                    getattr(body, "vendor_type", "BADAN"),
                    getattr(body, "opening_balance", 0),
                    opening_date,
                    getattr(body, "company_name", None),
                    getattr(body, "display_name", None),
                    getattr(body, "mobile_phone", None),
                    getattr(body, "website", None),
                    getattr(body, "is_pkp", False),
                    getattr(body, "currency", "IDR"),
                    getattr(body, "bank_name", None),
                    getattr(body, "bank_account_number", None),
                    getattr(body, "bank_account_holder", None),
                    ctx["user_id"],
                    body.nik,
                    body.account_number,
                    body.tax_address,
                    getattr(body, "tax_city", None),
                    getattr(body, "tax_province", None),
                    getattr(body, "tax_postal_code", None),
                    body.nitku,
                    body.negara,
                    body.default_tax_code,
                    body.default_pph_type,
                    body.default_pph_rate,
                )
                await _tulis_anak_pemasok(
                    conn, ctx["tenant_id"], vendor_id, body.shipping_address, body.contacts, body.model_fields_set, body.contacts_replace)

                logger.info(f"Vendor created: {vendor_id}, name={body.name}")

                # Log activity: Vendor created
                user_id = ctx.get("user_id")
                user = request.state.user
                user_name = (
                    user.get("fullname")
                    or user.get("username")
                    or user.get("email")
                    or "System"
                )
                await conn.execute(
                    """
                INSERT INTO vendor_activities (vendor_id, tenant_id, type, description, actor_id, actor_name)
                VALUES ($1, $2, 'created', 'Vendor dibuat', $3, $4)
                """,
                    vendor_id,
                    ctx["tenant_id"],
                    user_id,
                    user_name,
                )

                resp = {
                    "success": True,
                    "message": "Vendor created successfully",
                    "data": {
                        "id": str(vendor_id),
                        "name": body.name,
                        "code": body.code,
                    },
                }
                return await idem_buat.simpan(conn, ctx, kp, sidik, "VENDOR_CREATE", resp, vendor_id) if kp else resp

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating vendor: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create vendor")


# =============================================================================
# UPDATE VENDOR
# =============================================================================
@router.patch("/{vendor_id}", response_model=VendorResponse)
async def update_vendor(request: Request, vendor_id: UUID, body: UpdateVendorRequest):
    """
    Update an existing vendor.

    Only provided fields will be updated (partial update).
    Activity logging tracks all field changes.
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        # Optimistic concurrency (opt-in If-Match): reject a stale write.
        from ..services.optimistic_concurrency import assert_if_match_row
        await assert_if_match_row(request, "vendors", vendor_id)

        async with pool.acquire() as conn:
            async with conn.transaction():
                # Fetch existing vendor with all fields for change tracking
                existing = await conn.fetchrow(
                    """SELECT id, name, code, contact_person, phone, email,
                          address, city, province, postal_code, tax_id,
                          payment_terms_days, credit_limit, notes,
                          vendor_type, company_name, display_name,
                          mobile_phone, website, is_pkp, currency,
                          bank_name, bank_account_number, bank_account_holder,
                          is_active, nik, account_number, tax_address, tax_city,
                          tax_province, tax_postal_code, nitku, negara,
                          default_tax_code, default_pph_type, default_pph_rate
                   FROM vendors WHERE id = $1 AND tenant_id = $2""",
                    vendor_id,
                    ctx["tenant_id"],
                )
                if not existing:
                    raise HTTPException(status_code=404, detail="Vendor not found")

                # Check for duplicate name if name is being changed
                if body.name and body.name != existing["name"]:
                    duplicate = await conn.fetchval(
                        "SELECT id FROM vendors WHERE tenant_id = $1 AND name = $2 AND id != $3",
                        ctx["tenant_id"],
                        body.name,
                        vendor_id,
                    )
                    if duplicate:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Vendor with name '{body.name}' already exists",
                        )

                # Build update query dynamically
                update_data = body.model_dump(exclude_unset=True)
                # Anak (alamat pengiriman, kontak) bukan kolom vendors: ditulis terpisah di transaksi yang sama.
                anak_dikirim = {k for k in ("shipping_address", "contacts") if k in update_data}
                for k in ("shipping_address", "contacts", "contacts_replace"):
                    update_data.pop(k, None)
                if update_data.get("nik"):
                    tipe_efektif = update_data.get("vendor_type") or existing["vendor_type"]
                    if tipe_efektif != "ORANG_PRIBADI":
                        raise HTTPException(status_code=422, detail="NIK hanya untuk pemasok Orang Pribadi")
                if not update_data and not anak_dikirim:
                    return {
                        "success": True,
                        "message": "No changes provided",
                        "data": {"id": str(vendor_id)},
                    }

                updates = []
                params = []
                param_idx = 1

                for field, value in update_data.items():
                    updates.append(f"{field} = ${param_idx}")
                    params.append(value)
                    param_idx += 1

                updates.append("updated_at = NOW()")
                params.extend([vendor_id, ctx["tenant_id"]])

                query = f"""
                UPDATE vendors
                SET {', '.join(updates)}
                WHERE id = ${param_idx} AND tenant_id = ${param_idx + 1}
            """
                await conn.execute(query, *params)
                anak_berubah = await _tulis_anak_pemasok(
                    conn, ctx["tenant_id"], vendor_id, body.shipping_address, body.contacts, anak_dikirim, body.contacts_replace)

                logger.info(f"Vendor updated: {vendor_id}")

                # Log activity: Track changes for each updated field
                user_id = ctx.get("user_id")
                user = request.state.user
                user_name = (
                    user.get("fullname")
                    or user.get("username")
                    or user.get("email")
                    or "System"
                )

                field_labels = {
                    "name": "Nama",
                    "code": "Kode",
                    "contact_person": "Kontak",
                    "phone": "Telepon",
                    "email": "Email",
                    "address": "Alamat",
                    "city": "Kota",
                    "province": "Provinsi",
                    "postal_code": "Kode Pos",
                    "tax_id": "NPWP",
                    "payment_terms_days": "Termin Pembayaran",
                    "credit_limit": "Limit Kredit",
                    "notes": "Catatan",
                    "vendor_type": "Tipe Vendor",
                    "company_name": "Nama Perusahaan",
                    "display_name": "Nama Tampilan",
                    "mobile_phone": "HP",
                    "website": "Website",
                    "is_pkp": "PKP",
                    "currency": "Mata Uang",
                    "bank_name": "Bank",
                    "bank_account_number": "No Rekening",
                    "bank_account_holder": "Pemilik Rekening",
                    "is_active": "Status Aktif",
                    "nik": "NIK",
                    "account_number": "No Akun",
                    "tax_address": "Alamat Pajak",
                    "tax_city": "Kota Pajak",
                    "tax_province": "Provinsi Pajak",
                    "tax_postal_code": "Kode Pos Pajak",
                    "nitku": "NITKU",
                    "negara": "Negara",
                    "default_tax_code": "Kode Pajak Bawaan",
                    "default_pph_type": "Jenis PPh Bawaan",
                    "default_pph_rate": "Tarif PPh Bawaan",
                }

                changes_logged = []
                for field, new_value in update_data.items():
                    old_value = existing.get(field)
                    # Normalize values for comparison
                    if old_value != new_value:
                        label = field_labels.get(field, field)
                        old_display = str(old_value) if old_value is not None else "-"
                        new_display = str(new_value) if new_value is not None else "-"

                        # Log each change
                        await conn.execute(
                            """
                        INSERT INTO vendor_activities
                        (vendor_id, tenant_id, type, description, field_name, old_value, new_value, actor_id, actor_name)
                        VALUES ($1, $2, 'updated', $3, $4, $5, $6, $7, $8)
                        """,
                            vendor_id,
                            ctx["tenant_id"],
                            f"{label} diubah",
                            field,
                            old_display,
                            new_display,
                            user_id,
                            user_name,
                        )
                        changes_logged.append(
                            f"{label}: {old_display} -> {new_display}"
                        )

                for nama_anak in anak_berubah:
                    await conn.execute(
                        """
                        INSERT INTO vendor_activities
                        (vendor_id, tenant_id, type, description, field_name, actor_id, actor_name)
                        VALUES ($1, $2, 'updated', $3, $4, $5, $6)
                        """,
                        vendor_id,
                        ctx["tenant_id"],
                        "Alamat pengiriman diubah" if nama_anak == "shipping_address" else "Kontak diubah",
                        nama_anak,
                        user_id,
                        user_name,
                    )

                if changes_logged:
                    logger.info(f"Vendor {vendor_id} changes logged: {changes_logged}")

                return {
                    "success": True,
                    "message": "Vendor updated successfully",
                    "data": {"id": str(vendor_id)},
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating vendor {vendor_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update vendor")


# =============================================================================
# TOGGLE VENDOR STATUS
# =============================================================================
@router.patch("/{vendor_id}/status", response_model=VendorResponse)
async def toggle_vendor_status(
    request: Request,
    vendor_id: UUID,
    status: Literal["active", "inactive"] = Query(..., description="New status"),
    response: Response = None,
):
    """
    Toggle vendor active/inactive status.

    This is a convenience endpoint for quickly changing vendor status.
    Equivalent to PATCH /vendors/:id with { is_active: true/false }
    VENDOR-IDEM (25 Okt 2026): idem VENDOR_STATUS + kunci VENDOR:{tenant}:{id} + FOR UPDATE (aktivitas status tercatat sekali per maksud).
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                kp, sidik, lama = await idem_buat.mulai_aksi(conn, ctx, idem_buat.kunci_dari(request), "VENDOR_STATUS", vendor_id,
                                                             {"status": status}, response)
                if lama is not None:
                    return lama
                await _kunci_vendor(conn, ctx["tenant_id"], vendor_id)
                # Check if vendor exists
                existing = await conn.fetchrow(
                    "SELECT id, name, is_active FROM vendors WHERE id = $1 AND tenant_id = $2 FOR UPDATE",
                    vendor_id,
                    ctx["tenant_id"],
                )
                if not existing:
                    raise HTTPException(status_code=404, detail="Vendor not found")

                is_active = status == "active"

                # Update status
                await conn.execute(
                    """
                UPDATE vendors
                SET is_active = $1, updated_at = NOW()
                WHERE id = $2 AND tenant_id = $3
                """,
                    is_active,
                    vendor_id,
                    ctx["tenant_id"],
                )

                logger.info(f"Vendor status changed: {vendor_id}, status={status}")

                # Log activity: Status change
                user_id = ctx.get("user_id")
                user = request.state.user
                user_name = (
                    user.get("fullname")
                    or user.get("username")
                    or user.get("email")
                    or "System"
                )
                old_status = "aktif" if existing["is_active"] else "nonaktif"
                new_status = "aktif" if is_active else "nonaktif"

                await conn.execute(
                    """
                INSERT INTO vendor_activities
                (vendor_id, tenant_id, type, description, field_name, old_value, new_value, actor_id, actor_name)
                VALUES ($1, $2, 'status_changed', 'Status vendor diubah', 'is_active', $3, $4, $5, $6)
                """,
                    vendor_id,
                    ctx["tenant_id"],
                    old_status,
                    new_status,
                    user_id,
                    user_name,
                )

                resp = {
                    "success": True,
                    "message": "Status vendor berhasil diubah",
                    "data": {
                        "id": str(vendor_id),
                        "name": existing["name"],
                        "is_active": is_active,
                    },
                }
                return await idem_buat.simpan(conn, ctx, kp, sidik, "VENDOR_STATUS", resp, vendor_id) if kp else resp

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error toggling vendor status {vendor_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update vendor status")


# =============================================================================
# DELETE VENDOR (Hard delete - removes the row)
# =============================================================================
@router.delete("/{vendor_id}", response_model=VendorResponse)
async def delete_vendor(request: Request, vendor_id: UUID, response: Response = None):
    """Delete a vendor. Hard-deletes if no ledger/transaction footprint; else raises 400.
    VENDOR-IDEM (25 Okt 2026): idem VENDOR_DELETE (ulang ber-kunci sesudah sukses = jawaban pertama, bukan 404) + kunci VENDOR:{tenant}:{id}
    + VENDOR_OPENING:{id} -- cek jejak (tagihan) tak lagi berpacu dengan pembuatan tagihan pembuka saldo awal."""
    from ..services.smart_delete import smart_delete

    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                kp, sidik, lama = await idem_buat.mulai_aksi(conn, ctx, idem_buat.kunci_dari(request), "VENDOR_DELETE", vendor_id, {}, response)
                if lama is not None:
                    return lama
                await _kunci_vendor(conn, ctx["tenant_id"], vendor_id, saldo_awal=True)
                result = await smart_delete(
                    conn,
                    tenant_id=ctx["tenant_id"],
                    entity_id=str(vendor_id),
                    table="vendors",
                    name_column="name",
                    ledger_checks=[
                        (
                            "bills",
                            "vendor_id",
                            "Tidak bisa menghapus vendor yang sudah memiliki tagihan. Nonaktifkan saja.",
                        ),
                        (
                            "accounts_payable",
                            "supplier_id",
                            "Tidak bisa menghapus vendor yang memiliki catatan utang. Nonaktifkan saja.",
                        ),
                        (
                            "vendor_credits",
                            "vendor_id",
                            "Tidak bisa menghapus vendor yang memiliki kredit. Nonaktifkan saja.",
                        ),
                        (
                            "vendor_deposits",
                            "vendor_id",
                            "Tidak bisa menghapus vendor yang memiliki deposit. Nonaktifkan saja.",
                        ),
                    ],
                )
                resp = {
                    "success": True,
                    "message": result["message"],
                    "data": {"id": str(vendor_id), "name": result["name"]},
                }
                return await idem_buat.simpan(conn, ctx, kp, sidik, "VENDOR_DELETE", resp, vendor_id) if kp else resp
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting vendor {vendor_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to delete vendor")


# =============================================================================
# VENDOR NEXT CODE (for form auto-generation)
# =============================================================================



# =============================================================================
# VENDOR TRANSACTIONS HISTORY
# =============================================================================


@router.get("/{vendor_id}/transactions")
async def get_vendor_transactions(
    request: Request,
    vendor_id: str,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
):
    """Get transaction history for a vendor."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        offset = (page - 1) * limit
        async with pool.acquire() as conn:
            vendor = await conn.fetchrow(
                "SELECT id, name FROM vendors WHERE id = $1 AND tenant_id = $2",
                vendor_id,
                ctx["tenant_id"],
            )
            if not vendor:
                raise HTTPException(status_code=404, detail="Vendor not found")
            # Fixed: use correct column names from bills and purchase_orders tables
            rows = await conn.fetch(
                """
                SELECT * FROM (
                    SELECT id::text, 'bill' as type, invoice_number as number,
                        issue_date as date, COALESCE(grand_total, amount) as amount, 'Bill' as description, status_v2 as status
                    FROM bills WHERE tenant_id = $1 AND vendor_id::text = $2
                    UNION ALL
                    SELECT id::text, 'purchase_order' as type, po_number as number,
                        po_date as date, total_amount as amount, 'Purchase Order' as description, status
                    FROM purchase_orders WHERE tenant_id = $1 AND vendor_id::text = $2
                ) t ORDER BY date DESC LIMIT $3 OFFSET $4
            """,
                ctx["tenant_id"],
                vendor_id,
                limit,
                offset,
            )
            count = await conn.fetchval(
                """
                SELECT COUNT(*) FROM (
                    SELECT id FROM bills WHERE tenant_id = $1 AND vendor_id::text = $2
                    UNION ALL SELECT id FROM purchase_orders WHERE tenant_id = $1 AND vendor_id::text = $2
                ) t
            """,
                ctx["tenant_id"],
                vendor_id,
            )
            transactions = [
                {
                    "id": row["id"],
                    "type": row["type"],
                    "number": row["number"],
                    "date": row["date"].isoformat() if row["date"] else None,
                    "amount": row["amount"],
                    "description": row["description"],
                    "status": row["status"],
                }
                for row in rows
            ]
            return {
                "transactions": transactions,
                "total": count,
                "page": page,
                "limit": limit,
                "has_more": offset + limit < count,
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting vendor transactions: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get transactions")


# =============================================================================
# VENDOR OPEN BILLS (for pay bills feature)
# =============================================================================


@router.get("/{vendor_id}/open-bills")
async def get_vendor_open_bills(request: Request, vendor_id: str):
    """Get open (unpaid/partially paid) bills for a vendor."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        async with pool.acquire() as conn:
            # #10b-3a: hari ini = tanggal bisnis tenant, bukan UTC
            hari_ini = await tanggal_dokumen(conn, ctx["tenant_id"])
            # Fixed: use correct column names from bills table
            # invoice_number instead of bill_number
            # issue_date instead of bill_date
            # grand_total/amount instead of total_amount
            # status_v2 instead of status
            # Pure Ledger: open bills from compute_vendor_ap() DB function
            rows = await conn.fetch(
                """
                SELECT bill_id, bill_number, bill_date, due_date,
                       bill_total as total_amount,
                       paid_amount,
                       outstanding as remaining_amount,
                       CASE WHEN due_date < $3::date THEN true ELSE false END as is_overdue
                FROM compute_vendor_ap($1, $2::uuid)
                WHERE outstanding > 0 AND bill_id IS NOT NULL  -- V457: baris vendor tanpa tagihan (saldo awal / pembayaran yatim / nota debit bebas) bukan tagihan
                ORDER BY due_date ASC
            """,
                ctx["tenant_id"],
                vendor_id,
                hari_ini,
            )
            bills = [
                {
                    "id": str(row["bill_id"]),
                    "bill_number": row["bill_number"],
                    "bill_date": row["bill_date"].isoformat(),
                    "due_date": row["due_date"].isoformat(),
                    "total_amount": row["total_amount"],
                    "paid_amount": int(row["paid_amount"]) or 0,
                    "remaining_amount": int(row["remaining_amount"]),
                    "is_overdue": row["is_overdue"],
                }
                for row in rows
            ]
            total_outstanding = sum(b["remaining_amount"] for b in bills)
            return {"bills": bills, "total_outstanding": total_outstanding}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting open bills: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get open bills")


# =============================================================================
# VENDOR OPENING BALANCE
# =============================================================================


class SaldoAwalVendorIn(BaseModel):
    """Badan POST /vendors/{id}/opening-balance -- kontrak FE TETAP (amount, as_of_date, description | notes)."""
    amount: Decimal
    as_of_date: Optional[date] = None
    description: Optional[str] = None


KODE_SALDO_AWAL_VENDOR_SUDAH_ADA = "SALDO_AWAL_VENDOR_SUDAH_ADA"
KODE_AKUN_SALDO_AWAL_BELUM_DIPETAKAN = "AKUN_SALDO_AWAL_BELUM_DIPETAKAN"


@router.post("/{vendor_id}/opening-balance")
async def set_vendor_opening_balance(request: Request, vendor_id: str, response: Response = None):
    """
    SALDO-AWAL-VENDOR-A (25 Okt 2026; aturan "Saldo awal hutang vendor = tagihan pembuka" + V458 DISETUJUI LANGSUNG pemilik).
    SATU transaksi: draf tagihan pembuka (bills.is_opening_balance, 1 baris "Saldo awal hutang", tanpa PPN, jatuh tempo = tanggal saldo awal)
    lalu posting lewat BillsService.post_bill YANG SAMA (conn_luar) -> jurnal Dr 3-50000 Modal Saldo Awal / Cr 2-10100 Hutang Usaha, source
    BILL, penentu tagihan_aksi.blok_posting/hitung_debit (== pratinjau). Masuk compute_ap sebagai tagihan biasa (umur, Pembayaran Keluar,
    batal = void tagihan). Menggantikan jurnal VENDOR_OPENING_BALANCE lama yang TERBALIK (Dr Hutang -> hutang negatif, V457) dan penahan 409
    SALDO-AWAL-VENDOR-C. Idem X-Idempotency-Key (VENDOR_OB). Satu tagihan pembuka AKTIF per vendor (409 di sini + indeks unik V458).
    """
    from ..services import idem_buat as _ib
    from ..services.role_resolver import AccountRoleUnmappedError
    from .bills import get_bills_service

    ctx = get_user_context(request)
    raw = await request.json()
    try:
        tgl = raw.get("as_of_date")
        masuk = SaldoAwalVendorIn(amount=Decimal(str(raw.get("amount", 0))),
                                  as_of_date=date.fromisoformat(tgl) if isinstance(tgl, str) and tgl else None,
                                  description=raw.get("description") or raw.get("notes"))
        vid = UUID(str(vendor_id))
    except (InvalidOperation, ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Nominal, tanggal, atau vendor saldo awal tidak sah.")
    if masuk.amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be greater than zero")
    pool = await get_pool()
    await _ensure_role_preconditions(pool, ctx["tenant_id"])
    svc = await get_bills_service()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])
            kp, sidik, lama = await _ib.mulai(conn, ctx, _ib.kunci_dari(request), "VENDOR_OB", masuk, "bills", "bill", response)
            if lama is not None:
                return lama
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"VENDOR_OPENING:{vid}")
            vendor = await conn.fetchrow("SELECT id, name FROM vendors WHERE id = $1 AND tenant_id = $2", vid, ctx["tenant_id"])
            if not vendor:
                raise HTTPException(status_code=404, detail="Vendor not found")
            try:
                ap_account = await _resolve_role_account(conn, ctx["tenant_id"], AccountRole.AP_TRADE)
                equity_account = await _resolve_role_account(conn, ctx["tenant_id"], AccountRole.EQUITY_OPENING_BALANCE)
            except (AccountRoleUnmappedError, HTTPException):
                raise HTTPException(status_code=422, detail={
                    "code": KODE_AKUN_SALDO_AWAL_BELUM_DIPETAKAN,
                    "message": "Akun Hutang Usaha atau Modal Saldo Awal belum dipetakan. Atur di Pengaturan, Akun Default."})
            ada = await conn.fetchval(
                "SELECT invoice_number FROM bills WHERE tenant_id = $1 AND vendor_id = $2 AND is_opening_balance AND status_v2 <> 'void'",
                ctx["tenant_id"], vendor["id"])
            ada = ada or await conn.fetchval(  # jurnal VENDOR_OPENING_BALANCE lama (terbalik) yang belum dibalik
                """SELECT journal_number FROM journal_entries WHERE tenant_id = $1 AND source_type = 'VENDOR_OPENING_BALANCE' AND source_id = $2
                   AND status = 'POSTED' AND reversed_by_id IS NULL AND reversal_of_id IS NULL""", ctx["tenant_id"], vendor["id"])
            if ada:
                raise HTTPException(status_code=409, detail={
                    "code": KODE_SALDO_AWAL_VENDOR_SUDAH_ADA,
                    "message": f"Vendor ini sudah punya saldo awal {ada}. Batalkan dulu untuk menggantinya."})
            tanggal = masuk.as_of_date or await tanggal_dokumen(conn, ctx["tenant_id"])
            nomor = await conn.fetchval("SELECT generate_purchase_bill_number($1)", ctx["tenant_id"])
            bill_id = await conn.fetchval(
                """INSERT INTO bills (tenant_id, invoice_number, ref_no, vendor_id, vendor_name, amount, issue_date, due_date, notes, created_by,
                                      status_v2, tax_rate, tax_inclusive, subtotal, dpp, tax_amount, grand_total, is_opening_balance)
                   VALUES ($1, $2, 'SALDO AWAL', $3, $4, $5::numeric, $6, $6, $7, $8, 'draft', 0, false, $5::numeric, $5::numeric, 0, $5::numeric, true)
                   RETURNING id""",  # bills.dpp BIGINT, kolom uang lain NUMERIC -> tipe $5 eksplisit
                ctx["tenant_id"], nomor, vendor["id"], vendor["name"], masuk.amount, tanggal,
                masuk.description or f"Saldo awal hutang - {vendor['name']}", UUID(str(ctx["user_id"])))
            await conn.execute(
                """INSERT INTO bill_items (bill_id, description, quantity, unit_price, subtotal, total, line_number, tax_rate, tax_amount, dpp)
                   VALUES ($1, 'Saldo awal hutang', 1, $2::numeric, $2::numeric, $2::numeric, 1, 0, 0, $2::numeric)""", bill_id, masuk.amount)
            hasil = await svc.post_bill(ctx["tenant_id"], bill_id, UUID(str(ctx["user_id"])), conn_luar=conn)
            if not hasil.get("success"):
                raise HTTPException(status_code=400, detail=hasil.get("message") or "Saldo awal vendor gagal diposting.")
            jr = await conn.fetchrow(
                "SELECT je.id, je.journal_number FROM bills b JOIN journal_entries je ON je.id::text = b.journal_id::text WHERE b.id = $1", bill_id)
            resp = {
                "success": True,
                "message": "Saldo awal vendor dicatat sebagai tagihan pembuka",
                "data": {
                    "vendor_id": str(vendor["id"]),
                    "vendor_name": vendor["name"],
                    "amount": float(masuk.amount),
                    "as_of_date": tanggal.isoformat(),
                    "bill_id": str(bill_id),
                    "bill_number": nomor,
                    "journal_id": str(jr["id"]),
                    "journal_number": jr["journal_number"],
                    "journal_entry": {
                        "debit": {"account_code": equity_account["account_code"], "account_name": equity_account["name"],
                                  "amount": float(masuk.amount)},
                        "credit": {"account_code": ap_account["account_code"], "account_name": ap_account["name"],
                                   "amount": float(masuk.amount)},
                    },
                },
            }
            return await _ib.simpan(conn, ctx, kp, sidik, "VENDOR_OB", resp, bill_id) if kp else resp


@router.get("/{vendor_id}/journal-entries")
async def get_vendor_journal_entries(
    request: Request,
    vendor_id: UUID,
    start_date: Optional[str] = Query(
        None, description="Start date filter (YYYY-MM-DD)"
    ),
    end_date: Optional[str] = Query(None, description="End date filter (YYYY-MM-DD)"),
    source_type: Optional[str] = Query(
        None, description="Filter by source type (PURCHASE_INVOICE, PAYMENT_MADE)"
    ),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
):
    """
    Get journal entries related to a vendor.

    Data comes from journal_entries table (Law 1: Ledger Supremacy).
    Read-only endpoint (Law 2: Journal Immutability).
    Results are deterministic and reproducible (Law 9: Deterministic Reporting).

    Journal entries are linked via:
    - bills.journal_id (source_type = PURCHASE_INVOICE)
    - bill_payments_v2.journal_id (source_type = PAYMENT_MADE)
    - vendor_deposits.journal_id (source_type = VENDOR_DEPOSIT)
    - vendor_credits.journal_id (source_type = VENDOR_CREDIT)

    Returns entries sorted by journal_date DESC for deterministic ordering.
    """
    from datetime import datetime

    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Check if vendor exists
            vendor = await conn.fetchrow(
                "SELECT id, name FROM vendors WHERE id = $1 AND tenant_id = $2",
                vendor_id,
                ctx["tenant_id"],
            )
            if not vendor:
                raise HTTPException(status_code=404, detail="Vendor not found")

            # Build conditions for filtering
            conditions = ["je.tenant_id = $1", "je.status = 'POSTED'"]
            params = [ctx["tenant_id"]]
            param_idx = 2

            # Date filters
            if start_date:
                try:
                    parsed_start = datetime.strptime(start_date, "%Y-%m-%d").date()
                    conditions.append(f"je.journal_date >= ${param_idx}")
                    params.append(parsed_start)
                    param_idx += 1
                except ValueError:
                    raise HTTPException(
                        status_code=400,
                        detail="Invalid start_date format. Use YYYY-MM-DD",
                    )

            if end_date:
                try:
                    parsed_end = datetime.strptime(end_date, "%Y-%m-%d").date()
                    conditions.append(f"je.journal_date <= ${param_idx}")
                    params.append(parsed_end)
                    param_idx += 1
                except ValueError:
                    raise HTTPException(
                        status_code=400,
                        detail="Invalid end_date format. Use YYYY-MM-DD",
                    )

            # Source type filter
            if source_type:
                source_type_upper = source_type.upper()
                conditions.append(f"je.source_type = ${param_idx}")
                params.append(source_type_upper)
                param_idx += 1

            where_clause = " AND ".join(conditions)
            offset = (page - 1) * limit
            vendor_id_param_idx = param_idx
            limit_param_idx = param_idx + 1
            offset_param_idx = param_idx + 2

            params.extend([vendor_id, limit, offset])

            # Query journal entries linked to this vendor through bills, payments, deposits, credits
            query = f"""
                WITH vendor_journals AS (
                    -- Bills (Purchase Invoices)
                    SELECT DISTINCT je.id as journal_id
                    FROM journal_entries je
                    INNER JOIN bills b ON b.journal_id = je.id
                    WHERE {where_clause}
                      AND b.vendor_id = ${vendor_id_param_idx}

                    UNION

                    -- Bill Payments
                    SELECT DISTINCT je.id as journal_id
                    FROM journal_entries je
                    INNER JOIN bill_payments_v2 bp ON bp.journal_id = je.id
                    WHERE {where_clause}
                      AND bp.vendor_id = ${vendor_id_param_idx}

                    UNION

                    -- Vendor Deposits
                    SELECT DISTINCT je.id as journal_id
                    FROM journal_entries je
                    INNER JOIN vendor_deposits vd ON vd.journal_id = je.id
                    WHERE {where_clause}
                      AND vd.vendor_id = ${vendor_id_param_idx}

                    UNION

                    -- Vendor Credits
                    SELECT DISTINCT je.id as journal_id
                    FROM journal_entries je
                    INNER JOIN vendor_credits vc ON vc.journal_id = je.id
                    WHERE {where_clause}
                      AND vc.vendor_id = ${vendor_id_param_idx}
                )
                SELECT
                    je.id,
                    je.journal_date,
                    je.journal_number,
                    je.description,
                    je.source_type,
                    je.total_debit,
                    je.total_credit,
                    je.status,
                    je.created_at
                FROM journal_entries je
                INNER JOIN vendor_journals vj ON vj.journal_id = je.id
                ORDER BY je.journal_date DESC, je.created_at DESC
                LIMIT ${limit_param_idx} OFFSET ${offset_param_idx}
            """

            rows = await conn.fetch(query, *params)

            # Get journal lines for each entry
            entries = []
            for row in rows:
                # Fetch lines with account names
                lines_query = """
                    SELECT
                        jl.id,
                        jl.line_number,
                        jl.account_id,
                        coa.name as account_name,
                        coa.account_code,
                        jl.debit,
                        jl.credit,
                        jl.memo
                    FROM journal_lines jl
                    INNER JOIN chart_of_accounts coa ON coa.id = jl.account_id
                    WHERE jl.journal_id = $1
                    ORDER BY jl.line_number ASC
                """
                line_rows = await conn.fetch(lines_query, row["id"])

                lines = [
                    {
                        "id": str(lr["id"]),
                        "line_number": lr["line_number"],
                        "account_id": str(lr["account_id"]),
                        "account_name": lr["account_name"],
                        "account_code": lr["account_code"],
                        "debit": int(lr["debit"]) if lr["debit"] else 0,
                        "credit": int(lr["credit"]) if lr["credit"] else 0,
                        "memo": lr["memo"],
                    }
                    for lr in line_rows
                ]

                entries.append(
                    {
                        "id": str(row["id"]),
                        "date": row["journal_date"].isoformat(),
                        "journal_number": row["journal_number"],
                        "description": row["description"],
                        "source_type": row["source_type"],
                        "total_debit": int(row["total_debit"])
                        if row["total_debit"]
                        else 0,
                        "total_credit": int(row["total_credit"])
                        if row["total_credit"]
                        else 0,
                        "status": row["status"],
                        "lines": lines,
                    }
                )

            # Get total count for pagination
            count_query = f"""
                WITH vendor_journals AS (
                    SELECT DISTINCT je.id as journal_id
                    FROM journal_entries je
                    INNER JOIN bills b ON b.journal_id = je.id
                    WHERE {where_clause}
                      AND b.vendor_id = ${vendor_id_param_idx}

                    UNION

                    SELECT DISTINCT je.id as journal_id
                    FROM journal_entries je
                    INNER JOIN bill_payments_v2 bp ON bp.journal_id = je.id
                    WHERE {where_clause}
                      AND bp.vendor_id = ${vendor_id_param_idx}

                    UNION

                    SELECT DISTINCT je.id as journal_id
                    FROM journal_entries je
                    INNER JOIN vendor_deposits vd ON vd.journal_id = je.id
                    WHERE {where_clause}
                      AND vd.vendor_id = ${vendor_id_param_idx}

                    UNION

                    SELECT DISTINCT je.id as journal_id
                    FROM journal_entries je
                    INNER JOIN vendor_credits vc ON vc.journal_id = je.id
                    WHERE {where_clause}
                      AND vc.vendor_id = ${vendor_id_param_idx}
                )
                SELECT COUNT(*) FROM vendor_journals
            """
            # Only need params up to vendor_id for count query
            count_params = params[:-2]  # Exclude limit and offset
            total = await conn.fetchval(count_query, *count_params)

            return {
                "success": True,
                "data": {
                    "entries": entries,
                    "vendor_id": str(vendor_id),
                    "vendor_name": vendor["name"],
                    "total": total,
                    "page": page,
                    "limit": limit,
                    "has_more": offset + limit < total,
                },
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Error getting vendor journal entries {vendor_id}: {e}", exc_info=True
        )
        raise HTTPException(status_code=500, detail="Failed to get journal entries")


# =============================================================================
# VENDOR ACTIVITY HISTORY (Read-only - Law 12: Audit Trail Immutability)
# =============================================================================


@router.get("/{vendor_id}/activity", response_model=VendorActivityResponse)
async def get_vendor_activity(
    request: Request,
    vendor_id: UUID,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """Get activity log / audit trail for a vendor."""
    ctx = get_user_context(request)
    pool = await get_pool()
    conn = None

    try:
        conn = await pool.acquire()

        # Verify vendor exists
        vendor_exists = await conn.fetchval(
            "SELECT id FROM vendors WHERE id = $1 AND tenant_id = $2",
            vendor_id,
            ctx["tenant_id"],
        )
        if not vendor_exists:
            raise HTTPException(status_code=404, detail="Vendor not found")

        # Get total count (including transaction events)
        count_query = """
            SELECT (
                SELECT COUNT(*) FROM vendor_activities
                WHERE vendor_id = $1 AND tenant_id = $2
            ) + (
                SELECT COUNT(*) FROM bills
                WHERE vendor_id = $1 AND tenant_id = $2
                  AND status_v2 NOT IN ('draft', 'void')
            ) + (
                SELECT COUNT(*) FROM bill_payments_v2
                WHERE vendor_id = $1 AND tenant_id = $2
                  AND status NOT IN ('draft', 'void')
            )
        """
        total = await conn.fetchval(count_query, vendor_id, ctx["tenant_id"])

        # Get activities (UNION with transaction events)
        query = """
            SELECT id::text, type, description, details, actor_name, timestamp,
                   NULL as document_number, NULL::numeric as total,
                   field_name, old_value, new_value
            FROM vendor_activities
            WHERE vendor_id = $1 AND tenant_id = $2

            UNION ALL

            SELECT b.id::text, 'bill' as type,
                   'Faktur pembelian ' || b.invoice_number as description,
                   'Total: ' || COALESCE(b.grand_total, b.amount)::text as details,
                   NULL as actor_name,
                   b.created_at as timestamp,
                   b.invoice_number as document_number,
                   COALESCE(b.grand_total, b.amount)::numeric as total,
                   NULL as field_name, NULL as old_value, NULL as new_value
            FROM bills b
            WHERE b.vendor_id = $1 AND b.tenant_id = $2
              AND b.status_v2 NOT IN ('draft', 'void')

            UNION ALL

            SELECT bp.id::text, 'payment' as type,
                   'Pembayaran ' || bp.payment_number as description,
                   'Total: ' || bp.total_amount::text as details,
                   NULL as actor_name,
                   bp.created_at as timestamp,
                   bp.payment_number as document_number,
                   bp.total_amount::numeric as total,
                   NULL as field_name, NULL as old_value, NULL as new_value
            FROM bill_payments_v2 bp
            WHERE bp.vendor_id = $1 AND bp.tenant_id = $2
              AND bp.status NOT IN ('draft', 'void')

            ORDER BY timestamp DESC
            LIMIT $3 OFFSET $4
        """
        rows = await conn.fetch(query, vendor_id, ctx["tenant_id"], limit, offset)

        activities = [
            VendorActivity(
                id=str(row["id"]),
                type=row["type"],
                description=row["description"],
                details=row.get("details"),
                actor_name=row.get("actor_name"),
                timestamp=row["timestamp"].isoformat() if row["timestamp"] else None,
                document_number=row.get("document_number"),
                total=int(row["total"]) if row.get("total") is not None else None,
                field_name=row.get("field_name"),
                old_value=row.get("old_value"),
                new_value=row.get("new_value"),
            )
            for row in rows
        ]

        return VendorActivityResponse(
            success=True,
            activities=activities,
            total=total or 0,
            has_more=(offset + limit) < (total or 0),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting vendor activity: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn:
            await pool.release(conn)


# =============================================================================
# MERGE VENDORS -- GABUNG-VENDOR (25 Okt 2026, putusan MASTER (a) tanpa migrasi; pola NetSuite/QBO = customers.merge)
# =============================================================================
# Sumber DINONAKTIFKAN (is_active=false + catatan), SEMUA transaksi/dokumen pindah ke tujuan (vendor_id; vendor_name ikut nama tujuan bila
# kolomnya ada), jejak = audit_logs VENDOR_MERGED + vendor_activities di kedua vendor. NOL jurnal ditulis/diubah: GL tetap; saldo hutang
# per vendor (compute_ap_outstanding, diturunkan dari bills/vendor_credits/bill_payments_v2.vendor_id) BERPINDAH utuh ke tujuan.
# Dulu: badan rute membaca/menulis kolom yang TAK ADA (vendors.merged_into_id/deleted_at/deleted_by) + tabel TAK ADA (master_data_audit_log)
# -> 500 di setiap panggilan (diparkir 409 di VENDOR-IDEM 5c9c4395).
# Penolak (tak dilemahkan, pesan terbaca): (1) jurnal saldo awal LAMA (VENDOR_OPENING_BALANCE, source_id = vendor sumber) bersaldo -- jurnal
# immutable, tak bisa ikut pindah; (2) sumber DAN tujuan sama-sama punya tagihan pembuka saldo awal aktif (uq_bill_saldo_awal_vendor);
# (3) sumber punya tagihan ber-status_v2 NULL (pemicu status menolak UPDATE apa pun pada baris itu).
GABUNG_TABEL = (  # (tabel, kolom vendor, ada tenant_id, ada vendor_name) -- diukur information_schema 25 Okt 2026
    ("bills", "vendor_id", True, True),
    ("bill_payments_v2", "vendor_id", True, True),
    ("vendor_credits", "vendor_id", True, True),
    ("vendor_deposits", "vendor_id", True, False),
    ("purchase_orders", "vendor_id", True, True),
    ("goods_receipts", "vendor_id", True, False),
    ("expenses", "vendor_id", True, True),
    ("recurring_bills", "vendor_id", True, False),
    ("withholding_tax_records", "vendor_id", True, False),
    ("cheques", "vendor_id", True, False),
    ("fixed_assets", "vendor_id", True, False),
    ("production_subcontracts", "vendor_id", True, False),
    ("accounts_payable", "supplier_id", True, False),
    ("asset_maintenance", "vendor_id", False, False),  # tanpa tenant_id: dibatasi id vendor sumber (sudah divalidasi milik tenant)
    ("bom_operations", "vendor_id", False, False),
)


def _gabung_ids(body: MergeVendorRequest):
    try:
        sumber, tujuan = UUID(str(body.source_vendor_id).strip()), UUID(str(body.target_vendor_id).strip())
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail="Vendor tidak ditemukan.")
    if sumber == tujuan:
        raise HTTPException(status_code=400, detail="Vendor sumber dan tujuan tidak boleh sama.")
    return sumber, tujuan


async def _gabung_rencana(conn, tenant_id: str, sumber, tujuan) -> dict:
    """SATU penentu gabung vendor (pratinjau DAN eksekusi; eksekusi memanggilnya DI DALAM kunci). NOL tulisan.
    -> {pihak, cacah per tabel, blocks}. Vendor tak ada / tenant lain -> 404 (pesan sama)."""
    baris = await conn.fetch("SELECT id, name, code, is_active, notes FROM vendors WHERE id = ANY($1::uuid[]) AND tenant_id = $2",
                             [sumber, tujuan], tenant_id)
    peta = {r["id"]: r for r in baris}
    if len(peta) != 2:
        raise HTTPException(status_code=404, detail="Vendor tidak ditemukan.")
    blok = []
    for vid, peran in ((sumber, "sumber"), (tujuan, "tujuan")):
        if not peta[vid]["is_active"]:
            blok.append({"code": "VENDOR_NONAKTIF", "message": f"Vendor {peran} ({peta[vid]['name']}) sudah nonaktif."})
    cacah = {}
    for tabel, kolom, ber_tenant, _ in GABUNG_TABEL:
        if ber_tenant:
            cacah[tabel] = await conn.fetchval(f"SELECT count(*) FROM {tabel} WHERE tenant_id = $1 AND {kolom} = $2", tenant_id, sumber)  # nosec B608 -- tabel/kolom literal GABUNG_TABEL
        else:
            cacah[tabel] = await conn.fetchval(f"SELECT count(*) FROM {tabel} WHERE {kolom} = $1", sumber)  # nosec B608
    ob_lama = await conn.fetchval(
        """SELECT COALESCE(SUM(jl.credit), 0) - COALESCE(SUM(jl.debit), 0) FROM journal_entries je
           JOIN journal_lines jl ON jl.journal_id = je.id JOIN chart_of_accounts coa ON coa.id = jl.account_id
           WHERE je.tenant_id = $1 AND je.source_type = 'VENDOR_OPENING_BALANCE' AND je.source_id = $2 AND je.status = 'POSTED'
             AND je.reversed_by_id IS NULL AND je.reversal_of_id IS NULL AND coa.account_type = 'PAYABLE'""", tenant_id, sumber)
    if ob_lama:
        blok.append({"code": "SALDO_AWAL_LAMA_TAK_BISA_PINDAH",
                     "message": f"Vendor sumber ({peta[sumber]['name']}) punya saldo awal hutang cara lama (jurnal langsung) yang tidak bisa "
                                "dipindah. Penggabungan belum bisa dilakukan."})
    pembuka = await conn.fetchval(
        """SELECT count(DISTINCT vendor_id) FROM bills WHERE tenant_id = $1 AND vendor_id = ANY($2::uuid[]) AND is_opening_balance
             AND status_v2 <> 'void'""", tenant_id, [sumber, tujuan])
    if pembuka == 2:
        blok.append({"code": "SALDO_AWAL_GANDA",
                     "message": "Kedua vendor sama-sama punya tagihan saldo awal. Batalkan salah satunya dulu, lalu gabungkan."})
    # Pemicu trg_validate_bill_status_v2 membandingkan OLD.status_v2 = NEW.status_v2: NULL = NULL bukan TRUE -> UPDATE kolom APA PUN pada
    # tagihan ber-status_v2 NULL ditolak (diukur 25 Okt: 1 baris, draf uji kaos). Tolak dengan pesan terbaca, bukan 500 di tengah pemindahan.
    tanpa_status = await conn.fetchval("SELECT count(*) FROM bills WHERE tenant_id = $1 AND vendor_id = $2 AND status_v2 IS NULL", tenant_id, sumber)
    if tanpa_status:
        blok.append({"code": "TAGIHAN_TANPA_STATUS",
                     "message": f"Vendor sumber ({peta[sumber]['name']}) punya {tanpa_status} tagihan lama tanpa status yang tidak bisa dipindah. "
                                "Perbaiki atau hapus tagihan itu dulu, lalu gabungkan."})
    return {"peta": peta, "cacah": cacah, "blocks": blok}


def _pihak(r) -> dict:
    return {"id": str(r["id"]), "name": r["name"], "code": r["code"]}


@router.post("/merge/preview")
async def preview_merge_vendors(request: Request, body: MergeVendorRequest):
    """Pratinjau gabung vendor: penentu YANG SAMA dengan POST /merge, transaksi READ ONLY tanpa kunci, NOL tulisan. 200 walau diblok."""
    try:
        ctx = get_user_context(request)
        sumber, tujuan = _gabung_ids(body)
        pool = await get_pool()
        async with pool.acquire() as conn, conn.transaction(readonly=True):
            r = await _gabung_rencana(conn, ctx["tenant_id"], sumber, tujuan)
        return {"success": True, "data": {
            "ok": not r["blocks"], "source": _pihak(r["peta"][sumber]), "target": _pihak(r["peta"][tujuan]),
            "records_to_move": r["cacah"], "blocks": r["blocks"], "irreversible": True}}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error previewing vendor merge: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Gagal menampilkan pratinjau penggabungan vendor.")


@router.post("/merge", response_model=MergeVendorResponse)
async def merge_vendors(request: Request, body: MergeVendorRequest, response: Response = None):
    """Gabungkan vendor sumber ke vendor tujuan (TAK BISA DIBATALKAN). Kontrak FE tetap: {source_vendor_id, target_vendor_id} ->
    {success, message, data:{source, target, summary}}. Idem VENDOR_MERGE (ulang ber-kunci = jawaban pertama). Kunci KEDUA vendor (urut) +
    VENDOR_OPENING keduanya; penentu _gabung_rencana diulang DI DALAM kunci; blok -> 409 dengan pesan blok pertama."""
    try:
        ctx = get_user_context(request)
        if not ctx.get("user_id"):
            raise HTTPException(status_code=401, detail="ID pengguna wajib ada.")
        sumber, tujuan = _gabung_ids(body)
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                kp, sidik, lama = await idem_buat.mulai_aksi(conn, ctx, idem_buat.kunci_dari(request), "VENDOR_MERGE", sumber,
                                                             lambda: body.model_dump(mode="json"), response)
                if lama is not None:
                    return lama
                await _kunci_vendor(conn, ctx["tenant_id"], sumber, tujuan, saldo_awal=True)
                r = await _gabung_rencana(conn, ctx["tenant_id"], sumber, tujuan)
                if r["blocks"]:
                    b = r["blocks"][0]
                    raise HTTPException(status_code=409, detail={"code": b["code"], "message": b["message"]})
                ps, pt = r["peta"][sumber], r["peta"][tujuan]

                pindah = {}
                for tabel, kolom, ber_tenant, ber_nama in GABUNG_TABEL:
                    nama = ", vendor_name = $4" if ber_nama else ""
                    if ber_tenant:
                        hasil = await conn.execute(
                            f"UPDATE {tabel} SET {kolom} = $1{nama} WHERE tenant_id = $2 AND {kolom} = $3",  # nosec B608 -- literal GABUNG_TABEL
                            tujuan, ctx["tenant_id"], sumber, *([pt["name"]] if ber_nama else []))
                    else:
                        hasil = await conn.execute(f"UPDATE {tabel} SET {kolom} = $1 WHERE {kolom} = $2", tujuan, sumber)  # nosec B608
                    pindah[tabel] = int(hasil.split()[-1])
                if pindah != r["cacah"]:  # tak mungkin di dalam kunci; bila terjadi, jangan setengah jadi
                    raise HTTPException(status_code=409, detail="Data vendor berubah saat digabung; muat ulang pratinjau.")

                catatan = f"{ps['notes'] or ''}\n[DIGABUNG] ke '{pt['name']}' (ID: {tujuan})".strip()
                await conn.execute("UPDATE vendors SET is_active = false, notes = $3, updated_at = NOW() WHERE id = $1 AND tenant_id = $2",
                                   sumber, ctx["tenant_id"], catatan)
                user = request.state.user
                aktor = user.get("fullname") or user.get("username") or user.get("email") or "System"
                for vid, teks in ((tujuan, f"Vendor {ps['name']} digabung ke vendor ini"), (sumber, f"Digabung ke vendor {pt['name']}")):
                    await conn.execute(
                        """INSERT INTO vendor_activities (vendor_id, tenant_id, type, description, actor_id, actor_name, metadata)
                           VALUES ($1, $2, 'merged', $3, $4, $5, $6::jsonb)""",
                        vid, ctx["tenant_id"], teks, ctx["user_id"], aktor,
                        json.dumps({"source_id": str(sumber), "target_id": str(tujuan), "moved": pindah}))
                await conn.execute(
                    """INSERT INTO audit_logs (id, "eventType", entity_type, entity_id, entity_number, tenant_id, source, metadata, success, "createdAt")
                       VALUES (gen_random_uuid()::text, 'VENDOR_MERGED', 'vendor', $1, $2, $3, 'api:vendors.merge',
                               jsonb_build_object('target_id', $4::text, 'target_name', $5::text, 'moved', $6::jsonb, 'user_id', $7::text), true, now())""",
                    str(sumber), ps["name"], ctx["tenant_id"], str(tujuan), pt["name"], json.dumps(pindah), str(ctx["user_id"]))

                total = sum(pindah.values())
                logger.info(f"Vendor merge: {sumber} -> {tujuan}, pindah {total} baris")
                resp = MergeVendorResponse(
                    success=True,
                    message=f"Vendor '{ps['name']}' digabung ke '{pt['name']}'. {total} catatan dipindah.",
                    data={"source": _pihak(ps), "target": _pihak(pt), "summary": pindah},
                )
                return await idem_buat.simpan(conn, ctx, kp, sidik, "VENDOR_MERGE", resp, sumber) if kp else resp
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Merge error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Gagal menggabungkan vendor.")
