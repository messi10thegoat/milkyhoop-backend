"""
Policy Engine Client for API Gateway
Provides access control decisions based on V093 Access Control Foundation

IRON LAW COMPLIANCE:
- Law 0: Separation of Concerns - This is the policy layer, not financial core
- Law 10: AI Safety Boundary - All permission checks logged
- Law 12: Audit Immutability - Permission decisions can be audited
"""
import logging
from typing import Optional, List, Dict
from dataclasses import dataclass
import asyncpg

logger = logging.getLogger(__name__)


@dataclass
class UserContext:
    user_id: str
    tenant_id: str
    subscription_role: str  # FREE/USER/OWNER/ADMIN (existing)
    business_role_id: Optional[str] = None
    business_role_code: Optional[str] = None
    visibility_levels: List[str] = None  # [L1, L2, L3]
    approval_limit: Optional[int] = None


# Module name mapping (frontend/API -> database)
MODULE_NAME_MAPPING = {
    # Sales/AR Cycle
    "sales_invoice": "INVOICE",
    "invoice": "INVOICE",
    "quote": "QUOTE",
    "sales_order": "SALES_ORDER",
    "receive_payment": "RECEIPT",
    "customer": "CUSTOMER",
    "credit_note": "CREDIT_NOTE",
    "ar_aging": "AR_AGING",
    # Purchasing/AP Cycle
    "purchase_invoice": "BILL",
    "bill": "BILL",
    "purchase_order": "BILL",
    "send_payment": "PAYMENT",
    "supplier": "VENDOR",
    "vendor": "VENDOR",
    "debit_note": "DEBIT_NOTE",
    # Expense
    "expense": "EXPENSE",
    # HR & Payroll
    "payroll": "PAYROLL",
    "employee": "EMPLOYEE",
    "salary_component": "SALARY_COMPONENT",
    "bpjs": "BPJS",
    "pay_group": "PAY_GROUP",
    # Inventory
    "item": "PRODUCT",
    "product": "PRODUCT",
    "unit": "UNIT",
    "stock_adjust": "STOCK_ADJUST",
    "warehouse": "WAREHOUSE",
    # Manufacturing
    "bom": "BOM",
    "work_order": "WORK_ORDER",
    "work_center": "WORK_CENTER",
    "material_issue": "MATERIAL_ISSUE",
    "fg_receipt": "FG_RECEIPT",
    # Cash & Bank
    "kas_bank": "BANK",
    "bank_account": "BANK",
    # Accounting
    "journal": "JOURNAL",
    "chart_of_accounts": "ACCOUNT",
    "account": "ACCOUNT",
    "ledger": "LEDGER",
    "period": "PERIOD",
    # Reports
    "reports": "REPORT",
    "report": "REPORT",
    # Tax
    "tax": "TAX",
    # Settings & Management
    "team_management": "USER_MANAGEMENT",
    "tenant_settings": "SETTINGS",
    # Other
    "approval_inbox": "JOURNAL",
    "payment_request": "PAYMENT",
    "dashboard": "REPORT",
}

# All known DB modules for OWNER full-access generation
ALL_DB_MODULES = [
    "INVOICE",
    "QUOTE",
    "SALES_ORDER",
    "RECEIPT",
    "AR_AGING",
    "CUSTOMER",
    "CREDIT_NOTE",
    "BILL",
    "PAYMENT",
    "VENDOR",
    "DEBIT_NOTE",
    "EXPENSE",
    "EMPLOYEE",
    "PAYROLL",
    "SALARY_COMPONENT",
    "BPJS",
    "PAY_GROUP",
    "BANK",
    "PRODUCT",
    "UNIT",
    "STOCK_ADJUST",
    "WAREHOUSE",
    "BOM",
    "WORK_ORDER",
    "WORK_CENTER",
    "MATERIAL_ISSUE",
    "FG_RECEIPT",
    "ACCOUNT",
    "JOURNAL",
    "LEDGER",
    "PERIOD",
    "REPORT",
    "TAX",
    "SETTINGS",
    "USER_MANAGEMENT",
]

ALL_ACTIONS = ["C", "R", "U", "D", "V", "A", "P", "E"]


def normalize_module_name(module: str) -> str:
    """Convert frontend module name to database module code."""
    return MODULE_NAME_MAPPING.get(module.lower(), module.upper())


class PolicyEngineClient:
    """
    Client untuk access control decisions.
    Connects to roles, role_permissions, role_visibility, user_tenant_roles,
    user_permission_overrides tables.
    """

    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool
        self._permission_cache: Dict[str, Dict] = {}  # role_id -> permissions
        self._visibility_cache: Dict[str, List[str]] = {}  # role_id -> levels

    async def get_user_context(
        self, user_id: str, tenant_id: str, subscription_role: str
    ) -> UserContext:
        """
        Get full user context including business role and visibility.
        Called after JWT validation in auth middleware.
        """
        context = UserContext(
            user_id=user_id,
            tenant_id=tenant_id,
            subscription_role=subscription_role,
            visibility_levels=["L1", "L2", "L3"],  # Default for backward compat
        )

        try:
            # FIX (deadlock): release the connection BEFORE calling
            # self.get_visibility(), which acquires another connection from the
            # same pool. Holding one while waiting for another exhausts the
            # max-size=5 pool under concurrent load → permanent deadlock.
            async with self.pool.acquire() as conn:
                role_row = await conn.fetchrow(
                    """
                    SELECT r.id, r.code, r.approval_limit
                    FROM user_tenant_roles utr
                    JOIN roles r ON r.id = utr.role_id
                    WHERE utr.user_id = $1::uuid AND utr.tenant_id = $2
                    AND r.is_active = TRUE
                    ORDER BY utr.is_primary DESC
                    LIMIT 1
                """,
                    user_id,
                    tenant_id,
                )

            if role_row:
                context.business_role_id = str(role_row["id"])
                context.business_role_code = role_row["code"]
                context.approval_limit = role_row["approval_limit"]
                context.visibility_levels = await self.get_visibility(
                    context.business_role_id
                )
            else:
                default_visibility = {
                    "ADMIN": ["L1", "L2", "L3", "L4", "L5"],
                    "OWNER": ["L1", "L2", "L3", "L4", "L5"],
                    "USER": ["L1", "L2", "L3"],
                    "FREE": ["L1", "L2"],
                }
                context.visibility_levels = default_visibility.get(
                    subscription_role, ["L1"]
                )

        except Exception as e:
            logger.error(f"Error fetching user context: {e}")

        return context

    async def get_visibility(self, role_id: str) -> List[str]:
        """Get visibility levels for a role (with caching)"""
        if role_id in self._visibility_cache:
            return self._visibility_cache[role_id]

        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT level::text FROM role_visibility WHERE role_id = $1::uuid
                """,
                    role_id,
                )
                levels = [row["level"] for row in rows]
                self._visibility_cache[role_id] = levels
                return levels
        except Exception as e:
            logger.error(f"Error fetching visibility: {e}")
            return ["L1"]

    async def can(self, user_context: UserContext, action: str, module: str) -> bool:
        """
        Check if user can perform action on module.
        Resolution: user_permission_overrides > role_permissions > deny

        Actions: C (Create), R (Read), U (Update), D (Delete),
                 V (Void), A (Approve), P (Post), E (Export)
        """
        if not user_context.business_role_id:
            return user_context.subscription_role in ["ADMIN", "OWNER"]

        # OWNER bypass
        if user_context.business_role_code == "OWNER":
            return True

        try:
            db_module = normalize_module_name(module)

            # Check user overrides first (Kelola Akses)
            overrides = await self._get_user_overrides(
                user_context.user_id, user_context.tenant_id
            )
            if db_module in overrides:
                return action in overrides[db_module]

            # Fall back to role permissions
            permissions = await self._get_role_permissions(
                user_context.business_role_id
            )
            module_perms = permissions.get(db_module, [])
            return action in module_perms
        except Exception as e:
            logger.error(f"Error checking permission: {e}")
            return False  # Fail-closed

    async def _get_role_permissions(self, role_id: str) -> Dict[str, List[str]]:
        """Get all permissions for a role (with caching)"""
        if role_id in self._permission_cache:
            return self._permission_cache[role_id]

        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT module, actions FROM role_permissions WHERE role_id = $1::uuid
                """,
                    role_id,
                )
                permissions = {row["module"]: list(row["actions"]) for row in rows}
                self._permission_cache[role_id] = permissions
                return permissions
        except Exception as e:
            logger.error(f"Error fetching permissions: {e}")
            return {}

    async def _get_user_overrides(
        self, user_id: str, tenant_id: str
    ) -> Dict[str, List[str]]:
        """Get user-specific permission overrides (from Kelola Akses)."""
        cache_key = f"override:{user_id}:{tenant_id}"
        if cache_key in self._permission_cache:
            return self._permission_cache[cache_key]

        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT module, actions
                    FROM user_permission_overrides
                    WHERE user_id = $1 AND tenant_id = $2
                """,
                    user_id,
                    tenant_id,
                )
                overrides = {row["module"]: list(row["actions"]) for row in rows}
                self._permission_cache[cache_key] = overrides
                return overrides
        except Exception as e:
            logger.error(f"Error fetching user overrides: {e}")
            return {}

    async def get_effective_permissions(self, user_id: str, tenant_id: str) -> dict:
        """
        Get merged effective permissions for user.
        Returns: {"role_code": str, "effective_permissions": {module: {"actions": [...], "source": "role"|"override"}}}
        """
        async with self.pool.acquire() as conn:
            # Get user's business role
            role_row = await conn.fetchrow(
                """
                SELECT r.id, r.code
                FROM user_tenant_roles utr
                JOIN roles r ON r.id = utr.role_id
                WHERE utr.user_id = $1::uuid AND utr.tenant_id = $2
                AND r.is_active = TRUE
                ORDER BY utr.is_primary DESC LIMIT 1
            """,
                user_id,
                tenant_id,
            )

            if not role_row:
                return {"role_code": "VIEWER", "effective_permissions": {}}

            role_code = role_row["code"]

            # OWNER = full access everything
            if role_code == "OWNER":
                return {
                    "role_code": "OWNER",
                    "effective_permissions": {
                        m: {"actions": ALL_ACTIONS, "source": "role"}
                        for m in ALL_DB_MODULES
                    },
                }

            # Get role permissions
            role_perms = await self._get_role_permissions(str(role_row["id"]))

            # Get user overrides
            overrides = await self._get_user_overrides(user_id, tenant_id)

            # Merge: override wins
            effective = {}
            all_known_modules = set(list(role_perms.keys()) + list(overrides.keys()))
            for mod in all_known_modules:
                if mod in overrides:
                    effective[mod] = {"actions": overrides[mod], "source": "override"}
                else:
                    effective[mod] = {
                        "actions": role_perms.get(mod, []),
                        "source": "role",
                    }

            return {"role_code": role_code, "effective_permissions": effective}

    def invalidate_user_cache(self, user_id: str, tenant_id: str):
        """Invalidate cached overrides for a specific user."""
        cache_key = f"override:{user_id}:{tenant_id}"
        self._permission_cache.pop(cache_key, None)

    async def requires_approval(
        self, tenant_id: str, document_type: str, amount: int
    ) -> Optional[Dict]:
        """
        Check if transaction requires approval based on approval_workflows table.
        """
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT id, name, min_amount, max_amount, is_sequential
                    FROM approval_workflows
                    WHERE tenant_id = $1
                    AND document_type = $2
                    AND is_active = TRUE
                    AND ($3 >= min_amount OR min_amount IS NULL OR min_amount = 0)
                    AND ($3 <= max_amount OR max_amount IS NULL)
                    ORDER BY min_amount DESC NULLS LAST
                    LIMIT 1
                """,
                    tenant_id,
                    document_type,
                    amount,
                )

                if row:
                    return dict(row)
                return None
        except Exception as e:
            logger.error(f"Error checking approval requirement: {e}")
            return None

    def clear_cache(self):
        """Clear permission and visibility caches (call when roles updated)"""
        self._permission_cache.clear()
        self._visibility_cache.clear()


# Singleton instance (initialized in main.py lifespan)
policy_engine: Optional[PolicyEngineClient] = None


def get_policy_engine() -> PolicyEngineClient:
    if policy_engine is None:
        raise RuntimeError("PolicyEngineClient not initialized")
    return policy_engine


def init_policy_engine(pool: asyncpg.Pool):
    global policy_engine
    policy_engine = PolicyEngineClient(pool)
    logger.info("PolicyEngineClient initialized")
