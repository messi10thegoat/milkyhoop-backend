"""
Layer 1: SECURITY
Validates tenant_id and user_id are present and well-formed.
"""
import logging
import re

from .base import BaseValidator, ValidationContext

logger = logging.getLogger(__name__)

# Simple UUID-like pattern (accepts UUIDs and numeric IDs)
UUID_PATTERN = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
NUMERIC_ID_PATTERN = re.compile(r"^\d+$")


# Alphanumeric slug pattern (e.g., "evlogia", "tenant-1")
SLUG_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,99}$")


def _is_valid_id(value: str) -> bool:
    """Accept UUID format, numeric ID, or alphanumeric slug."""
    if not value or not value.strip():
        return False
    value = value.strip()
    return bool(UUID_PATTERN.match(value) or NUMERIC_ID_PATTERN.match(value) or SLUG_PATTERN.match(value))


class SecurityValidator(BaseValidator):
    """Layer 1: Basic security checks on tenant and user identity."""

    async def validate(self, ctx: ValidationContext) -> None:
        logger.debug("Running SECURITY validation")

        # Check tenant_id
        if not ctx.tenant_id or not ctx.tenant_id.strip():
            ctx.add_error(
                layer="SECURITY",
                code="MISSING_TENANT_ID",
                message="tenant_id is required",
                blocking=True,
                field_name="tenant_id",
            )
            return  # No point continuing without tenant

        if not _is_valid_id(ctx.tenant_id):
            ctx.add_error(
                layer="SECURITY",
                code="INVALID_TENANT_ID",
                message=f"tenant_id format is invalid: {ctx.tenant_id}",
                blocking=True,
                field_name="tenant_id",
            )
            return

        # Check user_id
        if not ctx.user_id or not ctx.user_id.strip():
            ctx.add_error(
                layer="SECURITY",
                code="MISSING_USER_ID",
                message="user_id is required",
                blocking=True,
                field_name="user_id",
            )
            return

        if not _is_valid_id(ctx.user_id):
            ctx.add_error(
                layer="SECURITY",
                code="INVALID_USER_ID",
                message=f"user_id format is invalid: {ctx.user_id}",
                blocking=True,
                field_name="user_id",
            )

        logger.debug("SECURITY validation passed")
