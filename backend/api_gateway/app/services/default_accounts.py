"""
default_accounts — resolve default CoA account IDs for CRUD payloads.

Reads `default_accounts_policy` from DirectActionConfig and auto-fills payload fields.
Ensures chat-created entities have same CoA linkage as form-created entities.

Policy format (per DirectActionConfig):
    default_accounts_policy = {
        "sales_account_id": ("REVENUE", "4-10100", "penjualan"),
        "purchase_account_id": ("EXPENSE", "5-20900", "lain"),
        "inventory_account_id": ("ASSET", "1-10600", "persediaan"),
        "cogs_account_id": ("COGS", "5-10100", "hpp"),
    }

Tuple format: (account_type, code_prefix, name_hint)
- Tries code_prefix first (exact match), then account_type + name_hint ILIKE.

Safe fallback: if lookup fails for any field, it's skipped (non-blocking).
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)


async def resolve_default_accounts(
    conn,
    tenant_id: str,
    payload: dict,
    policy: Optional[dict] = None,
) -> dict:
    """
    Resolve default CoA accounts based on policy and inject into payload.

    Args:
        conn: asyncpg connection
        tenant_id: tenant for RLS
        payload: dict to mutate (adds *_id and *_account keys)
        policy: dict from DirectActionConfig.default_accounts_policy

    Returns:
        Same payload dict (mutated in place, also returned for chaining)
    """
    if not policy:
        return payload

    async def _resolve_one(code_prefix: str, account_type: str, name_hint: str):
        row = await conn.fetchrow(
            """SELECT id, account_code, name FROM chart_of_accounts
               WHERE tenant_id = $1 AND is_active = true AND NOT is_header
                 AND account_code LIKE $2
               ORDER BY account_code ASC LIMIT 1""",
            tenant_id,
            f"{code_prefix}%",
        )
        if row:
            return row
        return await conn.fetchrow(
            """SELECT id, account_code, name FROM chart_of_accounts
               WHERE tenant_id = $1 AND is_active = true AND NOT is_header
                 AND account_type = $2 AND name ILIKE $3
               ORDER BY account_code ASC LIMIT 1""",
            tenant_id,
            account_type,
            f"%{name_hint}%",
        )

    resolved = {}
    for id_field, spec in policy.items():
        # Skip if user/chat already provided it
        if payload.get(id_field):
            continue
        try:
            account_type, code_prefix, name_hint = spec
            row = await _resolve_one(code_prefix, account_type, name_hint)
            if row:
                payload[id_field] = str(row["id"])
                # Derive friendly name field (e.g. sales_account_id → sales_account)
                name_field = id_field.replace("_id", "")
                payload[name_field] = row["name"]
                resolved[id_field] = row["name"]
        except Exception as e:
            logger.warning("[default_accounts] Failed to resolve %s: %s", id_field, e)

    if resolved:
        logger.info("[default_accounts] Resolved: %s", resolved)

    return payload
