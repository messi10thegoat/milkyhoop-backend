"""
Database utilities for Policy Engine

Provides async database connection pool management.
"""
import asyncio
import logging
from typing import Optional, Any
from contextlib import asynccontextmanager

import asyncpg
from asyncpg import Pool, Connection

from ..config import settings

logger = logging.getLogger(__name__)


class DatabasePool:
    """
    Async database connection pool manager.
    
    Usage:
        pool = DatabasePool()
        await pool.initialize()
        
        async with pool.acquire() as conn:
            result = await conn.fetch("SELECT * FROM users")
        
        await pool.close()
    """
    
    def __init__(self, database_url: Optional[str] = None):
        self.database_url = database_url or settings.db.url
        self._pool: Optional[Pool] = None
        self._lock = asyncio.Lock()
    
    async def initialize(self) -> None:
        """Initialize the connection pool"""
        if self._pool is not None:
            return
        
        async with self._lock:
            if self._pool is not None:
                return
            
            try:
                self._pool = await asyncpg.create_pool(
                    self.database_url,
                    min_size=2,
                    max_size=settings.db.pool_size,
                    max_inactive_connection_lifetime=settings.db.pool_recycle,
                    command_timeout=settings.db.pool_timeout,
                )
                logger.info("Database pool initialized successfully")
            except Exception as e:
                logger.error(f"Failed to initialize database pool: {e}")
                raise
    
    async def close(self) -> None:
        """Close the connection pool"""
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
            logger.info("Database pool closed")
    
    @asynccontextmanager
    async def acquire(self):
        """Acquire a connection from the pool"""
        if self._pool is None:
            await self.initialize()
        
        async with self._pool.acquire() as connection:
            yield connection
    
    async def execute(self, query: str, *args) -> str:
        """Execute a query and return status"""
        async with self.acquire() as conn:
            return await conn.execute(query, *args)
    
    async def fetch(self, query: str, *args) -> list:
        """Fetch all rows"""
        async with self.acquire() as conn:
            return await conn.fetch(query, *args)
    
    async def fetchrow(self, query: str, *args) -> Optional[Any]:
        """Fetch single row"""
        async with self.acquire() as conn:
            return await conn.fetchrow(query, *args)
    
    async def fetchval(self, query: str, *args) -> Optional[Any]:
        """Fetch single value"""
        async with self.acquire() as conn:
            return await conn.fetchval(query, *args)


# Global pool instance
_pool: Optional[DatabasePool] = None


async def get_pool() -> DatabasePool:
    """Get or create the global database pool"""
    global _pool
    if _pool is None:
        _pool = DatabasePool()
        await _pool.initialize()
    return _pool


async def close_pool() -> None:
    """Close the global database pool"""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
