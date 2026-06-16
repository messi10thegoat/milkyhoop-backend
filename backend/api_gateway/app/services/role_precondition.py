"""
Role Precondition Check — Fase C0
==================================

Pre-flight check before flipping a posting path from hardcoded
account_code resolution to role-based resolution
(`resolve_account_id_by_role`).

Workflow:
    1. Before refactor: call `check_required_roles_for_path()` or run the
       CLI to confirm ALL tenants have the required role mappings.
    2. If any tenant is missing a mapping, abort the refactor. Either
       seed the mapping (V150-style migration) or document the gap and
       defer migration of that posting path.
    3. After refactor and DB seed: call `assert_required_roles_for_path()`
       at gateway startup or pre-flight test to fail-loud if a tenant
       regresses.

Iron Laws:
    Law 27 — runtime resolution, single source of truth.
    Law 32 — caller-managed connection from get_db_pool().
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import Iterable

from .role_resolver import _CATALOG  # type: ignore[attr-defined]


class PreconditionFailedError(Exception):
    """Raised when one or more tenants lack required role mappings.

    Attributes:
        path_name: logical posting path (e.g. ``sales_invoices``).
        gaps: mapping ``role_key -> [tenant_id, ...]`` for missing rows.
    """

    def __init__(self, path_name: str, gaps: dict[str, list[str]]):
        self.path_name = path_name
        self.gaps = gaps
        details = ", ".join(
            f"{role}={tenants}" for role, tenants in sorted(gaps.items())
        )
        super().__init__(f"Precondition failed for path '{path_name}': {details}")


def _validate_roles(required_roles: Iterable[str]) -> list[str]:
    roles = list(required_roles)
    if not roles:
        raise ValueError("required_roles must be non-empty")
    unknown = [r for r in roles if r not in _CATALOG]
    if unknown:
        raise ValueError(
            f"Unknown role_key(s): {unknown}. "
            f"Add to AccountRole + V149 CHECK constraint first."
        )
    return roles


async def check_required_roles_for_path(
    pool, path_name: str, required_roles: list[str], tenant_id: str | None = None
) -> dict[str, list[str]]:
    """Audit tenants for the required role mappings.

    Args:
        tenant_id: when given, audit ONLY this tenant (the one performing the
            action). When None, audit every tenant (the original startup/CLI
            behaviour). Scoping to the acting tenant is what posting paths want:
            one misconfigured (e.g. test) tenant must NOT block posting for a
            properly-onboarded tenant. (FIX_ROLE_PRECOND_PER_TENANT, 2026-06-16)

    Returns:
        ``{role_key: [tenant_id, ...]}`` of MISSING mappings. Empty dict
        ⇒ the audited tenant(s) are mapping-complete for this path.

    Notes:
        - Reads via superuser/admin pool; bypasses RLS on purpose so the
          audit sees every tenant. Caller MUST ensure they hold the right
          privilege before invoking.
        - ``path_name`` is informational; used only in error messages.
    """
    _ = path_name  # informational
    roles = _validate_roles(required_roles)

    async with pool.acquire() as conn:
        if tenant_id is not None:
            tenant_ids = [tenant_id]
            present = await conn.fetch(
                "SELECT tenant_id, role_key FROM account_roles "
                "WHERE role_key = ANY($1::text[]) AND tenant_id = $2",
                roles,
                tenant_id,
            )
        else:
            rows = await conn.fetch('SELECT id FROM "Tenant" ORDER BY id')
            tenant_ids = [r["id"] for r in rows]
            # Single round-trip: fetch all (tenant_id, role_key) pairs we care about.
            present = await conn.fetch(
                "SELECT tenant_id, role_key FROM account_roles "
                "WHERE role_key = ANY($1::text[])",
                roles,
            )

    have = {(r["tenant_id"], r["role_key"]) for r in present}
    gaps: dict[str, list[str]] = {}
    for role in roles:
        missing = [t for t in tenant_ids if (t, role) not in have]
        if missing:
            gaps[role] = missing
    return gaps


async def assert_required_roles_for_path(
    pool, path_name: str, required_roles: list[str], tenant_id: str | None = None
) -> None:
    """Fail-loud version of :func:`check_required_roles_for_path`.

    Args:
        tenant_id: when given, only the acting tenant is audited (see
            :func:`check_required_roles_for_path`).

    Raises:
        PreconditionFailedError: the audited tenant(s) miss any required role.
    """
    gaps = await check_required_roles_for_path(
        pool, path_name, required_roles, tenant_id=tenant_id
    )
    if gaps:
        raise PreconditionFailedError(path_name, gaps)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

_DEFAULT_DSN = os.environ.get(
    "TEST_DATABASE_URL",
    "",  # set TEST_DATABASE_URL env var
)


async def _cli_main(dsn: str, path_name: str, roles: list[str]) -> int:
    import asyncpg  # local import to keep module light at startup

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        gaps = await check_required_roles_for_path(pool, path_name, roles)
    finally:
        await pool.close()

    if not gaps:
        print(f"[OK] path={path_name}: all tenants mapped for {roles}")
        return 0

    print(f"[GAP] path={path_name}: missing mappings detected")
    for role in sorted(gaps):
        tenants = ", ".join(gaps[role])
        print(f"  - {role}: {tenants}")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="role_precondition",
        description="Audit account_roles mappings before posting-path migration.",
    )
    parser.add_argument(
        "--path", required=True, help="Posting path name (informational)"
    )
    parser.add_argument(
        "--roles",
        required=True,
        help="Comma-separated role_keys (e.g. AR_TRADE,REVENUE_SALES_GOODS)",
    )
    parser.add_argument("--dsn", default=_DEFAULT_DSN, help="Postgres DSN")
    args = parser.parse_args()

    roles = [r.strip() for r in args.roles.split(",") if r.strip()]
    return asyncio.run(_cli_main(args.dsn, args.path, roles))


if __name__ == "__main__":
    sys.exit(main())
