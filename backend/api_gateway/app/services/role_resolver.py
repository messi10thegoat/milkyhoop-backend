"""
Role Resolver — Account Role Mapping Layer (Fase B)
====================================================

Provides semantic resolution of CoA account_id by role_key per tenant.
Infrastructure ONLY in Fase B — posting code unchanged. Existing posting
paths must NOT use this resolver yet; migration happens in Fase C.

Source of truth: docs/MAPPING-ROLE-AKUN-LOCKED.md

Iron Laws:
    Law 1, 18, 27 — runtime resolution, no hardcoded UUIDs, no header mapping.
    Law 24 — RLS-enforced via app.tenant_id session var.
    Law 32 — caller provides conn from get_db_pool(); resolver never acquires.

Usage:
    from app.services.role_resolver import (
        AccountRole, resolve_account_id_by_role, AccountRoleUnmappedError,
    )

    account_id = await resolve_account_id_by_role(
        conn, tenant_id, AccountRole.AR_TRADE
    )
"""

from __future__ import annotations

from typing import Final
from uuid import UUID


class AccountRoleUnmappedError(Exception):
    """Raised when a role_key has no account_roles mapping for the tenant.

    Posting MUST NOT proceed when this is raised — there is no silent
    fallback. The tenant must configure the mapping in account_roles.
    """


# -----------------------------------------------------------------------------
# Catalog — every role_key the system knows about.
# MUST stay in sync with CHECK constraint in V149__account_roles.sql.
# Seeded in Fase B = TIER 1 + TIER 2 (+ VAT_OUTPUT interim).
# Reserved (NOT seeded, awaiting Fase D / future modules) = TIER 3 + FUTURE.
# -----------------------------------------------------------------------------
class AccountRole:
    """Catalog of role_keys. String constants used as DB values."""

    # ---- TIER 1 — CONFIRMED (seeded Fase B) ---------------------------------
    CASH_GENERAL: Final[str] = "CASH_GENERAL"
    BANK_OPERATIONAL: Final[str] = "BANK_OPERATIONAL"
    AR_TRADE: Final[str] = "AR_TRADE"
    AR_OTHER: Final[str] = "AR_OTHER"
    INVENTORY_MERCHANDISE: Final[str] = "INVENTORY_MERCHANDISE"
    AP_TRADE: Final[str] = "AP_TRADE"
    CUSTOMER_DEPOSIT_LIABILITY: Final[str] = "CUSTOMER_DEPOSIT_LIABILITY"
    EQUITY_OPENING_BALANCE: Final[str] = "EQUITY_OPENING_BALANCE"
    REVENUE_SALES_GOODS: Final[str] = "REVENUE_SALES_GOODS"
    REVENUE_SALES_RETURN: Final[str] = "REVENUE_SALES_RETURN"
    COGS_SALES: Final[str] = "COGS_SALES"
    COGS_PURCHASE_RETURN: Final[str] = "COGS_PURCHASE_RETURN"
    # Fase C1.1 addendum (V151+V152): PSAK 72 contract liability (3-event model).
    REVENUE_DEFERRED: Final[str] = "REVENUE_DEFERRED"

    # ---- TIER 2 — CORRECTED (seeded Fase B) ---------------------------------
    CASH_PETTY: Final[str] = "CASH_PETTY"

    # ---- TIER 1 PROMOTED — TAX SPLIT Fase D1 (V155, seeded 5/5) -------------
    # VAT_OUTPUT was interim 2-10300 in Fase B; V155 repointed to dedicated
    # 2-10600 PPN Keluaran (is_interim=false). VAT_INPUT/WHT_PPH_PREPAID/
    # WHT_PPH_PAYABLE promoted from TIER 3 reservation.
    VAT_OUTPUT: Final[str] = "VAT_OUTPUT"
    VAT_INPUT: Final[str] = "VAT_INPUT"
    WHT_PPH_PAYABLE: Final[str] = "WHT_PPH_PAYABLE"
    WHT_PPH_PREPAID: Final[str] = "WHT_PPH_PREPAID"

    # ---- V156 D2-wrap B TIER 1 promote ---------------------------------------
    # AP_PREPAID  -> 1-10550 Uang Muka Pembelian (ASSET) — advance to vendor
    #                before bill received. Replaces legacy 1-10500 (AR_OTHER)
    #                literal in bill_payments.py.
    # PURCHASE_DISCOUNT -> 5-10200 Diskon Pembelian (COGS contra) — vendor
    #                cash / early-pay discount on bill payment.
    AP_PREPAID: Final[str] = "AP_PREPAID"
    PURCHASE_DISCOUNT: Final[str] = "PURCHASE_DISCOUNT"

    # ---- V158 D3.1 TIER 1 promote — manufaktur (MAPPED PENDING D3.2 seed) ----
    # WIP_GENERIC                  -> single WIP bucket; semua produksi unified.
    # COGS_VARIANCE_PRODUCTION     -> varian total (material+labor+overhead lumped).
    # WIP_SUBCONTRACT              -> biaya subkontrak/maklon.
    # INVENTORY_ADJUSTMENT_EXPENSE -> generic stock adjustment loss expense.
    WIP_GENERIC: Final[str] = "WIP_GENERIC"
    COGS_VARIANCE_PRODUCTION: Final[str] = "COGS_VARIANCE_PRODUCTION"
    WIP_SUBCONTRACT: Final[str] = "WIP_SUBCONTRACT"
    INVENTORY_ADJUSTMENT_EXPENSE: Final[str] = "INVENTORY_ADJUSTMENT_EXPENSE"

    # ---- V161 D4.1 TIER 1 promote — payroll (MAPPED PENDING D4.2 seed) -------
    # SALARY_EXPENSE       -> 5-20100 Beban Gaji (existing in seed_default_coa).
    # SALARY_PAYABLE       -> 2-10400 Utang Gaji (existing in seed_default_coa).
    # PPH21_PAYABLE        -> 2-10310 Utang PPh 21 (payroll-exclusive boundary;
    #                         LOCKED §"PPH 21 PAYROLL BOUNDARY"). NEVER via
    #                         WHT_PPH_PAYABLE (that points to 2-10320 = AP only).
    # BPJS_EE_PAYABLE      -> 2-10410 Utang BPJS Karyawan.
    # BPJS_ER_PAYABLE      -> 2-10420 Utang BPJS Perusahaan.
    # BPJS_ER_EXPENSE      -> 5-20150 Beban BPJS Perusahaan.
    # PPH21_ER_EXPENSE     -> 5-80100 Beban PPh 21 Perusahaan (nett method).
    SALARY_EXPENSE: Final[str] = "SALARY_EXPENSE"
    SALARY_PAYABLE: Final[str] = "SALARY_PAYABLE"
    PPH21_PAYABLE: Final[str] = "PPH21_PAYABLE"
    BPJS_EE_PAYABLE: Final[str] = "BPJS_EE_PAYABLE"
    BPJS_ER_PAYABLE: Final[str] = "BPJS_ER_PAYABLE"
    BPJS_ER_EXPENSE: Final[str] = "BPJS_ER_EXPENSE"
    PPH21_ER_EXPENSE: Final[str] = "PPH21_ER_EXPENSE"

    # ---- V165 Pre-Fase 6 Kas & Bank — BANK_FEE (MAPPED via V165) -------------
    # BANK_FEE -> 5-20850 Biaya Administrasi Bank (EXPENSE). Replaces
    # bank_transfers.py literal BANK_FEE_ACCOUNT = "5-20950" (non-existent code).
    BANK_FEE: Final[str] = "BANK_FEE"

    # ---- TIER 3 — PENDING (reserved, NOT seeded) ----------------------------
    VAT_INPUT_NONCREDITABLE: Final[str] = "VAT_INPUT_NONCREDITABLE"
    VAT_PAYABLE_NET: Final[str] = "VAT_PAYABLE_NET"
    # Granular WHT reservation (per-pasal, forward-compat, NOT mapped in D1).
    # Unified WHT_PPH_PAYABLE / WHT_PPH_PREPAID covers PPh 23/22/4(2) for now.
    WHT_PPH21: Final[str] = "WHT_PPH21"
    WHT_PPH23: Final[str] = "WHT_PPH23"
    WHT_PPH4_2: Final[str] = "WHT_PPH4_2"
    WHT_PPH22: Final[str] = "WHT_PPH22"
    ACCUMULATED_DEPRECIATION: Final[str] = "ACCUMULATED_DEPRECIATION"
    IC_SALES: Final[str] = "IC_SALES"
    BRANCH_AR: Final[str] = "BRANCH_AR"
    BRANCH_AP: Final[str] = "BRANCH_AP"

    # ---- FUTURE RESERVATION (not seeded; for forward-compat tenant mapping)-
    AR_ALLOWANCE: Final[str] = "AR_ALLOWANCE"
    AP_ACCRUED: Final[str] = "AP_ACCRUED"
    INVENTORY_RAW: Final[str] = "INVENTORY_RAW"
    INVENTORY_WIP: Final[str] = "INVENTORY_WIP"
    INVENTORY_FINISHED: Final[str] = "INVENTORY_FINISHED"
    INVENTORY_PACKAGING: Final[str] = "INVENTORY_PACKAGING"
    INVENTORY_WRITEOFF_DAMAGE: Final[str] = "INVENTORY_WRITEOFF_DAMAGE"
    INVENTORY_WRITEOFF_EXPIRED: Final[str] = "INVENTORY_WRITEOFF_EXPIRED"
    INVENTORY_WRITEOFF_SHRINKAGE: Final[str] = "INVENTORY_WRITEOFF_SHRINKAGE"
    INVENTORY_RECALL_LOSS: Final[str] = "INVENTORY_RECALL_LOSS"
    MFG_DIRECT_LABOR: Final[str] = "MFG_DIRECT_LABOR"
    MFG_OVERHEAD_INDIRECT_MATERIAL: Final[str] = "MFG_OVERHEAD_INDIRECT_MATERIAL"
    MFG_OVERHEAD_INDIRECT_LABOR: Final[str] = "MFG_OVERHEAD_INDIRECT_LABOR"
    MFG_OVERHEAD_UTILITIES: Final[str] = "MFG_OVERHEAD_UTILITIES"
    MFG_OVERHEAD_DEPRECIATION: Final[str] = "MFG_OVERHEAD_DEPRECIATION"
    MFG_OVERHEAD_APPLIED: Final[str] = "MFG_OVERHEAD_APPLIED"
    # ---- Deep-val 2.5 V173 — labor/OH applied liability clearing (TIER 1 promote) -
    MFG_LABOR_APPLIED: Final[str] = "MFG_LABOR_APPLIED"
    REVENUE_SALES_SERVICE: Final[str] = "REVENUE_SALES_SERVICE"
    REVENUE_SALES_DISCOUNT: Final[str] = "REVENUE_SALES_DISCOUNT"
    # REVENUE_DEFERRED — promoted to TIER 1 in Fase C1.1 addendum (see above).
    REVENUE_UNBILLED: Final[str] = "REVENUE_UNBILLED"
    COGS_PRODUCTION: Final[str] = "COGS_PRODUCTION"
    COGS_SERVICE: Final[str] = "COGS_SERVICE"
    COGS_VARIANCE_MATERIAL: Final[str] = "COGS_VARIANCE_MATERIAL"
    COGS_VARIANCE_LABOR: Final[str] = "COGS_VARIANCE_LABOR"
    COGS_VARIANCE_OVERHEAD: Final[str] = "COGS_VARIANCE_OVERHEAD"
    CURRENCY_GAIN: Final[str] = "CURRENCY_GAIN"
    CURRENCY_LOSS: Final[str] = "CURRENCY_LOSS"
    CURRENCY_UNREALIZED_FX: Final[str] = "CURRENCY_UNREALIZED_FX"

    # ---- V158 D3.1 RESERVED (forward-compat, NOT TIER 1, NOT seeded) --------
    # Granular manufaktur tier — keputusan D3.1: jangan split sekarang,
    # gunakan WIP_GENERIC. Reserved untuk future split per-cost-element.
    WIP_RAW: Final[str] = "WIP_RAW"
    WIP_LABOR: Final[str] = "WIP_LABOR"
    WIP_OVERHEAD: Final[str] = "WIP_OVERHEAD"
    # Finished goods — keputusan owner D3.1: TIDAK dipisah dari merchandise.
    # Semua FG -> INVENTORY_MERCHANDISE. Reserved untuk future MFG-only tenants.
    FG_FINISHED: Final[str] = "FG_FINISHED"
    # Writeoff (forward-compat farmasi/F&B) — alias singkat untuk future split.
    # Existing INVENTORY_WRITEOFF_* tetap di catalog.
    WRITEOFF_DAMAGE: Final[str] = "WRITEOFF_DAMAGE"
    WRITEOFF_EXPIRED: Final[str] = "WRITEOFF_EXPIRED"
    WRITEOFF_SHRINKAGE: Final[str] = "WRITEOFF_SHRINKAGE"


# Frozen catalog set — MUST equal V149 CHECK constraint exactly.
_CATALOG: Final[frozenset[str]] = frozenset(
    {
        # TIER 1
        "CASH_GENERAL",
        "BANK_OPERATIONAL",
        "AR_TRADE",
        "AR_OTHER",
        "INVENTORY_MERCHANDISE",
        "AP_TRADE",
        "CUSTOMER_DEPOSIT_LIABILITY",
        "EQUITY_OPENING_BALANCE",
        "REVENUE_SALES_GOODS",
        "REVENUE_SALES_RETURN",
        "COGS_SALES",
        "COGS_PURCHASE_RETURN",
        "REVENUE_DEFERRED",
        # TIER 1 promoted (V155 Fase D1 — tax split)
        "VAT_OUTPUT",
        "VAT_INPUT",
        "WHT_PPH_PAYABLE",
        "WHT_PPH_PREPAID",
        # TIER 1 promoted (V156 D2-wrap B)
        "AP_PREPAID",
        "PURCHASE_DISCOUNT",
        # TIER 1 promoted (V158 D3.1 — manufaktur, MAPPED PENDING D3.2)
        "WIP_GENERIC",
        "COGS_VARIANCE_PRODUCTION",
        "WIP_SUBCONTRACT",
        "INVENTORY_ADJUSTMENT_EXPENSE",
        # TIER 1 promoted (V161 D4.1 — payroll, MAPPED via D4.2)
        "SALARY_EXPENSE",
        "SALARY_PAYABLE",
        "PPH21_PAYABLE",
        "BPJS_EE_PAYABLE",
        "BPJS_ER_PAYABLE",
        "BPJS_ER_EXPENSE",
        "PPH21_ER_EXPENSE",
        # TIER 1 promoted (V165 — Pre-Fase 6 Kas & Bank)
        "BANK_FEE",
        # TIER 2
        "CASH_PETTY",
        # TIER 3 (reserved, NOT seeded)
        "VAT_INPUT_NONCREDITABLE",
        "VAT_PAYABLE_NET",
        # WHT granular reservation (forward-compat per Q2, NOT mapped in D1)
        "WHT_PPH21",
        "WHT_PPH23",
        "WHT_PPH4_2",
        "WHT_PPH22",
        "ACCUMULATED_DEPRECIATION",
        "IC_SALES",
        "BRANCH_AR",
        "BRANCH_AP",
        # FUTURE
        "AR_ALLOWANCE",
        "AP_ACCRUED",
        "INVENTORY_RAW",
        "INVENTORY_WIP",
        "INVENTORY_FINISHED",
        "INVENTORY_PACKAGING",
        "INVENTORY_WRITEOFF_DAMAGE",
        "INVENTORY_WRITEOFF_EXPIRED",
        "INVENTORY_WRITEOFF_SHRINKAGE",
        "INVENTORY_RECALL_LOSS",
        "MFG_DIRECT_LABOR",
        "MFG_OVERHEAD_INDIRECT_MATERIAL",
        "MFG_OVERHEAD_INDIRECT_LABOR",
        "MFG_OVERHEAD_UTILITIES",
        "MFG_OVERHEAD_DEPRECIATION",
        "MFG_OVERHEAD_APPLIED",
        "MFG_LABOR_APPLIED",  # V173 deep-val 2.5
        "REVENUE_SALES_SERVICE",
        "REVENUE_SALES_DISCOUNT",
        "REVENUE_UNBILLED",
        "COGS_PRODUCTION",
        "COGS_SERVICE",
        "COGS_VARIANCE_MATERIAL",
        "COGS_VARIANCE_LABOR",
        "COGS_VARIANCE_OVERHEAD",
        "CURRENCY_GAIN",
        "CURRENCY_LOSS",
        "CURRENCY_UNREALIZED_FX",
        # V158 D3.1 reserved (forward-compat, NOT seeded)
        "WIP_RAW",
        "WIP_LABOR",
        "WIP_OVERHEAD",
        "FG_FINISHED",
        "WRITEOFF_DAMAGE",
        "WRITEOFF_EXPIRED",
        "WRITEOFF_SHRINKAGE",
    }
)


def is_valid_role(role_key: str) -> bool:
    """Return True if role_key is in the known catalog."""
    return role_key in _CATALOG


async def resolve_account_id_by_role(conn, tenant_id: str, role_key: str) -> UUID:
    """Resolve account_id for a tenant's role mapping.

    Args:
        conn: asyncpg connection (caller-managed, RLS context set by middleware).
        tenant_id: tenant scope.
        role_key: catalog key (use AccountRole.* constants).

    Returns:
        account_id (UUID).

    Raises:
        ValueError: role_key not in catalog (typo / unknown).
        AccountRoleUnmappedError: role_key valid but no mapping configured —
            posting MUST abort, no silent fallback.
    """
    if not isinstance(role_key, str) or not role_key:
        raise ValueError(f"role_key must be non-empty str, got {role_key!r}")
    if role_key not in _CATALOG:
        raise ValueError(
            f"role_key {role_key!r} is not in account role catalog. "
            f"Add it to AccountRole and the V149 CHECK constraint before use."
        )

    row = await conn.fetchrow(
        "SELECT account_id FROM account_roles "
        "WHERE tenant_id = $1 AND role_key = $2",
        tenant_id,
        role_key,
    )
    if row is None:
        raise AccountRoleUnmappedError(
            f"Role {role_key!r} is not mapped for tenant {tenant_id!r}. "
            f"Posting cannot proceed. Configure mapping in account_roles "
            f"(see seed_default_account_roles or Fase D plan)."
        )
    return row["account_id"]


# -----------------------------------------------------------------------------
# PKP Toggle helper (V154/V155 Fase D1)
# -----------------------------------------------------------------------------
# VAT roles are gated by Tenant.is_pkp. Non-PKP tenants skip VAT line emission
# (sales/purchase without PPN). WHT roles are NOT affected by this toggle.
_VAT_ROLES: Final[frozenset[str]] = frozenset(
    {
        "VAT_OUTPUT",
        "VAT_INPUT",
        "VAT_INPUT_NONCREDITABLE",
        "VAT_PAYABLE_NET",
    }
)


async def resolve_account_id_by_role_if_pkp(
    conn, tenant_id: str, role_key: str
) -> UUID | None:
    """Resolve account_id with PKP gating for VAT roles.

    Behavior:
        - VAT_OUTPUT / VAT_INPUT / VAT_INPUT_NONCREDITABLE / VAT_PAYABLE_NET:
          check Tenant.is_pkp first. If false, return None (caller must skip
          VAT line emission). If true, delegate to resolve_account_id_by_role.
        - Non-VAT roles: delegate directly (WHT, AR, AP, inventory, etc.).

    Returns:
        UUID when role resolves; None when tenant is non-PKP and role is VAT.

    Raises:
        ValueError: role_key not in catalog.
        AccountRoleUnmappedError: PKP tenant but VAT role unmapped, or any
            non-VAT role unmapped — posting MUST abort.

    Use this in posting paths for VAT_OUTPUT / VAT_INPUT to support non-PKP
    tenants gracefully. WHT_PPH_* paths should NOT use this — call
    resolve_account_id_by_role() directly.
    """
    if role_key in _VAT_ROLES:
        is_pkp = await conn.fetchval(
            'SELECT is_pkp FROM "Tenant" WHERE id = $1',
            tenant_id,
        )
        if is_pkp is None:
            raise AccountRoleUnmappedError(
                f"Tenant {tenant_id!r} not found while checking PKP status "
                f"for role {role_key!r}."
            )
        if not is_pkp:
            # Non-PKP: caller must skip VAT line emission.
            return None

    return await resolve_account_id_by_role(conn, tenant_id, role_key)
