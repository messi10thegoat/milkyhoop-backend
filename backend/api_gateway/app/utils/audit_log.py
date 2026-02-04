"""
Master Data Audit Logging Utility
Following Iron Law 12: Audit Trail Immutability

Usage:
    from app.utils.audit_log import log_master_data_change

    await log_master_data_change(
        conn=conn,
        tenant_id=tenant_id,
        entity_type="CUSTOMER",
        entity_id=str(customer_id),
        entity_name=customer["name"],
        action="UPDATE",
        changes={"phone": (old_phone, new_phone), "email": (old_email, new_email)},
        changed_by=user_id,
        changed_by_name=user_name
    )
"""

from typing import Optional, Dict, Tuple, Any
from uuid import UUID
import asyncpg
import logging

logger = logging.getLogger(__name__)


async def log_master_data_change(
    conn: asyncpg.Connection,
    tenant_id: UUID,
    entity_type: str,
    entity_id: str,
    entity_name: Optional[str],
    action: str,
    changes: Optional[Dict[str, Tuple[Any, Any]]] = None,
    changed_by: Optional[UUID] = None,
    changed_by_name: Optional[str] = None,
    source_ip: Optional[str] = None,
    user_agent: Optional[str] = None,
    notes: Optional[str] = None,
) -> None:
    """
    Log a master data change to the audit log.

    Args:
        conn: Database connection
        tenant_id: Tenant UUID
        entity_type: One of CUSTOMER, VENDOR, ITEM, ACCOUNT, BANK
        entity_id: UUID of the entity (as string)
        entity_name: Name of the entity at time of change (snapshot)
        action: One of CREATE, UPDATE, DELETE, SOFT_DELETE, ACTIVATE, DEACTIVATE, MERGE
        changes: Dict of {field_name: (old_value, new_value)} for UPDATE actions
        changed_by: User UUID who made the change
        changed_by_name: User name snapshot
        source_ip: Client IP address
        user_agent: Client user agent
        notes: Additional notes

    Returns:
        None - logs are fire-and-forget, errors are logged but not raised
    """
    try:
        if changes and action == "UPDATE":
            # Log each field change as separate row for easier querying
            for field_name, (old_value, new_value) in changes.items():
                # Skip if no actual change
                if str(old_value) == str(new_value):
                    continue

                await conn.execute(
                    """
                    INSERT INTO master_data_audit_log (
                        tenant_id, entity_type, entity_id, entity_name,
                        action, field_name, old_value, new_value,
                        changed_by, changed_by_name, source_ip, user_agent, notes
                    ) VALUES ($1, $2, $3::uuid, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
                    """,
                    tenant_id,
                    entity_type,
                    entity_id,
                    entity_name,
                    action,
                    field_name,
                    str(old_value) if old_value is not None else None,
                    str(new_value) if new_value is not None else None,
                    changed_by,
                    changed_by_name,
                    source_ip,
                    user_agent,
                    notes,
                )
        else:
            # Log single row for CREATE, DELETE, ACTIVATE, etc.
            await conn.execute(
                """
                INSERT INTO master_data_audit_log (
                    tenant_id, entity_type, entity_id, entity_name,
                    action, changed_by, changed_by_name, source_ip, user_agent, notes
                ) VALUES ($1, $2, $3::uuid, $4, $5, $6, $7, $8, $9, $10)
                """,
                tenant_id,
                entity_type,
                entity_id,
                entity_name,
                action,
                changed_by,
                changed_by_name,
                source_ip,
                user_agent,
                notes,
            )

        logger.info(
            f"Audit log: {action} {entity_type} {entity_id} ({entity_name}) by {changed_by_name}"
        )

    except Exception as e:
        # Log error but don't raise - audit logging should not block operations
        logger.error(f"Failed to log audit entry: {e}")


async def get_entity_audit_history(
    conn: asyncpg.Connection,
    tenant_id: UUID,
    entity_type: str,
    entity_id: str,
    limit: int = 50,
    offset: int = 0,
) -> list:
    """
    Get audit history for a specific entity.

    Returns list of audit log entries ordered by most recent first.
    """
    rows = await conn.fetch(
        """
        SELECT
            id, action, field_name, old_value, new_value,
            changed_by_name, changed_at, notes
        FROM master_data_audit_log
        WHERE tenant_id = $1 AND entity_type = $2 AND entity_id = $3::uuid
        ORDER BY changed_at DESC
        LIMIT $4 OFFSET $5
        """,
        tenant_id,
        entity_type,
        entity_id,
        limit,
        offset,
    )

    return [dict(row) for row in rows]
