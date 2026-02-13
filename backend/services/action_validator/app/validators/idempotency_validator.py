"""
Layer 4: IDEMPOTENCY
Checks that the idempotency_key has not already been used for this tenant.
"""
import logging

from .base import BaseValidator, ValidationContext

logger = logging.getLogger(__name__)


class IdempotencyValidator(BaseValidator):
    """Layer 4: Check idempotency key uniqueness."""

    async def validate(self, ctx: ValidationContext) -> None:
        logger.debug("Running IDEMPOTENCY validation")

        if not ctx.idempotency_key or not ctx.idempotency_key.strip():
            # No idempotency key provided -- this is a warning, not blocking
            ctx.add_warning(
                layer="IDEMPOTENCY",
                code="NO_IDEMPOTENCY_KEY",
                message="No idempotency_key provided; duplicate submissions are possible",
                field_name="idempotency_key",
            )
            return

        # Check idempotency_keys table
        row = await ctx.pool.fetchrow(
            "SELECT key FROM idempotency_keys WHERE key = $1 AND tenant_id = $2",
            ctx.idempotency_key,
            ctx.tenant_id,
        )
        if row is not None:
            ctx.add_error(
                layer="IDEMPOTENCY",
                code="DUPLICATE_IDEMPOTENCY_KEY",
                message=f"Action already submitted with idempotency_key: {ctx.idempotency_key}",
                blocking=True,
                field_name="idempotency_key",
            )
            return

        # Also check pending_actions table for the same key
        pa_row = await ctx.pool.fetchrow(
            "SELECT idempotency_key FROM pending_actions WHERE idempotency_key = $1 AND tenant_id = $2",
            ctx.idempotency_key,
            ctx.tenant_id,
        )
        if pa_row is not None:
            ctx.add_error(
                layer="IDEMPOTENCY",
                code="DUPLICATE_PENDING_ACTION",
                message=f"A pending action already exists with idempotency_key: {ctx.idempotency_key}",
                blocking=True,
                field_name="idempotency_key",
            )

        logger.debug("IDEMPOTENCY validation completed")
