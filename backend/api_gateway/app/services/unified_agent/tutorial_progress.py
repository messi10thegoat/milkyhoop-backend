"""
Tutorial progress CRUD — persistent per-user tutorial state.
Separate from chat_session_state so progress survives session expiry.

Table: user_tutorial_progress (V123)
  id UUID PK, tenant_id UUID, user_id UUID, tutorial_key TEXT,
  current_step INT, status TEXT, dismissed_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ, created_at TIMESTAMPTZ, updated_at TIMESTAMPTZ
  UNIQUE(tenant_id, user_id, tutorial_key)
  RLS: tenant_id = current_setting('app.tenant_id')::uuid
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from dataclasses import dataclass

import asyncpg

from .tutorial_registry import TUTORIAL_REGISTRY

logger = logging.getLogger("unified_agent.tutorial_progress")


# ============================================================================
# DATA CLASS
# ============================================================================


@dataclass
class TutorialProgress:
    """Mirrors user_tutorial_progress row."""

    id: str
    tenant_id: str
    user_id: str
    tutorial_key: str
    current_step: int
    status: str  # active | completed | dismissed
    dismissed_at: Optional[datetime]
    completed_at: Optional[datetime]


def _row_to_progress(row: asyncpg.Record) -> TutorialProgress:
    """Convert asyncpg Record to TutorialProgress dataclass."""
    return TutorialProgress(
        id=str(row["id"]),
        tenant_id=str(row["tenant_id"]),
        user_id=str(row["user_id"]),
        tutorial_key=row["tutorial_key"],
        current_step=row["current_step"],
        status=row["status"],
        dismissed_at=row["dismissed_at"],
        completed_at=row["completed_at"],
    )


# ============================================================================
# CRUD FUNCTIONS
# ============================================================================


async def get_progress(
    conn: asyncpg.Connection,
    user_id: str,
    tutorial_key: str,
) -> Optional[TutorialProgress]:
    """Get tutorial progress for current user (RLS scoped)."""
    row = await conn.fetchrow(
        """
        SELECT id, tenant_id, user_id, tutorial_key,
               current_step, status, dismissed_at, completed_at
        FROM user_tutorial_progress
        WHERE user_id = $1 AND tutorial_key = $2
        """,
        user_id,
        tutorial_key,
    )
    if row is None:
        return None
    return _row_to_progress(row)


async def upsert_progress(
    conn: asyncpg.Connection,
    user_id: str,
    tenant_id: str,
    tutorial_key: str,
    current_step: int = 0,
    status: str = "active",
) -> TutorialProgress:
    """Create or update tutorial progress.

    Uses INSERT ... ON CONFLICT (tenant_id, user_id, tutorial_key) DO UPDATE.
    """
    row = await conn.fetchrow(
        """
        INSERT INTO user_tutorial_progress
            (tenant_id, user_id, tutorial_key, current_step, status)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (tenant_id, user_id, tutorial_key) DO UPDATE SET
            current_step = EXCLUDED.current_step,
            status = EXCLUDED.status,
            updated_at = NOW()
        RETURNING id, tenant_id, user_id, tutorial_key,
                  current_step, status, dismissed_at, completed_at
        """,
        tenant_id,
        user_id,
        tutorial_key,
        current_step,
        status,
    )
    return _row_to_progress(row)


async def advance_tutorial(
    conn: asyncpg.Connection,
    user_id: str,
    tenant_id: str,
    tutorial_key: str,
) -> Optional[int]:
    """Advance to next step. Returns new step index, or None if completed.

    If next_step >= total_steps: mark status='completed', completed_at=NOW().
    """
    config = TUTORIAL_REGISTRY.get(tutorial_key)
    if config is None:
        logger.warning("advance_tutorial: unknown tutorial_key=%s", tutorial_key)
        return None

    # Get current progress (or create at step 0)
    progress = await get_progress(conn, user_id, tutorial_key)
    if progress is None:
        # First advance — create at step 0, then advance to 1
        await upsert_progress(conn, user_id, tenant_id, tutorial_key, 0, "active")
        next_step = 1
    else:
        if progress.status == "completed":
            logger.info("advance_tutorial: already completed key=%s", tutorial_key)
            return None
        next_step = progress.current_step + 1

    if next_step >= config.total_steps:
        # Tutorial complete
        await conn.execute(
            """
            UPDATE user_tutorial_progress
            SET current_step = $1, status = 'completed',
                completed_at = NOW(), updated_at = NOW()
            WHERE user_id = $2 AND tutorial_key = $3
            """,
            next_step,
            user_id,
            tutorial_key,
        )
        logger.info("advance_tutorial: completed key=%s user=%s", tutorial_key, user_id)
        return None

    # Advance step
    await conn.execute(
        """
        UPDATE user_tutorial_progress
        SET current_step = $1, updated_at = NOW()
        WHERE user_id = $2 AND tutorial_key = $3
        """,
        next_step,
        user_id,
        tutorial_key,
    )
    logger.info(
        "advance_tutorial: key=%s step=%d/%d user=%s",
        tutorial_key,
        next_step,
        config.total_steps,
        user_id,
    )
    return next_step


async def dismiss_tutorial(
    conn: asyncpg.Connection,
    user_id: str,
    tenant_id: str,
    tutorial_key: str,
) -> None:
    """Dismiss tutorial with cooldown. Sets status='dismissed', dismissed_at=NOW().

    If no row exists yet, upsert first then dismiss.
    """
    progress = await get_progress(conn, user_id, tutorial_key)
    if progress is None:
        # Create a row so the dismissal is tracked
        await upsert_progress(conn, user_id, tenant_id, tutorial_key, 0, "active")

    await conn.execute(
        """
        UPDATE user_tutorial_progress
        SET status = 'dismissed', dismissed_at = NOW(), updated_at = NOW()
        WHERE user_id = $1 AND tutorial_key = $2
        """,
        user_id,
        tutorial_key,
    )
    logger.info("dismiss_tutorial: key=%s user=%s", tutorial_key, user_id)


async def should_auto_trigger(
    conn: asyncpg.Connection,
    user_id: str,
    tenant_id: str,
) -> Optional[str]:
    """Check if any auto_trigger tutorial should fire.

    Iterates TUTORIAL_REGISTRY for tutorials with auto_trigger=True.
    Skip if:
      - completed (status='completed')
      - dismissed AND within cooldown_hours window
    Returns tutorial_key or None.
    """
    now = datetime.now(timezone.utc)

    for key, config in TUTORIAL_REGISTRY.items():
        if not config.auto_trigger:
            continue

        progress = await get_progress(conn, user_id, key)

        if progress is None:
            # Never seen — eligible to trigger
            logger.info("should_auto_trigger: eligible key=%s (new)", key)
            return key

        if progress.status == "completed":
            # Already completed — skip
            continue

        if progress.status == "active":
            # Already active — return it so the frontend can resume
            logger.info(
                "should_auto_trigger: resuming key=%s step=%d",
                key,
                progress.current_step,
            )
            return key

        if progress.status == "dismissed":
            # Check cooldown
            if progress.dismissed_at is not None:
                cooldown_end = progress.dismissed_at + timedelta(
                    hours=config.cooldown_hours
                )
                if now < cooldown_end:
                    # Still in cooldown — skip
                    continue
            # Cooldown expired — eligible to re-trigger
            logger.info("should_auto_trigger: cooldown expired key=%s", key)
            return key

    return None


async def get_active_tutorial(
    conn: asyncpg.Connection,
    user_id: str,
) -> Optional[TutorialProgress]:
    """Get currently active tutorial (status='active', most recently updated)."""
    row = await conn.fetchrow(
        """
        SELECT id, tenant_id, user_id, tutorial_key,
               current_step, status, dismissed_at, completed_at
        FROM user_tutorial_progress
        WHERE user_id = $1 AND status = 'active'
        ORDER BY updated_at DESC
        LIMIT 1
        """,
        user_id,
    )
    if row is None:
        return None
    return _row_to_progress(row)
