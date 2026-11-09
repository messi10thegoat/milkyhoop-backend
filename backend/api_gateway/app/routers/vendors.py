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
from ..services import kontak_daftar as KD  # B1 4 Nov 2026: daftar/ringkasan Pelanggan + Vendor satu penentu
from ..services import kontak_riwayat as KR  # B4: riwayat + /activity satu sumber
from ..services import kontak_detail as KDT  # B2 detail + aksi_tersedia + rename-impact (satu penentu dua modul)
from ..services import vendor_unik as VU  # VENDOR-DUPLIKAT (1 Nov 2026): 409 berkode satu bentuk dengan pelanggan
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
        kecuali = UUID(excludeId) if excludeId else None

        def _baris(row):
            return {"id": str(row["id"]), "name": row["name"], "company_name": row["company_name"],
                    "display_name": row["display_name"], "company": row["company_name"], "npwp": row["tax_id"],
                    "is_active": bool(row["is_active"])}

        # VENDOR-DUPLIKAT (1 Nov 2026): dulu SELECT tanpa display_name lalu row["display_name"] -> KeyError -> 500 tiap ADA
        # kecocokan; hanya vendor aktif (padahal nama nonaktif ikut memblok simpan, uq_vendors_tenant_name); % / _ di nama
        # jadi wildcard. Kini: kolom lengkap, nonaktif ikut (is_active), ILIKE ber-escape. Filter tenant tetap.
        async with pool.acquire() as conn:
            if nama and nama.strip():
                pola = "%" + nama.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
                rows = await conn.fetch(
                    """SELECT id, name, company_name, display_name, tax_id, is_active FROM vendors
                       WHERE tenant_id = $1 AND name ILIKE $2 ESCAPE '\\' AND ($3::uuid IS NULL OR id <> $3::uuid)
                       ORDER BY is_active DESC, name LIMIT 10""",
                    ctx["tenant_id"], pola, kecuali)
                by_name = [_baris(r) for r in rows]

            if npwp and npwp.strip():
                normalized_npwp = npwp.replace(".", "").replace("-", "").strip()
                rows = await conn.fetch(
                    """SELECT id, name, company_name, display_name, tax_id, is_active FROM vendors
                       WHERE tenant_id = $1 AND REPLACE(REPLACE(tax_id, '.', ''), '-', '') = $2
                         AND ($3::uuid IS NULL OR id <> $3::uuid)
                       ORDER BY is_active DESC, name LIMIT 10""",
                    ctx["tenant_id"], normalized_npwp, kecuali)
                by_npwp = [_baris(r) for r in rows]

        return {"byName": by_name, "byNpwp": by_npwp}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error checking vendor duplicates: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Duplicate check failed")


# =============================================================================
# LIST VENDORS
# =============================================================================
def _syarat_vendor(tid: str, search: Optional[str], is_active: Optional[bool], is_pkp: Optional[bool],
                   has_overdue: Optional[bool], hari_ini) -> tuple:
    """Syarat daftar vendor -- SATU pembangun untuk GET /vendors dan /vendors/summary (predikat sama). -> (where, params, idx)."""
    # Build query conditions.
    # SEMUA kueri di bawah memakai alias tabel `v` (FROM vendors v),
    # jadi setiap kondisi ditulis EKSPLISIT ber-prefiks v. — dulu
    # WHERE dialias lewat str.replace("tenant_id", "v.tenant_id")
    # yang juga merusak fragmen has_overdue (vendors.tenant_id ->
    # vendors.v.tenant_id) -> 500 saat has_overdue+sort ap_balance.
    conditions = ["v.tenant_id = $1"]
    params = [tid]
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


    # Filter by is_pkp (PKP vendors only)
    if is_pkp is True:
        conditions.append("v.is_pkp = true")

    # Pure Ledger: has_overdue filter using compute_ap_outstanding() DB function
    if has_overdue is True:
        # #10b-3a: hari ini = tanggal bisnis tenant, bukan UTC
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

    return " AND ".join(conditions), params, param_idx


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
    with_totals: bool = Query(False, description="B1: sum_sisa (Σ hutang hasil filter) + urut nama, id"),
    aksi: Optional[Literal["lewat_jatuh_tempo", "punya_hutang"]] = Query(None, description="B1: saring aksi (kontak_daftar)"),
):
    """
    List vendors with search, filtering, and pagination.
    B1 (4 Nov 2026): tanpa with_totals/aksi = perilaku lama (+ medan aditif); has_balance kini dari JURNAL (aksi punya_hutang,
    dulu COALESCE(v.opening_balance, 0) > 0); saldo / lewat tempo lewat services/kontak_daftar (satu penentu dengan pelanggan).

    **Search:** Matches name, code, contact_person, or phone.
    **Filter:** Use is_active=true to show only active vendors.
    """
    # dipanggil langsung (tes/agen) tanpa FastAPI: default Query(...) -> anggap tak dikirim
    with_totals = with_totals is True
    aksi = aksi if isinstance(aksi, str) else None
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            hari_ini = await tanggal_dokumen(conn, ctx["tenant_id"])
            where_clause, params, param_idx = _syarat_vendor(ctx["tenant_id"], search, is_active, is_pkp, has_overdue, hari_ini)
            # has_balance lama -> predikat JURNAL yang SAMA dengan ?aksi=punya_hutang (dulu COALESCE(v.opening_balance, 0) > 0)
            aksi_saring = aksi or ("punya_hutang" if has_balance is True else None)

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

            sum_sisa = None
            if with_totals or aksi_saring:  # B1: filter aksi + total/sum_sisa dari penentu bersama
                urut = None
                if not (with_totals or aksi):  # has_balance lama: urutan lama dipertahankan
                    urut = f"COALESCE(x.saldo, 0) {sort_dir}" if use_ap_balance_sort else f"v.{sort_field} {sort_dir}"
                h = await KD.halaman(conn, ctx["tenant_id"], "vendor", "v", where_clause, params, param_idx, hari_ini,
                                     skip=skip, limit=limit, aksi=aksi_saring, with_totals=with_totals, urut=urut)
                total, sum_sisa = h["total"], h.get("sum_sisa")
                pos = {str(i): n for n, i in enumerate(h["ids"])}
                rows = sorted(await conn.fetch(
                    """SELECT id, code, name, company_name, display_name, contact_person, phone, email, city,
                              payment_terms_days, is_active, created_at, is_pkp, default_tax_code FROM vendors WHERE tenant_id = $1 AND id = ANY($2::uuid[])""",
                    ctx["tenant_id"], h["ids"]), key=lambda r: pos[str(r["id"])])
            else:
                # Get total count
                count_query = f"SELECT COUNT(*) FROM vendors v WHERE {where_clause}"
                total = await conn.fetchval(count_query, *params)

                # Get items - handle ap_balance sorting with subquery
                if use_ap_balance_sort:
                    query = f"""
                        SELECT v.id, v.code, v.name, v.company_name, v.display_name, v.contact_person, v.phone, v.email, v.city,
                               v.payment_terms_days, v.is_active, v.created_at, v.is_pkp, v.default_tax_code,
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
                        SELECT id, code, name, company_name, display_name, contact_person, phone, email, city,
                               payment_terms_days, is_active, created_at, is_pkp, default_tax_code
                        FROM vendors v
                        WHERE {where_clause}
                        ORDER BY {sort_field} {sort_dir}
                        LIMIT ${param_idx} OFFSET ${param_idx + 1}
                    """
                params.extend([limit, skip])

                rows = await conn.fetch(query, *params)


            # B1: saldo / lewat tempo dari JURNAL lewat kontak_daftar (satu penentu dengan pelanggan)
            vendor_ids = [str(row["id"]) for row in rows]
            fakta = await KD.fakta_kontak(conn, ctx["tenant_id"], "vendor", vendor_ids, hari_ini)

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
                    "is_pkp": row["is_pkp"],  # BUG-088: bawaan pajak vendor untuk form PO/FPB (== GET /{id})
                    "default_tax_code": row["default_tax_code"],
                    "ap_balance": KD.medan_baris("vendor", row, fakta)["saldo"],
                    **KD.medan_baris("vendor", row, fakta),
                }
                for row in rows
            ]

            out = {"items": items, "total": total, "has_more": (skip + limit) < total}
            if with_totals:
                out["sum_sisa"] = sum_sisa
            return out

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing vendors: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list vendors")


# =============================================================================
# GET VENDOR DETAIL
# =============================================================================
@router.get("/summary")
async def vendors_summary(request: Request, search: Optional[str] = Query(None), is_active: Optional[bool] = Query(None),
                          is_pkp: Optional[bool] = Query(None), has_overdue: Optional[bool] = Query(None)):
    """B1: ringkasan daftar vendor -- predikat SAMA dengan GET /vendors (_syarat_vendor + kontak_daftar). SEBELUM /{vendor_id}."""
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        hari_ini = await tanggal_dokumen(conn, ctx["tenant_id"])
        where, params, idx = _syarat_vendor(ctx["tenant_id"], search, is_active, is_pkp, has_overdue, hari_ini)
        data = await KD.ringkasan(conn, ctx["tenant_id"], "vendor", "v", where, params, idx, hari_ini)
    return {"success": True, "data": data}


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


# Jejak transaksi yang menolak hapus vendor -- SATU daftar untuk DELETE (smart_delete) dan aksi detail B2 (cek_jejak)
JEJAK_VENDOR = [  # lengkap = SEMUA tabel ber-FK NO ACTION ke tabel ini (+ dokumen tanpa FK); diukur pg_constraint 9 Nov 2026 -- yang
    # terlewat dulu membuat DELETE 500 (FK) atau meninggalkan dokumen yatim. Penjaga: test_kontak_aksi_pratinjau.test_jejak_hapus_lengkap
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
        (
            "purchase_orders",
            "vendor_id",
            "Tidak bisa menghapus vendor yang memiliki pesanan pembelian. Nonaktifkan saja.",
        ),
        (
            "bill_payments_v2",
            "vendor_id",
            "Tidak bisa menghapus vendor yang memiliki pembayaran keluar. Nonaktifkan saja.",
        ),
        (
            "goods_receipts",
            "vendor_id",
            "Tidak bisa menghapus vendor yang memiliki penerimaan barang. Nonaktifkan saja.",
        ),
        (
            "expenses",
            "vendor_id",
            "Tidak bisa menghapus vendor yang memiliki biaya. Nonaktifkan saja.",
        ),
        (
            "recurring_bills",
            "vendor_id",
            "Tidak bisa menghapus vendor yang memiliki tagihan berulang. Nonaktifkan saja.",
        ),
        (
            "cheques",
            "vendor_id",
            "Tidak bisa menghapus vendor yang memiliki cek/giro. Nonaktifkan saja.",
        ),
        (
            "fixed_assets",
            "vendor_id",
            "Tidak bisa menghapus vendor yang memiliki aset tetap. Nonaktifkan saja.",
        ),
        (
            "asset_maintenance",
            "vendor_id",
            "Tidak bisa menghapus vendor yang memiliki catatan pemeliharaan aset. Nonaktifkan saja.",
        ),
        (
            "production_subcontracts",
            "vendor_id",
            "Tidak bisa menghapus vendor yang memiliki subkontrak produksi. Nonaktifkan saja.",
        ),
        (
            "bom_operations",
            "vendor_id",
            "Tidak bisa menghapus vendor yang memiliki operasi BOM. Nonaktifkan saja.",
        ),
    ]


async def saldo_awal_vendor(conn, tid: str, vendor_id) -> Optional[str]:
    """Nomor saldo awal vendor yang masih berlaku (tagihan pembuka tak-void, atau jurnal VENDOR_OPENING_BALANCE lama belum dibalik);
    None = belum ada. SATU pemeriksa untuk POST /opening-balance dan aksi detail B2."""
    ada = await conn.fetchval(
        "SELECT invoice_number FROM bills WHERE tenant_id = $1 AND vendor_id = $2 AND is_opening_balance AND status_v2 <> 'void'",
        tid, vendor_id)
    return ada or await conn.fetchval(  # jurnal VENDOR_OPENING_BALANCE lama (terbalik) yang belum dibalik
        """SELECT journal_number FROM journal_entries WHERE tenant_id = $1 AND source_type = 'VENDOR_OPENING_BALANCE' AND source_id = $2
           AND status = 'POSTED' AND reversed_by_id IS NULL AND reversal_of_id IS NULL""", tid, vendor_id)


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

            # B2 (4 Nov 2026): saldo / lewat tempo = kontak_daftar (satu penentu dengan daftar + pelanggan); total pembelian TURUNAN
            # JURNAL (services/vendor_pembelian); aksi boleh/alasan dari pemeriksa penulis (cek_jejak, saldo_awal_vendor).
            # Kredit vendor TIDAK dikirim (belum ada penentu jurnal).
            _hari = await tanggal_dokumen(conn, ctx["tenant_id"])
            _medan = KD.medan_baris("vendor", row, await KD.fakta_kontak(conn, ctx["tenant_id"], "vendor", [str(row["id"])], _hari))
            from ..services.vendor_pembelian import pembelian_vendor
            from ..services.smart_delete import cek_jejak
            _beli = (await pembelian_vendor(conn, ctx["tenant_id"], [row["id"]]))[str(row["id"])]
            from ..services.uang_muka_vendor import sisa_vendor
            try:
                _uang_muka = KD.uang_json(await sisa_vendor(conn, ctx["tenant_id"], row["id"]))  # sisa jurnal AP_PREPAID
            except HTTPException:  # peran Uang Muka Pembelian belum dipetakan -> medan null (bukan 0 palsu)
                _uang_muka = None
            _aksi = KDT.aksi_detail(row["is_active"], await cek_jejak(conn, str(row["id"]), row["name"], JEJAK_VENDOR),
                                    await saldo_awal_vendor(conn, ctx["tenant_id"], row["id"]))

            shipping_address, contacts = await _baca_anak_pemasok(conn, ctx["tenant_id"], vendor_id)

            _hasil = {
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
                    "ap_balance": _medan["saldo"],
                    "saldo": _medan["saldo"],
                    "lewat_tempo_nilai": _medan["lewat_tempo_nilai"],
                    "lewat_tempo_jumlah": _medan["lewat_tempo_jumlah"],
                    **_beli,
                    "uang_muka": _uang_muka,
                    "jumlah_dokumen": _beli["jumlah_tagihan"],
                    "kontak_teks": _medan["kontak_teks"],
                    "status_text": _medan["status_text"],
                    "tipe_text": KDT.tipe_text(row["vendor_type"]),
                    "termin_text": KDT.termin_text(row["payment_terms_days"]),
                    "aksi": _aksi,
                    "aksi_tersedia": KDT.aksi_tersedia(_aksi),
                },
            }
            # K2b: agregat dokumen hanya untuk yang boleh BACA modulnya (pola omitted B4; medan null, bukan 0 palsu)
            from ..services.dashboard_izin import boleh_baca
            await KDT.saring_agregat("vendor", _hasil["data"], lambda m: boleh_baca(request, m))
            return _hasil

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


PESAN_SALDO_AWAL_SAAT_BUAT = ("Saldo awal hutang tidak bisa diisi saat membuat vendor. "
                              "Simpan vendor dulu, lalu isi lewat Saldo awal di halaman vendor.")


async def _siapkan_buat_vendor(conn, tid: str, body: CreateVendorRequest) -> None:
    """Aturan buat vendor (BACA SAJA; galat = HTTPException SAMA dengan POST /vendors). Dipakai POST /vendors (sesudah kunci nama/kode)
    DAN POST /vendors/preview. VENDOR-DUPLIKAT: nama/kode ganda -> 409 berkode (predikat == indeks unik DB; nonaktif ikut)."""
    if (getattr(body, "opening_balance", 0) or 0) > 0:
        raise HTTPException(status_code=422, detail=PESAN_SALDO_AWAL_SAAT_BUAT)
    await VU.tolak_nama_dipakai(conn, tid, body.name)
    await VU.tolak_kode_dipakai(conn, tid, body.code)


@router.post("/preview")
async def preview_create_vendor(request: Request, body: dict):
    """Pratinjau BUAT vendor (K 2a, 9 Nov 2026; kembar POST /customers/preview): penentu SAMA dengan POST /vendors (_siapkan_buat_vendor),
    transaksi READ ONLY, nol tulis. 200 {success, data:{ok, blocks[{code,message,field?,id?}], rencana{...}, payload}}. Badan = badan POST
    byte-identik; galat skema -> blok DATA_TIDAK_SAH (bukan 422)."""
    from pydantic import ValidationError
    from .customers import blok_validasi, _pratinjau_baca
    ctx = get_user_context(request)
    payload = dict(body or {})
    try:
        cb = CreateVendorRequest.model_validate(payload)
    except ValidationError as e:
        return {"success": True, "data": {"ok": False, "blocks": blok_validasi(e), "rencana": None, "payload": payload}}
    pool = await get_pool()
    async with pool.acquire() as conn:
        bloks = await _pratinjau_baca(conn, lambda: _siapkan_buat_vendor(conn, ctx["tenant_id"], cb))
    rencana = {"name": cb.name, "code": cb.code, "vendor_type": cb.vendor_type, "payment_terms_days": cb.payment_terms_days,
               "efek": []}  # nol jurnal: saldo awal lewat rute terpisah
    return {"success": True, "data": {"ok": not bloks, "blocks": bloks, "rencana": rencana, "payload": payload}}


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
        if (getattr(body, "opening_balance", 0) or 0) > 0:  # tanpa koneksi bila badan sudah pasti ditolak (galat SAMA dgn penentu)
            raise HTTPException(status_code=422, detail=PESAN_SALDO_AWAL_SAAT_BUAT)
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
                # VENDOR-DUPLIKAT: nama/kode ganda -> 409 berkode (predikat == indeks unik DB; nonaktif ikut)
                await _siapkan_buat_vendor(conn, ctx["tenant_id"], body)

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
UBAH_LABEL = {  # label riwayat PATCH vendor -- SATU sumber untuk aktivitas dan pratinjau ubah
    "name": "Nama", "code": "Kode", "contact_person": "Kontak", "phone": "Telepon", "email": "Email", "address": "Alamat", "city": "Kota",
    "province": "Provinsi", "postal_code": "Kode Pos", "tax_id": "NPWP", "payment_terms_days": "Termin Pembayaran",
    "credit_limit": "Limit Kredit", "notes": "Catatan", "vendor_type": "Tipe Vendor", "company_name": "Nama Perusahaan",
    "display_name": "Nama Tampilan", "mobile_phone": "HP", "website": "Website", "is_pkp": "PKP", "currency": "Mata Uang",
    "bank_name": "Bank", "bank_account_number": "No Rekening", "bank_account_holder": "Pemilik Rekening", "is_active": "Status Aktif",
    "nik": "NIK", "account_number": "No Akun", "tax_address": "Alamat Pajak", "tax_city": "Kota Pajak", "tax_province": "Provinsi Pajak",
    "tax_postal_code": "Kode Pos Pajak", "nitku": "NITKU", "negara": "Negara", "default_tax_code": "Kode Pajak Bawaan",
    "default_pph_type": "Jenis PPh Bawaan", "default_pph_rate": "Tarif PPh Bawaan",
}


async def _siapkan_ubah_vendor(conn, tid: str, vendor_id, body: UpdateVendorRequest, kunci: bool = True) -> tuple:
    """Aturan PATCH vendor (galat = HTTPException yang sama dengan tulis). -> (baris sekarang, update_data kolom, anak_dikirim).
    Dipakai PATCH /{id} (kunci=True, di dalam transaksinya) DAN POST /{id}/edit/preview (kunci=False, READ ONLY)."""
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
        tid,
    )
    if not existing:
        raise HTTPException(status_code=404, detail="Vendor not found")
    # VENDOR-DUPLIKAT: nama/kode yang BERUBAH -> kunci (kembar buat) lalu 409 berkode bila dipakai vendor lain.
    # Dulu kode tak dicek sama sekali -> indeks unik DB -> 500.
    if body.name and body.name != existing["name"]:
        if kunci:
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"VENDOR_NAMA:{tid}:{body.name.strip().lower()}")
        await VU.tolak_nama_dipakai(conn, tid, body.name, kecuali_id=vendor_id)
    if body.code and body.code != existing["code"]:
        if kunci:
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"VENDOR_KODE:{tid}:{body.code.strip().lower()}")
        await VU.tolak_kode_dipakai(conn, tid, body.code, kecuali_id=vendor_id)
    update_data = body.model_dump(exclude_unset=True)
    # Anak (alamat pengiriman, kontak) bukan kolom vendors: ditulis terpisah di transaksi yang sama.
    anak_dikirim = {k for k in ("shipping_address", "contacts") if k in update_data}
    for k in ("shipping_address", "contacts", "contacts_replace"):
        update_data.pop(k, None)
    if update_data.get("nik"):
        tipe_efektif = update_data.get("vendor_type") or existing["vendor_type"]
        if tipe_efektif != "ORANG_PRIBADI":
            raise HTTPException(status_code=422, detail="NIK hanya untuk pemasok Orang Pribadi")
    return existing, update_data, anak_dikirim


def perubahan_vendor(existing, update_data: dict) -> list:
    """[{field, label, lama, baru}] kolom yang nilainya BERUBAH (aturan banding SAMA dengan riwayat 'X diubah')."""
    return [{"field": f, "label": UBAH_LABEL.get(f, f), "lama": existing.get(f) if existing else None, "baru": v}
            for f, v in update_data.items() if (existing.get(f) if existing else None) != v]


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
                existing, update_data, anak_dikirim = await _siapkan_ubah_vendor(conn, ctx["tenant_id"], vendor_id, body)
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

                changes_logged = []
                for x in perubahan_vendor(existing, update_data):
                    old_display = str(x["lama"]) if x["lama"] is not None else "-"
                    new_display = str(x["baru"]) if x["baru"] is not None else "-"
                    # Log each change
                    await conn.execute(
                        """
                    INSERT INTO vendor_activities
                    (vendor_id, tenant_id, type, description, field_name, old_value, new_value, actor_id, actor_name)
                    VALUES ($1, $2, 'updated', $3, $4, $5, $6, $7, $8)
                    """,
                        vendor_id,
                        ctx["tenant_id"],
                        f"{x['label']} diubah",
                        x["field"],
                        old_display,
                        new_display,
                        user_id,
                        user_name,
                    )
                    changes_logged.append(f"{x['label']}: {old_display} -> {new_display}")

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


@router.post("/{vendor_id}/edit/preview")
async def preview_update_vendor(request: Request, vendor_id: UUID, body: dict):
    """Pratinjau UBAH vendor (K-B, 9 Nov 2026): penentu SAMA dengan PATCH /{id} (_siapkan_ubah_vendor; exclude_unset, null = kosongkan;
    If-Match opt-in), READ ONLY tanpa kunci, nol tulis. 200 {ok, blocks, perubahan[{field,label,lama,baru}], catatan[...], payload}.
    catatan = pesan rename-impact bila nama berubah (KDT.dampak_ganti_nama) + anak (alamat pengiriman / kontak) yang dikirim."""
    from pydantic import ValidationError
    from ..services.optimistic_concurrency import assert_if_match_row
    from .customers import blok_validasi, _pratinjau_baca
    ctx = get_user_context(request)
    payload = dict(body or {})
    try:
        ub = UpdateVendorRequest.model_validate(payload)
    except ValidationError as e:
        return {"success": True, "data": {"ok": False, "blocks": blok_validasi(e), "perubahan": [], "catatan": [], "payload": payload}}
    hasil = {}
    pool = await get_pool()
    async with pool.acquire() as conn:
        async def kerja():
            hasil["cur"], hasil["data"], hasil["anak"] = await _siapkan_ubah_vendor(conn, ctx["tenant_id"], vendor_id, ub, kunci=False)
            await assert_if_match_row(request, "vendors", vendor_id)
        bloks = await _pratinjau_baca(conn, kerja)
        perubahan = [{**x, "lama": KDT.nilai_json(x["lama"]), "baru": KDT.nilai_json(x["baru"])}
                     for x in perubahan_vendor(hasil.get("cur"), hasil.get("data") or {})]
        catatan = []
        if any(x["field"] == "name" for x in perubahan):
            dampak = await KDT.dampak_ganti_nama(conn, ctx["tenant_id"], "vendor", vendor_id)
            if dampak and dampak["pesan"]:
                catatan.append(dampak["pesan"])
        catatan += [ANAK_TEKS[k] for k in ("shipping_address", "contacts") if k in hasil.get("anak", ())]
    return {"success": True, "data": {"ok": not bloks, "blocks": bloks, "perubahan": perubahan, "catatan": catatan, "payload": payload}}


ANAK_TEKS = {"shipping_address": "Alamat pengiriman ikut disimpan.", "contacts": "Daftar kontak ikut disimpan."}


@router.get("/{vendor_id}/rename-impact")
async def get_vendor_rename_impact(request: Request, vendor_id: UUID):
    """Jumlah dokumen yang menyimpan nama vendor ini sebagai snapshot (kembar /customers/{id}/rename-impact; penentu
    KDT.dampak_ganti_nama). Hanya baca."""
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        data = await KDT.dampak_ganti_nama(conn, ctx["tenant_id"], "vendor", vendor_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Vendor not found")
    return {"success": True, "data": data}


@router.post("/{vendor_id}/status/preview")
async def preview_vendor_status(request: Request, vendor_id: UUID,
                                status: Literal["active", "inactive"] = Query(..., description="Status baru")):
    """Pratinjau PATCH /{id}/status: penulisnya tanpa syarat selain vendor ada. 200 {ok, blocks, rencana{is_active}}; 404."""
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        ada = await conn.fetchval("SELECT 1 FROM vendors WHERE id = $1 AND tenant_id = $2", vendor_id, ctx["tenant_id"])
    if not ada:
        raise HTTPException(status_code=404, detail="Vendor not found")
    return {"success": True, "data": {"ok": True, "blocks": [], "rencana": {"is_active": status == "active"}}}


@router.post("/{vendor_id}/delete/preview")
async def preview_delete_vendor(request: Request, vendor_id: UUID):
    """Pratinjau HAPUS: cek_jejak YANG SAMA dengan DELETE (smart_delete, JEJAK_VENDOR), nol tulis. 200 {ok, blocks, rencana{id, nama}}; 404."""
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        data = await KDT.pratinjau_hapus(conn, ctx["tenant_id"], "vendor", vendor_id, JEJAK_VENDOR)
    if data is None:
        raise HTTPException(status_code=404, detail="Vendor not found")
    return {"success": True, "data": data}


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
                    ledger_checks=JEJAK_VENDOR,
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
            from ..schemas.uang_json import _uang_json as uang  # Law 9: dulu int() memotong sen
            bills = [
                {
                    "id": str(row["bill_id"]),
                    "bill_number": row["bill_number"],
                    "bill_date": row["bill_date"].isoformat(),
                    "due_date": row["due_date"].isoformat(),
                    "total_amount": uang(row["total_amount"]),
                    "paid_amount": uang(row["paid_amount"] or 0),
                    "remaining_amount": uang(row["remaining_amount"]),
                    "is_overdue": row["is_overdue"],
                }
                for row in rows
            ]
            total_outstanding = uang(sum((r["remaining_amount"] for r in rows), Decimal("0")))
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
            ada = await saldo_awal_vendor(conn, ctx["tenant_id"], vendor["id"])
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


# =============================================================================
# SA-KOREKSI (25 Okt 2026): BALIK saldo awal vendor CARA LAMA (jurnal VENDOR_OPENING_BALANCE langsung, arah TERBALIK Dr Hutang, V457)
# =============================================================================
# Aturan DIKONFIRMASI LANGSUNG pemilik 25 Okt: koreksi saldo awal = batalkan lalu buat baru; saldo awal cara lama dibalik dengan JURNAL PEMBALIK
# (jurnal lama TETAP ada sebagai jejak, ditandai reversed_by_id; Law 2). Pintu umum POST /journals/{id}/reverse menolak VENDOR_OPENING_BALANCE
# ("perbaiki lewat modul saldo awal") -- inilah pintunya. Pembalik ber-source_type REVERSAL (whitelist pemicu guard_arap_requires_obligation:
# kredit Hutang tanpa tagihan ditolak untuk source_type lain), reversal_of_id = jurnal lama, source_id = vendor. compute_ap_outstanding (ob_rows)
# membuang jurnal yang dibalik -> saldo awal lama vendor = 0; sesudahnya saldo awal baru lewat POST /{id}/opening-balance (tagihan pembuka).


async def _rencana_balik_saldo_awal_lama(conn, tenant_id: str, vid) -> dict:
    """SATU penentu (pratinjau READ ONLY dan tulis di dalam kunci). NOL tulisan. -> {vendor, journals:[{id,number,date,lines}], blocks}."""
    vendor = await conn.fetchrow("SELECT id, name FROM vendors WHERE id = $1 AND tenant_id = $2", vid, tenant_id)
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")
    jurnal = await conn.fetch(
        """SELECT id, journal_number, journal_date FROM journal_entries WHERE tenant_id = $1 AND source_type = 'VENDOR_OPENING_BALANCE'
             AND source_id = $2 AND status = 'POSTED' AND reversed_by_id IS NULL AND reversal_of_id IS NULL ORDER BY journal_date, journal_number""",
        tenant_id, vid)
    hasil = []
    for j in jurnal:
        baris = await conn.fetch(
            """SELECT jl.account_id, coa.account_code, coa.name AS account_name, jl.debit, jl.credit, jl.memo, jl.line_number
               FROM journal_lines jl JOIN chart_of_accounts coa ON coa.id = jl.account_id WHERE jl.journal_id = $1 ORDER BY jl.line_number""", j["id"])
        hasil.append({"id": j["id"], "number": j["journal_number"], "date": j["journal_date"], "lines": [dict(b) for b in baris]})
    blok = [] if hasil else [{"code": "TAK_ADA_SALDO_AWAL_LAMA",
                              "message": f"Vendor {vendor['name']} tidak punya saldo awal cara lama yang perlu dibalik."}]
    return {"vendor": vendor, "journals": hasil, "blocks": blok}


def _bentuk_balik(r: dict) -> dict:
    return {"vendor_id": str(r["vendor"]["id"]), "vendor_name": r["vendor"]["name"],
            "journals": [{"journal_id": str(j["id"]), "journal_number": j["number"], "journal_date": j["date"].isoformat(),
                          "reversal_lines": [{"account_code": b["account_code"], "account_name": b["account_name"],
                                              "debit": f"{Decimal(str(b['credit'])):.2f}", "credit": f"{Decimal(str(b['debit'])):.2f}"}
                                             for b in j["lines"]]} for j in r["journals"]]}


@router.post("/{vendor_id}/opening-balance/legacy/reverse/preview")
async def preview_reverse_legacy_opening_balance(request: Request, vendor_id: UUID):
    """Pratinjau balik saldo awal cara lama: penentu SAMA dengan tulis, transaksi READ ONLY tanpa kunci, NOL tulisan. 200 walau diblok."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        async with pool.acquire() as conn, conn.transaction(readonly=True):
            r = await _rencana_balik_saldo_awal_lama(conn, ctx["tenant_id"], vendor_id)
        return {"success": True, "data": {"ok": not r["blocks"], **_bentuk_balik(r), "blocks": r["blocks"]}}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error previewing legacy OB reverse {vendor_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Gagal menampilkan pratinjau pembalikan saldo awal.")


@router.post("/{vendor_id}/opening-balance/legacy/reverse")
async def reverse_legacy_opening_balance(request: Request, vendor_id: UUID, response: Response = None):
    """Balik SEMUA jurnal saldo awal cara lama vendor ini (jurnal pembalik REVERSAL, DRAFT -> POSTED, jurnal lama ditandai reversed_by_id).
    Idem VENDOR_OB_LAMA_BALIK; kunci VENDOR:{tenant}:{id} + VENDOR_OPENING:{id} (SAMA dengan POST /{id}/opening-balance); penentu diulang
    DI DALAM kunci. Blok -> 409."""
    try:
        ctx = get_user_context(request)
        if not ctx.get("user_id"):
            raise HTTPException(status_code=401, detail="ID pengguna wajib ada.")
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                kp, sidik, lama = await idem_buat.mulai_aksi(conn, ctx, idem_buat.kunci_dari(request), "VENDOR_OB_LAMA_BALIK", vendor_id, {},
                                                             response)
                if lama is not None:
                    return lama
                await _kunci_vendor(conn, ctx["tenant_id"], vendor_id, saldo_awal=True)
                r = await _rencana_balik_saldo_awal_lama(conn, ctx["tenant_id"], vendor_id)
                if r["blocks"]:
                    raise HTTPException(status_code=409, detail=r["blocks"][0])
                tanggal = await tanggal_dokumen(conn, ctx["tenant_id"])
                dibalik = []
                for j in r["journals"]:
                    total = sum(Decimal(str(b["debit"])) for b in j["lines"])
                    nomor = await conn.fetchval("SELECT get_next_journal_number($1, $2, $3)", ctx["tenant_id"], "REV", tanggal)
                    rid = await conn.fetchval(
                        """INSERT INTO journal_entries (tenant_id, journal_number, journal_date, description, source_type, source_id, total_debit,
                                                        total_credit, status, created_by, reversal_of_id, reversal_reason)
                           VALUES ($1, $2, $3, $4, 'REVERSAL', $5, $6, $6, 'DRAFT', $7, $8, $9) RETURNING id""",
                        ctx["tenant_id"], nomor, tanggal, f"Balik saldo awal lama {j['number']} - {r['vendor']['name']}", vendor_id, total,
                        UUID(str(ctx["user_id"])), j["id"], "Koreksi saldo awal vendor cara lama")
                    for i, b in enumerate(j["lines"], 1):
                        await conn.execute(
                            "INSERT INTO journal_lines (journal_id, account_id, debit, credit, memo, line_number) VALUES ($1, $2, $3, $4, $5, $6)",
                            rid, b["account_id"], b["credit"], b["debit"], f"BALIK: {b['memo'] or j['number']}", i)
                    await conn.execute("UPDATE journal_entries SET status = 'POSTED' WHERE id = $1", rid)  # Law 20
                    await conn.execute("UPDATE journal_entries SET reversed_by_id = $1, reversed_at = NOW() WHERE id = $2 AND reversed_by_id IS NULL",
                                       rid, j["id"])
                    dibalik.append({"journal_number": j["number"], "reversal_journal_id": str(rid), "reversal_journal_number": nomor})
                user = request.state.user
                aktor = user.get("fullname") or user.get("username") or user.get("email") or "System"
                await conn.execute(
                    """INSERT INTO vendor_activities (vendor_id, tenant_id, type, description, actor_id, actor_name, metadata)
                       VALUES ($1, $2, 'opening_balance_reversed', $3, $4, $5, $6::jsonb)""",
                    vendor_id, ctx["tenant_id"], "Saldo awal cara lama dibalik", ctx["user_id"], aktor, json.dumps({"dibalik": dibalik}))
                await conn.execute(
                    """INSERT INTO audit_logs (id, "eventType", entity_type, entity_id, entity_number, tenant_id, source, metadata, success, "createdAt")
                       VALUES (gen_random_uuid()::text, 'VENDOR_OPENING_BALANCE_REVERSED', 'vendor', $1, $2, $3, 'api:vendors.ob_legacy_reverse',
                               jsonb_build_object('dibalik', $4::jsonb, 'user_id', $5::text), true, now())""",
                    str(vendor_id), r["vendor"]["name"], ctx["tenant_id"], json.dumps(dibalik), str(ctx["user_id"]))
                resp = {"success": True, "message": "Saldo awal cara lama dibalik.",
                        "data": {**_bentuk_balik(r), "reversed": dibalik}}
                return await idem_buat.simpan(conn, ctx, kp, sidik, "VENDOR_OB_LAMA_BALIK", resp, vendor_id) if kp else resp
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error reversing legacy OB {vendor_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Gagal membalik saldo awal cara lama.")


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


@router.get("/{vendor_id}/history")
async def get_vendor_history(request: Request, vendor_id: UUID, limit: int = Query(200, ge=1, le=500)):
    """B4: riwayat bentuk /sales-orders/{id}/history (services/kontak_riwayat, sumber SAMA dengan /activity); dokumen tersaring
    izin BACA pemanggil -> omitted."""
    from ..services.dashboard_izin import boleh_baca
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        if not await conn.fetchval("SELECT 1 FROM vendors WHERE id = $1 AND tenant_id = $2", vendor_id, ctx["tenant_id"]):
            raise HTTPException(status_code=404, detail="Vendor not found")
        data = await KR.riwayat(conn, ctx["tenant_id"], "vendor", vendor_id, lambda m: boleh_baca(request, m), limit)
    return {"success": True, "data": data}


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
        # B4 (4 Nov 2026): sumber BERSAMA dengan GET /vendor_id/history (services/kontak_riwayat)
        rows, total = await KR.baris(conn, "vendor", vendor_id, ctx["tenant_id"], limit, offset)

        activities = [
            VendorActivity(
                id=str(row["id"]),
                type=row["type"],
                description=row["description"],
                details=row.get("details"),
                actor_name=row.get("actor_name"),
                timestamp=row["timestamp"].isoformat() if row["timestamp"] else None,
                document_number=row.get("document_number"),
                total=KR.total_aktivitas(row),
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
