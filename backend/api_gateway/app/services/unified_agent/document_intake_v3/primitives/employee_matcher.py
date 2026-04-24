"""EmployeeMatcher — lookup employees by name or bank account number."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class EmployeeCandidate:
    id: str
    name: str
    bank_account_number: Optional[str]
    bank_account_name: Optional[str]
    bank_name: Optional[str]
    confidence: float = 0.0


class EmployeeMatcher:
    def __init__(self, pool, tenant_id: str):
        self.pool = pool
        self.tenant_id = tenant_id

    async def match_by_name(self, name: str) -> list[EmployeeCandidate]:
        if not name or not name.strip():
            return []
        pattern = f"%{name.strip()}%"
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(f"SET LOCAL app.tenant_id = '{self.tenant_id}'")
                rows = await conn.fetch(
                    """
                    SELECT id, name, bank_account_number, bank_account_name, bank_name
                    FROM employees
                    WHERE tenant_id = $1 AND is_active = true
                      AND name ILIKE $2
                    ORDER BY length(name) ASC
                    LIMIT 10
                    """,
                    self.tenant_id,
                    pattern,
                )
                return [
                    EmployeeCandidate(
                        id=str(r["id"]),
                        name=r["name"],
                        bank_account_number=r["bank_account_number"],
                        bank_account_name=r["bank_account_name"],
                        bank_name=r["bank_name"],
                        confidence=1.0 if len(rows) == 1 else 0.7,
                    )
                    for r in rows
                ]

    async def match_by_account_number(
        self, account_number: str
    ) -> list[EmployeeCandidate]:
        if not account_number:
            return []
        digits = "".join(c for c in account_number if c.isdigit())
        if not digits:
            return []
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(f"SET LOCAL app.tenant_id = '{self.tenant_id}'")
                rows = await conn.fetch(
                    r"""
                    SELECT id, name, bank_account_number, bank_account_name, bank_name
                    FROM employees
                    WHERE tenant_id = $1 AND is_active = true
                      AND regexp_replace(bank_account_number, '\D', '', 'g') = $2
                    LIMIT 5
                    """,
                    self.tenant_id,
                    digits,
                )
                return [
                    EmployeeCandidate(
                        id=str(r["id"]),
                        name=r["name"],
                        bank_account_number=r["bank_account_number"],
                        bank_account_name=r["bank_account_name"],
                        bank_name=r["bank_name"],
                        confidence=1.0,
                    )
                    for r in rows
                ]
