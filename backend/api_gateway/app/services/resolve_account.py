"""
Shared account resolution helper — Law 27 compliance.

Centralized function to resolve account codes from chart_of_accounts.
Raises explicit error if account not found for tenant.
"""
from typing import Dict, List, Optional


async def resolve_account(conn, tenant_id: str, account_code: str) -> dict:
    """
    Resolve account code to full account record from chart_of_accounts.

    Args:
        conn: asyncpg connection
        tenant_id: Tenant identifier
        account_code: CoA account code (e.g., '5-10100')

    Returns:
        dict with keys: id, account_code, name

    Raises:
        ValueError if account not found or inactive
    """
    row = await conn.fetchrow(
        """
        SELECT id, account_code, name
        FROM chart_of_accounts
        WHERE tenant_id = $1 AND account_code = $2 AND is_active = true
        """,
        tenant_id, account_code
    )
    if not row:
        raise ValueError(
            f"Account '{account_code}' not found for tenant '{tenant_id}'. "
            f"Ensure the account exists in chart_of_accounts and is active."
        )
    return dict(row)


async def resolve_account_id(conn, tenant_id: str, account_code: str) -> str:
    """Shorthand: resolve account code to UUID string."""
    acct = await resolve_account(conn, tenant_id, account_code)
    return str(acct["id"])


async def resolve_account_id_or_none(conn, tenant_id: str, account_code: str) -> Optional[str]:
    """Resolve account code to UUID string, return None if not found."""
    row = await conn.fetchrow(
        """
        SELECT id FROM chart_of_accounts
        WHERE tenant_id = $1 AND account_code = $2 AND is_active = true
        """,
        tenant_id, account_code
    )
    return str(row["id"]) if row else None


async def resolve_accounts_by_codes(conn, tenant_id: str, account_codes: List[str]) -> Dict[str, str]:
    """
    Bulk resolve multiple account codes to a dict of {account_code: uuid_string}.

    Args:
        conn: asyncpg connection
        tenant_id: Tenant identifier
        account_codes: List of CoA account codes

    Returns:
        Dict mapping account_code -> UUID string

    Raises:
        ValueError if any account code not found
    """
    rows = await conn.fetch(
        """
        SELECT id, account_code FROM chart_of_accounts
        WHERE tenant_id = $1 AND account_code = ANY($2) AND is_active = true
        """,
        tenant_id, account_codes
    )
    result = {row["account_code"]: str(row["id"]) for row in rows}

    missing = set(account_codes) - set(result.keys())
    if missing:
        raise ValueError(
            f"Accounts not found for tenant '{tenant_id}': {sorted(missing)}. "
            f"Ensure they exist in chart_of_accounts and are active."
        )
    return result
