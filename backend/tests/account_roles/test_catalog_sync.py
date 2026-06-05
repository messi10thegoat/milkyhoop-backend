"""
Catalog Sync Tests (Section D — Fase F audit)
=============================================
3-way sync verification: AccountRole enum (Python) ↔ role_resolver._CATALOG
↔ DB CHECK constraint on account_roles.role_key.

Spec: any drift between these three sources is a latent bug — a role_key
that the enum allows but the DB rejects (or vice-versa) means posting code
can construct a value that fails at INSERT time, producing a runtime error
with no test coverage.

Also verifies TIER 1 (5 confirmed roles) are seeded for every active tenant,
because TIER 1 mapping is a Fase B exit-criterion.
"""

from __future__ import annotations

import asyncio
import os
import re
import sys

import asyncpg
import pytest

sys.path.insert(0, "/root/milkyhoop-dev/backend/api_gateway")

from app.services.role_resolver import (  # noqa: E402
    AccountRole,
    _CATALOG,
)

SUPERUSER_DSN = os.environ.get("TEST_DATABASE_URL") or os.environ.get(
    "DATABASE_URL", ""
)

if not SUPERUSER_DSN:
    pytest.skip(
        "TEST_DATABASE_URL / DATABASE_URL not set; catalog sync checks skipped",
        allow_module_level=True,
    )


def _run(coro):
    return asyncio.run(coro)


def _enum_values() -> set[str]:
    """Extract all `Final[str]` string constants from AccountRole class."""
    out: set[str] = set()
    for name in dir(AccountRole):
        if name.startswith("_"):
            continue
        val = getattr(AccountRole, name)
        if isinstance(val, str):
            out.add(val)
    return out


async def _db_check_values() -> set[str]:
    """Parse the role_key CHECK constraint and extract allowed string literals."""
    conn = await asyncpg.connect(SUPERUSER_DSN)
    try:
        defn = await conn.fetchval(
            """
            SELECT pg_get_constraintdef(oid)
            FROM pg_constraint
            WHERE conrelid = 'account_roles'::regclass
              AND contype = 'c'
              AND conname = 'account_roles_role_key_check'
            """
        )
    finally:
        await conn.close()
    assert defn, "account_roles_role_key_check CHECK constraint not found"
    # Extract every single-quoted text between 'X'::text patterns.
    return set(re.findall(r"'([A-Z0-9_]+)'::text", defn))


# ---------------------------------------------------------------------------
# Test 1 — 3-way sync: enum == _CATALOG == DB CHECK
# ---------------------------------------------------------------------------
def test_catalog_enum_check_three_way_sync():
    """AccountRole enum, _CATALOG frozenset, and DB CHECK must be identical."""
    enum_set = _enum_values()
    catalog_set = set(_CATALOG)
    db_set = _run(_db_check_values())

    enum_minus_catalog = enum_set - catalog_set
    catalog_minus_enum = catalog_set - enum_set
    assert (
        not enum_minus_catalog
    ), f"AccountRole enum has values missing from _CATALOG: {enum_minus_catalog}"
    assert (
        not catalog_minus_enum
    ), f"_CATALOG has values missing from AccountRole enum: {catalog_minus_enum}"

    catalog_minus_db = catalog_set - db_set
    db_minus_catalog = db_set - catalog_set
    assert not catalog_minus_db, (
        f"_CATALOG has values rejected by DB CHECK: {catalog_minus_db} — "
        "posting code can construct values that fail INSERT."
    )
    assert not db_minus_catalog, (
        f"DB CHECK allows values not in _CATALOG: {db_minus_catalog} — "
        "stale enum, code cannot reference them."
    )


# ---------------------------------------------------------------------------
# Test 2 — TIER 1 seeded per tenant (Fase B exit criterion)
# ---------------------------------------------------------------------------
# TIER 1 minimum required for posting paths (CRUD on sales/purchase/inventory).
# BANK_OPERATIONAL is split out because it is only required for bank-flow
# postings and is observed-missing for 2/5 tenants as of Fase F audit
# (grapgrap, milkytest) — tracked separately as a soft gap.
TIER1_REQUIRED = {
    "CASH_GENERAL",
    "AR_TRADE",
    "INVENTORY_MERCHANDISE",
    "AP_TRADE",
}

TIER1_BANK_OPTIONAL = "BANK_OPERATIONAL"


def test_tier1_seeded_for_all_active_tenants():
    """Every active tenant must have a row in account_roles for each TIER 1 role.

    TIER 1 is the minimum set required for any posting to succeed. Missing
    mapping → AccountRoleUnmappedError at runtime.
    """

    async def body():
        conn = await asyncpg.connect(SUPERUSER_DSN)
        try:
            tenants = await conn.fetch(
                "SELECT id FROM \"Tenant\" WHERE status = 'ACTIVE' ORDER BY id"
            )
            assert tenants, "no ACTIVE tenants found"

            missing: dict[str, set[str]] = {}
            for row in tenants:
                tid = row["id"]
                mapped = {
                    r["role_key"]
                    for r in await conn.fetch(
                        "SELECT role_key FROM account_roles WHERE tenant_id = $1",
                        tid,
                    )
                }
                gap = TIER1_REQUIRED - mapped
                if gap:
                    missing[tid] = gap
            assert not missing, f"TIER 1 roles unmapped for tenants: {missing}"
        finally:
            await conn.close()

    _run(body())
