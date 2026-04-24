"""BankPairDetector — check if src/dst account numbers match tenant's own bank_accounts."""

from __future__ import annotations


class BankPairDetector:
    def __init__(self, pool, tenant_id: str):
        self.pool = pool
        self.tenant_id = tenant_id

    async def detect_own_accounts(
        self, src_account: str, dst_account: str
    ) -> tuple[bool, bool]:
        """Returns (src_is_own, dst_is_own)."""
        src_digits = self._digits(src_account)
        dst_digits = self._digits(dst_account)
        if not src_digits and not dst_digits:
            return False, False
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(f"SET LOCAL app.tenant_id = '{self.tenant_id}'")
                rows = await conn.fetch(
                    r"""
                    SELECT regexp_replace(account_number, '\D', '', 'g') AS digits
                    FROM bank_accounts
                    WHERE tenant_id = $1 AND is_active = true
                      AND account_number IS NOT NULL
                    """,
                    self.tenant_id,
                )
                own = {r["digits"] for r in rows if r["digits"]}
        return (
            src_digits in own if src_digits else False,
            dst_digits in own if dst_digits else False,
        )

    @staticmethod
    def _digits(s: str | None) -> str:
        if not s:
            return ""
        return "".join(c for c in s if c.isdigit())
