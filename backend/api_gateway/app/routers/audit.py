"""
Audit Trail Router - Jejak Audit

Endpoints for audit logging, activity tracking, and compliance reporting.
NO JOURNAL ENTRIES - This is a logging/compliance system.

Endpoints:
- GET    /audit-logs                        - List audit logs with filters
- GET    /audit-logs/{id}                   - Get audit log detail
- GET    /audit-logs/search                 - Full-text search
- GET    /audit-logs/entity/{type}/{id}     - History for entity
- GET    /audit-logs/user/{user_id}         - User activity
- GET    /audit-logs/export                 - Export to CSV

- GET    /audit/login-history               - Login history
- GET    /audit/login-history/user/{id}     - User login history
- GET    /audit/failed-logins               - Failed attempts
- GET    /audit/suspicious-activity         - Flagged suspicious

- GET    /audit/sensitive-access            - Sensitive data access log
- POST   /audit/sensitive-access            - Log sensitive access

- GET    /audit/retention-policies          - List policies
- PATCH  /audit/retention-policies/{id}     - Update policy
- POST   /audit/cleanup                     - Run cleanup

- GET    /audit/summary                     - Activity summary
- GET    /audit/user-activity-report        - User activity report
- GET    /audit/changes-report              - Data changes report
"""

from fastapi import APIRouter, HTTPException, Request, Query
from typing import Optional, Literal
from uuid import UUID
import logging
import asyncpg
from datetime import date, datetime

from ..schemas.audit import (
    LogSensitiveAccessRequest,
    UpdateRetentionPolicyRequest,
    AuditLogListResponse,
    AuditLogDetailResponse,
    EntityHistoryResponse,
    LoginHistoryListResponse,
    SensitiveAccessListResponse,
    RetentionPolicyListResponse,
    AuditSummaryResponse,
    AuditSearchResponse,
    CleanupResponse,
    AuditResponse,
)
from ..config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

# Connection pool
_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
    """Get or create connection pool."""
    global _pool
    if _pool is None:
        db_config = settings.get_db_config()
        _pool = await asyncpg.create_pool(
            **db_config,
            min_size=2,
            max_size=10,
            command_timeout=30
        )
    return _pool


def get_user_context(request: Request) -> dict:
    """Extract and validate user context from request."""
    if not hasattr(request.state, 'user') or not request.state.user:
        raise HTTPException(status_code=401, detail="Authentication required")

    user = request.state.user
    tenant_id = user.get("tenant_id")
    user_id = user.get("user_id")

    if not tenant_id:
        raise HTTPException(status_code=401, detail="Invalid user context")

    return {
        "tenant_id": tenant_id,
        "user_id": UUID(user_id) if user_id else None,
        "user_email": user.get("email"),
        "user_role": user.get("role")
    }


# =============================================================================
# AUDIT LOGS
# =============================================================================

@router.get("/audit-logs", response_model=AuditLogListResponse)
async def list_audit_logs(
    request: Request,
    entity_type: Optional[str] = Query(None),
    entity_id: Optional[UUID] = Query(None),
    action: Optional[Literal["create", "read", "update", "delete", "login", "logout", "export"]] = Query(None),
    user_id: Optional[UUID] = Query(None),
    category: Optional[str] = Query(None),
    severity: Optional[Literal["info", "warning", "error", "critical"]] = Query(None),
    from_date: Optional[date] = Query(None),
    to_date: Optional[date] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    """List audit logs with filters."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Note: audit_logs table uses camelCase columns and no tenant_id
            conditions = ["1=1"]  # No tenant filter as table doesn't have tenant_id
            params = []
            param_idx = 1

            if action:
                conditions.append(f"\"eventType\" = ${param_idx}")
                params.append(action)
                param_idx += 1

            if user_id:
                conditions.append(f"\"userId\" = ${param_idx}")
                params.append(str(user_id))
                param_idx += 1

            if from_date:
                conditions.append(f"\"createdAt\"::DATE >= ${param_idx}")
                params.append(from_date)
                param_idx += 1

            if to_date:
                conditions.append(f"\"createdAt\"::DATE <= ${param_idx}")
                params.append(to_date)
                param_idx += 1

            where_clause = " AND ".join(conditions)

            # Count total
            count_query = f"SELECT COUNT(*) FROM audit_logs WHERE {where_clause}"
            total = await conn.fetchval(count_query, *params) if params else await conn.fetchval("SELECT COUNT(*) FROM audit_logs")

            # Get items - map camelCase columns to expected format
            query = f"""
                SELECT id, "createdAt" as event_time, "userId" as user_id,
                       "ipAddress" as ip_address, "eventType" as action,
                       "userAgent" as user_agent, metadata, success, "errorMessage"
                FROM audit_logs
                WHERE {where_clause}
                ORDER BY "createdAt" DESC
                LIMIT ${param_idx} OFFSET ${param_idx + 1}
            """
            params.extend([limit, skip])

            rows = await conn.fetch(query, *params)

            items = []
            for row in rows:
                raw_metadata = row.get("metadata")
                # Handle case where metadata might be a string or None
                if isinstance(raw_metadata, str):
                    import json
                    try:
                        metadata = json.loads(raw_metadata)
                    except:
                        metadata = {}
                else:
                    metadata = raw_metadata or {}
                items.append({
                    "id": str(row["id"]),
                    "event_time": row["event_time"],
                    "user_id": str(row["user_id"]) if row["user_id"] else None,
                    "user_email": metadata.get("email"),
                    "user_name": metadata.get("name"),
                    "ip_address": str(row["ip_address"]) if row["ip_address"] else None,
                    "action": row["action"],
                    "entity_type": metadata.get("entity_type"),
                    "entity_id": metadata.get("entity_id"),
                    "entity_number": metadata.get("entity_number"),
                    "description": metadata.get("description") or row.get("errorMessage"),
                    "changed_fields": metadata.get("changed_fields"),
                    "category": metadata.get("category", "general"),
                    "severity": "error" if not row["success"] else "info",
                })

            return {
                "items": items,
                "total": total or 0,
                "has_more": (skip + limit) < (total or 0)
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing audit logs: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list audit logs")


@router.get("/audit-logs/search", response_model=AuditSearchResponse)
async def search_audit_logs(
    request: Request,
    q: str = Query(..., min_length=2, description="Search query"),
    limit: int = Query(50, ge=1, le=200),
):
    """Full-text search audit logs."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT id, event_time, action, entity_type, entity_number,
                       description, user_email,
                       ts_rank(to_tsvector('english', search_text), plainto_tsquery('english', $2)) as rank
                FROM audit_logs
                WHERE tenant_id = $1
                AND to_tsvector('english', search_text) @@ plainto_tsquery('english', $2)
                ORDER BY rank DESC, event_time DESC
                LIMIT $3
            """, ctx["tenant_id"], q, limit)

            items = [
                {
                    "id": str(row["id"]),
                    "event_time": row["event_time"],
                    "action": row["action"],
                    "entity_type": row["entity_type"],
                    "entity_number": row["entity_number"],
                    "description": row["description"],
                    "user_email": row["user_email"],
                }
                for row in rows
            ]

            return {
                "items": items,
                "total": len(items),
                "has_more": False,
                "search_query": q
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error searching audit logs: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to search audit logs")


@router.get("/audit-logs/{audit_log_id}", response_model=AuditLogDetailResponse)
async def get_audit_log(request: Request, audit_log_id: UUID):
    """Get audit log detail."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT * FROM audit_logs
                WHERE id = $1 AND tenant_id = $2
            """, audit_log_id, ctx["tenant_id"])

            if not row:
                raise HTTPException(status_code=404, detail="Audit log not found")

            return {
                "success": True,
                "data": {
                    "id": str(row["id"]),
                    "event_time": row["event_time"],
                    "user_id": str(row["user_id"]) if row["user_id"] else None,
                    "user_email": row["user_email"],
                    "user_name": row["user_name"],
                    "ip_address": str(row["ip_address"]) if row["ip_address"] else None,
                    "user_agent": row["user_agent"],
                    "action": row["action"],
                    "entity_type": row["entity_type"],
                    "entity_id": str(row["entity_id"]) if row["entity_id"] else None,
                    "entity_number": row["entity_number"],
                    "description": row["description"],
                    "old_values": row["old_values"],
                    "new_values": row["new_values"],
                    "changed_fields": row["changed_fields"],
                    "request_id": str(row["request_id"]) if row["request_id"] else None,
                    "request_path": row["request_path"],
                    "request_method": row["request_method"],
                    "category": row["category"],
                    "severity": row["severity"],
                }
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting audit log: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get audit log")


@router.get("/audit-logs/entity/{entity_type}/{entity_id}", response_model=EntityHistoryResponse)
async def get_entity_history(
    request: Request,
    entity_type: str,
    entity_id: UUID,
    limit: int = Query(50, ge=1, le=200),
):
    """Get audit history for a specific entity."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Get entity info
            first_row = await conn.fetchrow("""
                SELECT entity_number FROM audit_logs
                WHERE entity_type = $1 AND entity_id = $2 AND tenant_id = $3
                ORDER BY event_time DESC LIMIT 1
            """, entity_type, entity_id, ctx["tenant_id"])

            # Get history
            rows = await conn.fetch("""
                SELECT id, event_time, action, user_email, description,
                       changed_fields, old_values, new_values
                FROM audit_logs
                WHERE entity_type = $1 AND entity_id = $2 AND tenant_id = $3
                ORDER BY event_time DESC
                LIMIT $4
            """, entity_type, entity_id, ctx["tenant_id"], limit)

            history = []
            for row in rows:
                changes = None
                if row["changed_fields"] and row["old_values"] and row["new_values"]:
                    changes = {}
                    for field in row["changed_fields"]:
                        changes[field] = {
                            "old": row["old_values"].get(field),
                            "new": row["new_values"].get(field)
                        }

                history.append({
                    "event_time": row["event_time"],
                    "action": row["action"],
                    "user_email": row["user_email"],
                    "description": row["description"],
                    "changed_fields": row["changed_fields"],
                    "changes": changes
                })

            return {
                "success": True,
                "data": {
                    "entity_type": entity_type,
                    "entity_id": str(entity_id),
                    "entity_number": first_row["entity_number"] if first_row else None,
                    "history": history,
                    "total_changes": len(history)
                }
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting entity history: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get entity history")


@router.get("/audit-logs/user/{user_id}")
async def get_user_activity(
    request: Request,
    user_id: UUID,
    from_date: Optional[date] = Query(None),
    to_date: Optional[date] = Query(None),
    limit: int = Query(100, ge=1, le=500),
):
    """Get activity log for a specific user."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            conditions = ["tenant_id = $1", "user_id = $2"]
            params = [ctx["tenant_id"], user_id]
            param_idx = 3

            if from_date:
                conditions.append(f"event_time::DATE >= ${param_idx}")
                params.append(from_date)
                param_idx += 1

            if to_date:
                conditions.append(f"event_time::DATE <= ${param_idx}")
                params.append(to_date)
                param_idx += 1

            where_clause = " AND ".join(conditions)

            rows = await conn.fetch(f"""
                SELECT id, event_time, action, entity_type, entity_number,
                       description, category
                FROM audit_logs
                WHERE {where_clause}
                ORDER BY event_time DESC
                LIMIT ${param_idx}
            """, *params, limit)

            return {
                "success": True,
                "data": {
                    "user_id": str(user_id),
                    "activity": [
                        {
                            "id": str(row["id"]),
                            "event_time": row["event_time"].isoformat(),
                            "action": row["action"],
                            "entity_type": row["entity_type"],
                            "entity_number": row["entity_number"],
                            "description": row["description"],
                            "category": row["category"]
                        }
                        for row in rows
                    ],
                    "total": len(rows)
                }
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting user activity: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get user activity")


# =============================================================================
# LOGIN HISTORY
# =============================================================================

@router.get("/audit/login-history", response_model=LoginHistoryListResponse)
async def list_login_history(
    request: Request,
    user_id: Optional[UUID] = Query(None),
    status: Optional[Literal["success", "failed_password", "failed_2fa", "blocked"]] = Query(None),
    from_date: Optional[date] = Query(None),
    to_date: Optional[date] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    """List login history."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            conditions = ["(tenant_id = $1 OR tenant_id IS NULL)"]
            params = [ctx["tenant_id"]]
            param_idx = 2

            if user_id:
                conditions.append(f"user_id = ${param_idx}")
                params.append(user_id)
                param_idx += 1

            if status:
                conditions.append(f"login_status = ${param_idx}")
                params.append(status)
                param_idx += 1

            if from_date:
                conditions.append(f"login_time::DATE >= ${param_idx}")
                params.append(from_date)
                param_idx += 1

            if to_date:
                conditions.append(f"login_time::DATE <= ${param_idx}")
                params.append(to_date)
                param_idx += 1

            where_clause = " AND ".join(conditions)

            count_query = f"SELECT COUNT(*) FROM login_history WHERE {where_clause}"
            total = await conn.fetchval(count_query, *params)

            query = f"""
                SELECT id, user_id, user_email, login_time, logout_time,
                       ip_address, device_type, location_country, location_city,
                       login_status, failure_reason, is_suspicious, mfa_used
                FROM login_history
                WHERE {where_clause}
                ORDER BY login_time DESC
                LIMIT ${param_idx} OFFSET ${param_idx + 1}
            """
            params.extend([limit, skip])

            rows = await conn.fetch(query, *params)

            items = [
                {
                    "id": str(row["id"]),
                    "user_id": str(row["user_id"]),
                    "user_email": row["user_email"],
                    "login_time": row["login_time"],
                    "logout_time": row["logout_time"],
                    "ip_address": str(row["ip_address"]) if row["ip_address"] else None,
                    "device_type": row["device_type"],
                    "location_country": row["location_country"],
                    "location_city": row["location_city"],
                    "login_status": row["login_status"],
                    "failure_reason": row["failure_reason"],
                    "is_suspicious": row["is_suspicious"],
                    "mfa_used": row["mfa_used"],
                }
                for row in rows
            ]

            return {
                "items": items,
                "total": total,
                "has_more": (skip + limit) < total
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing login history: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list login history")


@router.get("/audit/failed-logins")
async def get_failed_logins(
    request: Request,
    hours: int = Query(24, ge=1, le=720, description="Look back hours"),
):
    """Get failed login attempts summary."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT user_email, COUNT(*) as failed_count,
                       MAX(login_time) as last_attempt,
                       ARRAY_AGG(DISTINCT failure_reason) as reasons
                FROM login_history
                WHERE (tenant_id = $1 OR tenant_id IS NULL)
                AND login_status != 'success'
                AND login_time >= NOW() - ($2 || ' hours')::INTERVAL
                GROUP BY user_email
                HAVING COUNT(*) >= 3
                ORDER BY COUNT(*) DESC
            """, ctx["tenant_id"], str(hours))

            return {
                "success": True,
                "data": [
                    {
                        "user_email": row["user_email"],
                        "failed_count": row["failed_count"],
                        "last_attempt": row["last_attempt"].isoformat(),
                        "reasons": [r for r in row["reasons"] if r]
                    }
                    for row in rows
                ],
                "hours_checked": hours
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting failed logins: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get failed logins")


@router.get("/audit/suspicious-activity")
async def get_suspicious_activity(
    request: Request,
    from_date: Optional[date] = Query(None),
    to_date: Optional[date] = Query(None),
):
    """Get suspicious login activity."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            conditions = ["(tenant_id = $1 OR tenant_id IS NULL)", "is_suspicious = true"]
            params = [ctx["tenant_id"]]
            param_idx = 2

            if from_date:
                conditions.append(f"login_time::DATE >= ${param_idx}")
                params.append(from_date)
                param_idx += 1

            if to_date:
                conditions.append(f"login_time::DATE <= ${param_idx}")
                params.append(to_date)
                param_idx += 1

            where_clause = " AND ".join(conditions)

            rows = await conn.fetch(f"""
                SELECT id, user_id, user_email, login_time, ip_address,
                       device_type, location_country, location_city, login_status
                FROM login_history
                WHERE {where_clause}
                ORDER BY login_time DESC
                LIMIT 100
            """, *params)

            return {
                "success": True,
                "data": [
                    {
                        "id": str(row["id"]),
                        "user_id": str(row["user_id"]),
                        "user_email": row["user_email"],
                        "login_time": row["login_time"].isoformat(),
                        "ip_address": str(row["ip_address"]) if row["ip_address"] else None,
                        "device_type": row["device_type"],
                        "location_country": row["location_country"],
                        "location_city": row["location_city"],
                        "login_status": row["login_status"]
                    }
                    for row in rows
                ]
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting suspicious activity: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get suspicious activity")


# =============================================================================
# SENSITIVE DATA ACCESS
# =============================================================================

@router.get("/audit/sensitive-access", response_model=SensitiveAccessListResponse)
async def list_sensitive_access(
    request: Request,
    data_type: Optional[str] = Query(None),
    user_id: Optional[UUID] = Query(None),
    from_date: Optional[date] = Query(None),
    to_date: Optional[date] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    """List sensitive data access logs."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            conditions = ["tenant_id = $1"]
            params = [ctx["tenant_id"]]
            param_idx = 2

            if data_type:
                conditions.append(f"data_type = ${param_idx}")
                params.append(data_type)
                param_idx += 1

            if user_id:
                conditions.append(f"user_id = ${param_idx}")
                params.append(user_id)
                param_idx += 1

            if from_date:
                conditions.append(f"access_time::DATE >= ${param_idx}")
                params.append(from_date)
                param_idx += 1

            if to_date:
                conditions.append(f"access_time::DATE <= ${param_idx}")
                params.append(to_date)
                param_idx += 1

            where_clause = " AND ".join(conditions)

            count_query = f"SELECT COUNT(*) FROM sensitive_data_access WHERE {where_clause}"
            total = await conn.fetchval(count_query, *params)

            query = f"""
                SELECT id, access_time, user_id, data_type, entity_type,
                       entity_id, reason, authorized_by, was_exported, export_format
                FROM sensitive_data_access
                WHERE {where_clause}
                ORDER BY access_time DESC
                LIMIT ${param_idx} OFFSET ${param_idx + 1}
            """
            params.extend([limit, skip])

            rows = await conn.fetch(query, *params)

            items = [
                {
                    "id": str(row["id"]),
                    "access_time": row["access_time"],
                    "user_id": str(row["user_id"]),
                    "data_type": row["data_type"],
                    "entity_type": row["entity_type"],
                    "entity_id": str(row["entity_id"]) if row["entity_id"] else None,
                    "reason": row["reason"],
                    "authorized_by": str(row["authorized_by"]) if row["authorized_by"] else None,
                    "was_exported": row["was_exported"],
                    "export_format": row["export_format"],
                }
                for row in rows
            ]

            return {
                "items": items,
                "total": total,
                "has_more": (skip + limit) < total
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing sensitive access: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list sensitive access")


@router.post("/audit/sensitive-access", response_model=AuditResponse, status_code=201)
async def log_sensitive_access(request: Request, body: LogSensitiveAccessRequest):
    """Log sensitive data access."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            access_id = await conn.fetchval("""
                INSERT INTO sensitive_data_access (
                    tenant_id, user_id, data_type, entity_type, entity_id,
                    reason, authorized_by, was_exported, export_format
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                RETURNING id
            """,
                ctx["tenant_id"],
                ctx["user_id"],
                body.data_type,
                body.entity_type,
                body.entity_id,
                body.reason,
                body.authorized_by,
                body.was_exported,
                body.export_format
            )

            return {
                "success": True,
                "message": "Sensitive data access logged",
                "data": {"id": str(access_id)}
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error logging sensitive access: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to log sensitive access")


# =============================================================================
# RETENTION POLICIES
# =============================================================================

@router.get("/audit/retention-policies", response_model=RetentionPolicyListResponse)
async def list_retention_policies(request: Request):
    """List audit retention policies."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT id, category, retention_days, archive_after_days,
                       delete_after_days, is_active
                FROM audit_retention_policies
                WHERE tenant_id = $1
                ORDER BY category
            """, ctx["tenant_id"])

            return {
                "success": True,
                "data": [
                    {
                        "id": str(row["id"]),
                        "category": row["category"],
                        "retention_days": row["retention_days"],
                        "archive_after_days": row["archive_after_days"],
                        "delete_after_days": row["delete_after_days"],
                        "is_active": row["is_active"],
                    }
                    for row in rows
                ]
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing retention policies: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list retention policies")


@router.patch("/audit/retention-policies/{policy_id}", response_model=AuditResponse)
async def update_retention_policy(
    request: Request,
    policy_id: UUID,
    body: UpdateRetentionPolicyRequest
):
    """Update a retention policy."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Verify ownership
            existing = await conn.fetchrow("""
                SELECT id FROM audit_retention_policies
                WHERE id = $1 AND tenant_id = $2
            """, policy_id, ctx["tenant_id"])

            if not existing:
                raise HTTPException(status_code=404, detail="Policy not found")

            # Build update
            updates = []
            params = []
            param_idx = 1

            if body.retention_days is not None:
                updates.append(f"retention_days = ${param_idx}")
                params.append(body.retention_days)
                param_idx += 1

            if body.archive_after_days is not None:
                updates.append(f"archive_after_days = ${param_idx}")
                params.append(body.archive_after_days)
                param_idx += 1

            if body.delete_after_days is not None:
                updates.append(f"delete_after_days = ${param_idx}")
                params.append(body.delete_after_days)
                param_idx += 1

            if body.is_active is not None:
                updates.append(f"is_active = ${param_idx}")
                params.append(body.is_active)
                param_idx += 1

            if not updates:
                return {"success": True, "message": "No changes", "data": None}

            updates.append("updated_at = NOW()")
            params.extend([policy_id, ctx["tenant_id"]])

            await conn.execute(f"""
                UPDATE audit_retention_policies
                SET {", ".join(updates)}
                WHERE id = ${param_idx} AND tenant_id = ${param_idx + 1}
            """, *params)

            return {
                "success": True,
                "message": "Retention policy updated",
                "data": {"id": str(policy_id)}
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating retention policy: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update retention policy")


@router.post("/audit/cleanup", response_model=CleanupResponse)
async def run_audit_cleanup(request: Request):
    """Run audit log cleanup based on retention policies."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            deleted_count = await conn.fetchval(
                "SELECT cleanup_audit_logs($1)",
                ctx["tenant_id"]
            )

            return {
                "success": True,
                "message": f"Cleanup completed. Deleted {deleted_count} records.",
                "deleted_count": deleted_count or 0
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error running audit cleanup: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to run audit cleanup")


# =============================================================================
# REPORTS
# =============================================================================

@router.get("/audit/summary", response_model=AuditSummaryResponse)
async def get_audit_summary(
    request: Request,
    from_date: Optional[date] = Query(None),
    to_date: Optional[date] = Query(None),
):
    """Get audit activity summary."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT action, entity_type, COUNT(*)::BIGINT as count
                FROM audit_logs
                WHERE tenant_id = $1
                AND ($2::DATE IS NULL OR event_time::DATE >= $2)
                AND ($3::DATE IS NULL OR event_time::DATE <= $3)
                GROUP BY action, entity_type
                ORDER BY count DESC
            """, ctx["tenant_id"], from_date, to_date)

            return {
                "success": True,
                "data": [
                    {
                        "action": row["action"],
                        "entity_type": row["entity_type"],
                        "count": row["count"]
                    }
                    for row in rows
                ],
                "period": {
                    "from_date": from_date.isoformat() if from_date else None,
                    "to_date": to_date.isoformat() if to_date else None
                }
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting audit summary: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get audit summary")


@router.get("/audit/user-activity-report")
async def get_user_activity_report(
    request: Request,
    from_date: Optional[date] = Query(None),
    to_date: Optional[date] = Query(None),
):
    """Get user activity report."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT user_id, user_email,
                       COUNT(*)::INTEGER as total_actions,
                       jsonb_object_agg(action, action_count) as actions_by_type,
                       MAX(event_time) as last_activity
                FROM (
                    SELECT user_id, user_email, action,
                           COUNT(*)::INTEGER as action_count, MAX(event_time) as event_time
                    FROM audit_logs
                    WHERE tenant_id = $1
                    AND user_id IS NOT NULL
                    AND ($2::DATE IS NULL OR event_time::DATE >= $2)
                    AND ($3::DATE IS NULL OR event_time::DATE <= $3)
                    GROUP BY user_id, user_email, action
                ) sub
                GROUP BY user_id, user_email
                ORDER BY total_actions DESC
            """, ctx["tenant_id"], from_date, to_date)

            return {
                "success": True,
                "data": [
                    {
                        "user_id": str(row["user_id"]),
                        "user_email": row["user_email"],
                        "total_actions": row["total_actions"],
                        "actions_by_type": row["actions_by_type"],
                        "last_activity": row["last_activity"].isoformat()
                    }
                    for row in rows
                ]
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting user activity report: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get user activity report")


@router.get("/audit/changes-report")
async def get_changes_report(
    request: Request,
    from_date: Optional[date] = Query(None),
    to_date: Optional[date] = Query(None),
):
    """Get data changes report by entity type and date."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT event_time::DATE as date, entity_type,
                       COUNT(CASE WHEN action = 'create' THEN 1 END)::INTEGER as creates,
                       COUNT(CASE WHEN action = 'update' THEN 1 END)::INTEGER as updates,
                       COUNT(CASE WHEN action = 'delete' THEN 1 END)::INTEGER as deletes
                FROM audit_logs
                WHERE tenant_id = $1
                AND action IN ('create', 'update', 'delete')
                AND ($2::DATE IS NULL OR event_time::DATE >= $2)
                AND ($3::DATE IS NULL OR event_time::DATE <= $3)
                GROUP BY event_time::DATE, entity_type
                ORDER BY event_time::DATE DESC, entity_type
            """, ctx["tenant_id"], from_date, to_date)

            return {
                "success": True,
                "data": [
                    {
                        "date": row["date"].isoformat(),
                        "entity_type": row["entity_type"],
                        "creates": row["creates"],
                        "updates": row["updates"],
                        "deletes": row["deletes"]
                    }
                    for row in rows
                ]
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting changes report: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get changes report")
