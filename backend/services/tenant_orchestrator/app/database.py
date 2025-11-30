"""
Raw SQL database client using asyncpg
Bypass Prisma to avoid "client not generated" issues
"""
import asyncpg
import os
from typing import Optional
import logging

logger = logging.getLogger(__name__)

# Global connection pool
_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
    """Get or create connection pool"""
    global _pool
    if _pool is None:
        database_url = os.getenv("DATABASE_URL", "")
        if not database_url:
            raise ValueError("DATABASE_URL environment variable not set")

        _pool = await asyncpg.create_pool(database_url, min_size=1, max_size=5)
        logger.info("✅ Database pool created")
    return _pool


async def fetch_product_barcode(product_id: Optional[str], product_name: Optional[str], tenant_id: str) -> Optional[str]:
    """
    Fetch product barcode from database using raw SQL.
    First tries by product_id, then falls back to product_name.
    """
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            # First try by product_id
            if product_id:
                row = await conn.fetchrow(
                    'SELECT barcode FROM products WHERE id = $1',
                    product_id
                )
                if row and row['barcode']:
                    logger.info(f"✅ Found barcode by ID: {row['barcode']}")
                    return row['barcode']

            # Fallback: search by product name
            if product_name and tenant_id:
                row = await conn.fetchrow(
                    'SELECT barcode FROM products WHERE tenant_id = $1 AND nama_produk = $2',
                    tenant_id, product_name
                )
                if row and row['barcode']:
                    logger.info(f"✅ Found barcode by name: {row['barcode']}")
                    return row['barcode']

        return None
    except Exception as e:
        logger.error(f"❌ Error fetching barcode: {e}")
        return None


async def update_product_harga_jual(product_id: str, harga_jual: float) -> bool:
    """Update product's selling price using raw SQL"""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                'UPDATE products SET harga_jual = $1 WHERE id = $2',
                harga_jual, product_id
            )
            logger.info(f"✅ Updated product harga_jual: {product_id} -> {harga_jual}")
            return True
    except Exception as e:
        logger.error(f"❌ Error updating harga_jual: {e}")
        return False


async def fetch_tenant_display_name(tenant_id: str) -> Optional[str]:
    """
    Fetch tenant display_name from database.
    Used for receipt header.
    """
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                'SELECT display_name FROM "Tenant" WHERE id = $1',
                tenant_id
            )
            if row and row['display_name']:
                logger.info(f"✅ Found tenant display_name: {row['display_name']}")
                return row['display_name']
        return None
    except Exception as e:
        logger.error(f"❌ Error fetching tenant display_name: {e}")
        return None


async def close_pool():
    """Close connection pool"""
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
        logger.info("⛔ Database pool closed")
