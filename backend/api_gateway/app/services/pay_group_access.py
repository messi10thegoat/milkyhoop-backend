"""Pay Group Access Helper — resolves which pay groups a user can access."""
import logging

logger = logging.getLogger(__name__)


async def get_accessible_pay_group_ids(
    user_id: str, tenant_id: str, role_code: str, conn
) -> list:
    """Get pay group IDs this user can access.

    OWNER/ADMIN = all active groups (bypass).
    Others = only groups in user_pay_group_access where revoked_at IS NULL.
    Empty list = no payroll access.
    """
    if role_code in ("OWNER", "ADMIN"):
        rows = await conn.fetch(
            "SELECT id FROM pay_groups WHERE tenant_id = $1 AND is_active = true",
            tenant_id,
        )
        return [str(r["id"]) for r in rows]

    rows = await conn.fetch(
        """SELECT pay_group_id FROM user_pay_group_access
           WHERE user_id = $1 AND tenant_id = $2 AND revoked_at IS NULL""",
        user_id,
        tenant_id,
    )
    return [str(r["pay_group_id"]) for r in rows]


async def get_user_role_code(user_id: str, tenant_id: str, conn) -> str:
    """Get user's business role code from user_tenant_roles."""
    row = await conn.fetchrow(
        """SELECT r.code FROM user_tenant_roles utr
           JOIN roles r ON r.id = utr.role_id
           WHERE utr.user_id = $1 AND utr.tenant_id = $2 AND LOWER(utr.status) = 'active'
           LIMIT 1""",
        user_id,
        tenant_id,
    )
    return row["code"] if row else "VIEWER"
