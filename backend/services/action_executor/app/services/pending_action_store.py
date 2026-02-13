"""
Pending Action Store (v2 - matches actual DB schema)

Database operations for the pending_actions table.
Handles CRUD + state transitions with optimistic locking.

IRON LAW 14: All operations use idempotency_key for duplicate prevention.
IRON LAW 13: State transitions use version-based optimistic locking.
"""
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import asyncpg

logger = logging.getLogger(__name__)


class PendingActionStore:
    """Database operations for pending_actions table."""

    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    async def create_pending_action(
        self,
        tenant_id: str,
        user_id: str,
        action_id: str,
        action_type: str,
        category: str,
        draft_payload: dict,
        idempotency_key: str,
        confidence: float,
        assumptions: list,
        preview_data: dict,
        ttl_minutes: int = 15,
    ) -> Dict[str, Any]:
        """
        Create a new pending_action record (status=PENDING).
        Returns: {"id", "confirmation_token", "expires_at", "trace_id"}
        """
        pending_id = str(uuid.uuid4())
        confirmation_token = str(uuid.uuid4())
        trace_id = str(uuid.uuid4())
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)

        # Pack everything into the action_plan jsonb
        action_plan_json = {
            "action_id": action_id,
            "action_type": action_type,
            "category": category,
            "draft_payload": draft_payload,
            "confidence": confidence,
            "assumptions": assumptions or [],
            "confirmation_token": confirmation_token,
        }

        try:
            await self.pool.execute(
                """
                INSERT INTO pending_actions (
                    id, tenant_id, user_id, action_id, action_type,
                    action_category, action_plan, status,
                    idempotency_key, expires_at, trace_id, version,
                    dry_run_preview
                ) VALUES (
                    $1, $2, $3, $4, $5,
                    $6, $7, 'PENDING',
                    $8, $9, $10, 1,
                    $11
                )
                """,
                pending_id, tenant_id, user_id, action_id, action_type,
                category, json.dumps(action_plan_json),
                idempotency_key or None, expires_at, trace_id,
                json.dumps(preview_data) if preview_data else None,
            )

            # Also store idempotency key if provided
            if idempotency_key:
                try:
                    await self.pool.execute(
                        """
                        INSERT INTO idempotency_keys (key, tenant_id, pending_action_id, expires_at)
                        VALUES ($1, $2, $3::uuid, $4)
                        ON CONFLICT (key) DO NOTHING
                        """,
                        idempotency_key, tenant_id, pending_id, expires_at,
                    )
                except Exception as e:
                    logger.warning(f"Failed to store idempotency key: {e}")

            return {
                "id": pending_id,
                "confirmation_token": confirmation_token,
                "trace_id": trace_id,
                "expires_at": expires_at,
            }

        except asyncpg.UniqueViolationError as e:
            logger.warning(f"Duplicate pending action: {e}")
            raise
        except Exception as e:
            logger.error(f"Failed to create pending action: {e}", exc_info=True)
            raise

    async def get_pending_action(
        self, pending_action_id: str, tenant_id: str
    ) -> Optional[Dict[str, Any]]:
        """Get a pending action by ID."""
        row = await self.pool.fetchrow(
            """
            SELECT id, tenant_id, user_id, action_id, action_type,
                   action_category, action_plan, status,
                   idempotency_key, expires_at, trace_id, version,
                   created_at, executed_at, result, error_message,
                   validation_result, dry_run_preview
            FROM pending_actions
            WHERE id = $1 AND tenant_id = $2
            """,
            pending_action_id, tenant_id,
        )
        if row is None:
            return None

        result = dict(row)
        # Parse action_plan from jsonb string
        action_plan = result.get("action_plan")
        if isinstance(action_plan, str):
            action_plan = json.loads(action_plan)

        # Extract confirmation_token and draft_payload from action_plan
        result["confirmation_token"] = action_plan.get("confirmation_token", "") if action_plan else ""
        result["draft_payload"] = json.dumps(action_plan.get("draft_payload", {})) if action_plan else "{}"
        result["category"] = result.get("action_category", "DOCUMENT")

        return result

    async def transition_to_executing(
        self, pending_action_id: str, tenant_id: str, expected_version: int
    ) -> bool:
        """
        Atomically transition PENDING → EXECUTING with optimistic locking.
        Returns True if successful, False if version mismatch or wrong state.
        """
        result = await self.pool.execute(
            """
            UPDATE pending_actions
            SET status = 'EXECUTING', version = version + 1
            WHERE id = $1 AND tenant_id = $2
              AND status = 'PENDING' AND version = $3
              AND expires_at > NOW()
            """,
            pending_action_id, tenant_id, expected_version,
        )
        updated = int(result.split(" ")[1]) > 0
        if not updated:
            logger.warning(
                f"Failed to transition {pending_action_id} to EXECUTING "
                f"(expected version {expected_version})"
            )
        return updated

    async def mark_completed(
        self, pending_action_id: str, tenant_id: str, result_data: dict
    ) -> bool:
        """Mark action as COMPLETED with result data."""
        result = await self.pool.execute(
            """
            UPDATE pending_actions
            SET status = 'COMPLETED',
                result = $3,
                executed_at = NOW(),
                version = version + 1
            WHERE id = $1 AND tenant_id = $2 AND status = 'EXECUTING'
            """,
            pending_action_id, tenant_id, json.dumps(result_data),
        )
        updated = int(result.split(" ")[1]) > 0

        # Update idempotency key result
        if updated and result_data:
            try:
                await self.pool.execute(
                    """
                    UPDATE idempotency_keys SET result = $2
                    WHERE pending_action_id = $1::uuid
                    """,
                    pending_action_id, json.dumps(result_data),
                )
            except Exception as e:
                logger.warning(f"Failed to update idempotency result: {e}")

        return updated

    async def mark_failed(
        self, pending_action_id: str, tenant_id: str, error_message: str, error_code: str = ""
    ) -> bool:
        """Mark action as FAILED with error info."""
        result = await self.pool.execute(
            """
            UPDATE pending_actions
            SET status = 'FAILED',
                error_message = $3,
                executed_at = NOW(),
                version = version + 1
            WHERE id = $1 AND tenant_id = $2 AND status = 'EXECUTING'
            """,
            pending_action_id, tenant_id, f"[{error_code}] {error_message}" if error_code else error_message,
        )
        return int(result.split(" ")[1]) > 0

    async def mark_cancelled(
        self, pending_action_id: str, tenant_id: str, reason: str = ""
    ) -> bool:
        """Cancel a pending action."""
        result = await self.pool.execute(
            """
            UPDATE pending_actions
            SET status = 'CANCELLED',
                error_message = $3,
                version = version + 1
            WHERE id = $1 AND tenant_id = $2 AND status = 'PENDING'
            """,
            pending_action_id, tenant_id, reason or "Cancelled by user",
        )
        return int(result.split(" ")[1]) > 0

    async def expire_old_actions(self) -> int:
        """Expire pending actions past their TTL. Returns count expired."""
        result = await self.pool.execute(
            """
            UPDATE pending_actions
            SET status = 'EXPIRED', version = version + 1
            WHERE status = 'PENDING' AND expires_at < NOW()
            """
        )
        count = int(result.split(" ")[1])
        if count > 0:
            logger.info(f"Expired {count} pending actions")
        return count
