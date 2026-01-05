"""
Chart of Accounts Service
========================

Manages the Chart of Accounts (Bagan Akun) for tenants.
Provides CRUD operations and account resolution by code.
"""
import logging
from decimal import Decimal
from typing import List, Optional, Dict
from uuid import UUID

from ..config import settings
from ..constants import AccountType, NormalBalance, ACCOUNT_TYPE_NORMAL_BALANCE
from ..models.coa import Account, AccountCreate, AccountUpdate, AccountBalance

logger = logging.getLogger(__name__)


class CoAService:
    """
    Chart of Accounts Service

    Responsibilities:
    - CRUD operations for accounts
    - Account resolution by code
    - Default CoA seeding
    - Account hierarchy management
    """

    def __init__(self, db_pool):
        """
        Initialize with database pool.

        Args:
            db_pool: asyncpg connection pool
        """
        self.db = db_pool

    async def get_account_by_code(
        self,
        tenant_id: str,
        code: str
    ) -> Optional[Account]:
        """
        Get account by code.

        Args:
            tenant_id: Tenant UUID
            code: Account code (e.g., "1-10100")

        Returns:
            Account if found, None otherwise
        """
        async with self.db.acquire() as conn:
            # Set tenant context for RLS
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true)",
                str(tenant_id)
            )

            row = await conn.fetchrow(
                """
                SELECT id, tenant_id, account_code, name, account_type, normal_balance,
                       parent_code, is_active, description,
                       created_at, updated_at
                FROM chart_of_accounts
                WHERE tenant_id = $1 AND account_code = $2
                """,
                tenant_id, code
            )

            if not row:
                return None

            return self._row_to_account(row)

    async def get_account_by_id(
        self,
        tenant_id: str,
        account_id: UUID
    ) -> Optional[Account]:
        """Get account by ID."""
        async with self.db.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true)",
                str(tenant_id)
            )

            row = await conn.fetchrow(
                """
                SELECT id, tenant_id, account_code, name, account_type, normal_balance,
                       parent_code, is_active, description,
                       created_at, updated_at
                FROM chart_of_accounts
                WHERE tenant_id = $1 AND id = $2
                """,
                tenant_id, account_id
            )

            if not row:
                return None

            return self._row_to_account(row)

    async def list_accounts(
        self,
        tenant_id: str,
        account_type: Optional[AccountType] = None,
        is_active: Optional[bool] = None,
        parent_id: Optional[UUID] = None,
        search: Optional[str] = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[Account]:
        """
        List accounts with filtering.

        Args:
            tenant_id: Tenant UUID
            account_type: Filter by type
            is_active: Filter by active status
            parent_id: Filter by parent
            search: Search in code/name
            limit: Max results
            offset: Skip results

        Returns:
            List of Account
        """
        async with self.db.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true)",
                str(tenant_id)
            )

            # Build query dynamically
            conditions = ["tenant_id = $1"]
            params = [tenant_id]
            param_idx = 2

            if account_type:
                conditions.append(f"type = ${param_idx}")
                params.append(account_type.value)
                param_idx += 1

            if is_active is not None:
                conditions.append(f"is_active = ${param_idx}")
                params.append(is_active)
                param_idx += 1

            if parent_id:
                conditions.append(f"parent_id = ${param_idx}")
                params.append(parent_id)
                param_idx += 1

            if search:
                conditions.append(
                    f"(code ILIKE ${param_idx} OR name ILIKE ${param_idx})"
                )
                params.append(f"%{search}%")
                param_idx += 1

            where_clause = " AND ".join(conditions)

            query = f"""
                SELECT id, tenant_id, account_code, name, account_type, normal_balance,
                       parent_code, is_active, description,
                       created_at, updated_at
                FROM chart_of_accounts
                WHERE {where_clause}
                ORDER BY code
                LIMIT ${param_idx} OFFSET ${param_idx + 1}
            """
            params.extend([limit, offset])

            rows = await conn.fetch(query, *params)

            return [self._row_to_account(row) for row in rows]

    async def create_account(self, request: AccountCreate) -> Account:
        """
        Create a new account.

        Args:
            request: AccountCreate request

        Returns:
            Created Account

        Raises:
            ValueError: If account code already exists
        """
        async with self.db.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true)",
                str(request.tenant_id)
            )

            # Check if code already exists
            existing = await conn.fetchrow(
                "SELECT id FROM chart_of_accounts WHERE tenant_id = $1 AND account_code = $2",
                request.tenant_id, request.code
            )
            if existing:
                raise ValueError(f"Account code {request.code} already exists")

            # Determine normal balance from type if not provided
            normal_balance = request.normal_balance
            if not normal_balance:
                normal_balance = ACCOUNT_TYPE_NORMAL_BALANCE.get(
                    request.type, NormalBalance.DEBIT
                )

            row = await conn.fetchrow(
                """
                INSERT INTO chart_of_accounts (
                    tenant_id, code, name, type, normal_balance,
                    parent_id, is_system, metadata
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                RETURNING id, tenant_id, code, name, type, normal_balance,
                          parent_code, is_active, description,
                          created_at, updated_at
                """,
                request.tenant_id,
                request.code,
                request.name,
                request.type.value,
                normal_balance.value,
                request.parent_id,
                request.is_system,
                request.metadata or {}
            )

            logger.info(f"Created account {request.code} for tenant {request.tenant_id}")
            return self._row_to_account(row)

    async def update_account(
        self,
        tenant_id: str,
        account_id: UUID,
        request: AccountUpdate
    ) -> Optional[Account]:
        """
        Update an account.

        Note: Cannot update system accounts or change code/type.

        Args:
            tenant_id: Tenant UUID
            account_id: Account UUID
            request: AccountUpdate request

        Returns:
            Updated Account or None if not found
        """
        async with self.db.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true)",
                str(tenant_id)
            )

            # Check if account exists and is not system
            existing = await conn.fetchrow(
                "SELECT is_active FROM chart_of_accounts WHERE tenant_id = $1 AND id = $2",
                tenant_id, account_id
            )
            if not existing:
                return None
            if existing["is_system"]:
                raise ValueError("Cannot update system account")

            # Build update query
            updates = []
            params = []
            param_idx = 1

            if request.name is not None:
                updates.append(f"name = ${param_idx}")
                params.append(request.name)
                param_idx += 1

            if request.parent_id is not None:
                updates.append(f"parent_id = ${param_idx}")
                params.append(request.parent_id)
                param_idx += 1

            if request.is_active is not None:
                updates.append(f"is_active = ${param_idx}")
                params.append(request.is_active)
                param_idx += 1

            if request.metadata is not None:
                updates.append(f"metadata = ${param_idx}")
                params.append(request.metadata)
                param_idx += 1

            if not updates:
                return await self.get_account_by_id(tenant_id, account_id)

            updates.append("updated_at = NOW()")

            query = f"""
                UPDATE chart_of_accounts
                SET {", ".join(updates)}
                WHERE tenant_id = ${param_idx} AND id = ${param_idx + 1}
                RETURNING id, tenant_id, code, name, type, normal_balance,
                          parent_code, is_active, description,
                          created_at, updated_at
            """
            params.extend([tenant_id, account_id])

            row = await conn.fetchrow(query, *params)
            if not row:
                return None

            return self._row_to_account(row)

    async def deactivate_account(
        self,
        tenant_id: str,
        account_id: UUID
    ) -> bool:
        """
        Deactivate an account (soft delete).

        Note: Cannot deactivate system accounts.

        Returns:
            True if deactivated, False if not found
        """
        async with self.db.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true)",
                str(tenant_id)
            )

            # Check if system account
            existing = await conn.fetchrow(
                "SELECT is_active FROM chart_of_accounts WHERE tenant_id = $1 AND id = $2",
                tenant_id, account_id
            )
            if not existing:
                return False
            if existing["is_system"]:
                raise ValueError("Cannot deactivate system account")

            result = await conn.execute(
                """
                UPDATE chart_of_accounts
                SET is_active = false, updated_at = NOW()
                WHERE tenant_id = $1 AND id = $2
                """,
                tenant_id, account_id
            )

            return result == "UPDATE 1"

    async def resolve_account_id(
        self,
        tenant_id: str,
        code: str
    ) -> Optional[UUID]:
        """
        Resolve account code to ID.

        Args:
            tenant_id: Tenant UUID
            code: Account code

        Returns:
            Account UUID or None
        """
        async with self.db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id FROM chart_of_accounts WHERE tenant_id = $1 AND account_code = $2",
                tenant_id, code
            )
            return row["id"] if row else None

    async def resolve_payment_account(
        self,
        tenant_id: str,
        payment_method: str
    ) -> Optional[UUID]:
        """
        Resolve payment method to account ID.

        Args:
            tenant_id: Tenant UUID
            payment_method: Payment method (CASH, TRANSFER, etc.)

        Returns:
            Account UUID for the payment method
        """
        code = settings.accounting.PAYMENT_ACCOUNT_MAPPING.get(
            payment_method.upper(),
            settings.accounting.CASH_ACCOUNT
        )
        return await self.resolve_account_id(tenant_id, code)

    async def seed_default_accounts(self, tenant_id: str) -> int:
        """
        Seed default Chart of Accounts for a tenant.

        Args:
            tenant_id: Tenant UUID

        Returns:
            Number of accounts created
        """
        async with self.db.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true)",
                str(tenant_id)
            )

            result = await conn.fetchrow(
                "SELECT seed_default_coa($1) as count",
                tenant_id
            )

            count = result["count"] if result else 0
            logger.info(f"Seeded {count} default accounts for tenant {tenant_id}")
            return count

    async def get_account_balances(
        self,
        tenant_id: str,
        as_of_date: str,
        account_type: Optional[AccountType] = None
    ) -> List[AccountBalance]:
        """
        Get account balances as of a date.

        Args:
            tenant_id: Tenant UUID
            as_of_date: Date string (YYYY-MM-DD)
            account_type: Optional filter by type

        Returns:
            List of AccountBalance
        """
        async with self.db.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true)",
                str(tenant_id)
            )

            type_filter = ""
            params = [tenant_id, as_of_date]
            if account_type:
                type_filter = "AND c.type = $3"
                params.append(account_type.value)

            query = f"""
                SELECT
                    c.id as account_id,
                    c.code as account_code,
                    c.name as account_name,
                    c.type as account_type,
                    c.normal_balance,
                    COALESCE(SUM(jl.debit), 0) as total_debit,
                    COALESCE(SUM(jl.credit), 0) as total_credit,
                    CASE
                        WHEN c.normal_balance = 'DEBIT'
                        THEN COALESCE(SUM(jl.debit), 0) - COALESCE(SUM(jl.credit), 0)
                        ELSE COALESCE(SUM(jl.credit), 0) - COALESCE(SUM(jl.debit), 0)
                    END as balance
                FROM chart_of_accounts c
                LEFT JOIN journal_lines jl ON jl.account_id = c.id
                LEFT JOIN journal_entries je ON je.id = jl.journal_id
                    AND je.journal_date <= $2::date
                    AND je.status = 'POSTED'
                WHERE c.tenant_id = $1
                    AND c.is_active = true
                    {type_filter}
                GROUP BY c.id, c.code, c.name, c.type, c.normal_balance
                HAVING COALESCE(SUM(jl.debit), 0) != 0
                    OR COALESCE(SUM(jl.credit), 0) != 0
                ORDER BY c.code
            """

            rows = await conn.fetch(query, *params)

            return [
                AccountBalance(
                    account_id=row["account_id"],
                    account_code=row["account_code"],
                    account_name=row["account_name"],
                    account_type=AccountType(row["account_type"]),
                    normal_balance=NormalBalance(row["normal_balance"]),
                    total_debit=Decimal(str(row["total_debit"])),
                    total_credit=Decimal(str(row["total_credit"])),
                    balance=Decimal(str(row["balance"])),
                    as_of_date=as_of_date
                )
                for row in rows
            ]

    def _row_to_account(self, row) -> Account:
        """Convert database row to Account model."""
        return Account(
            id=row["id"],
            tenant_id=row["tenant_id"],
            code=row["account_code"],  # DB column: account_code -> model: code
            name=row["name"],
            type=AccountType(row["account_type"]),  # DB column: account_type -> model: type
            normal_balance=NormalBalance(row["normal_balance"]),
            parent_id=None,  # DB has parent_code (text), not parent_id (uuid)
            is_active=row["is_active"],
            is_system=False,  # Not in DB schema
            metadata={},  # Not in DB schema
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
