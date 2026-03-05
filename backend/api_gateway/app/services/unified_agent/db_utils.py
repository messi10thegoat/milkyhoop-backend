"""
Database utilities for session management.

Lightweight asyncpg pool management for 4-layer memory tables.
Separate from Prisma since chat_* tables are managed via SQL directly.
"""
import asyncpg
import os
from typing import Optional

_session_db_pool: Optional[asyncpg.Pool] = None


async def get_session_db_pool() -> asyncpg.Pool:
    """Get or create database pool for session tables.

    Singleton pattern - creates pool once, reuses across requests.
    """
    global _session_db_pool

    if _session_db_pool is None:
        # Read from environment
        database_url = os.getenv("DATABASE_URL")
        if not database_url:
            # Build from components
            db_host = os.getenv("DB_HOST", "postgres")
            db_port = os.getenv("DB_PORT", "5432")
            db_user = os.getenv("DB_USER", "postgres")
            db_password = os.getenv("DB_PASSWORD", "")
            db_name = os.getenv("DB_NAME", "milkydb")
            database_url = (
                f"postgresql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"
            )

        _session_db_pool = await asyncpg.create_pool(
            database_url,
            min_size=2,
            max_size=10,
            command_timeout=60,
        )

    return _session_db_pool


async def close_session_db_pool():
    """Close database pool on shutdown."""
    global _session_db_pool
    if _session_db_pool:
        await _session_db_pool.close()
        _session_db_pool = None
