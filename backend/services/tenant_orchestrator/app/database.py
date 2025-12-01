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


# ============================================
# ATOMIC TRANSACTION FUNCTION
# ============================================

async def get_tenant_config(tenant_id: str) -> dict:
    """
    Get tenant feature flags from tenant_config table.
    Returns dict with use_atomic_function, use_listen_notify_worker, etc.
    """
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM get_tenant_config($1::TEXT)
                """,
                tenant_id
            )
            if row:
                return {
                    "use_atomic_function": row["use_atomic_function"],
                    "use_listen_notify_worker": row["use_listen_notify_worker"],
                    "worker_poll_interval_ms": row["worker_poll_interval_ms"],
                    "max_retry_count": row["max_retry_count"],
                    "batch_size": row["batch_size"],
                    "enable_telemetry": row["enable_telemetry"]
                }
        return {
            "use_atomic_function": False,
            "use_listen_notify_worker": False
        }
    except Exception as e:
        logger.error(f"❌ Error fetching tenant config: {e}")
        return {
            "use_atomic_function": False,
            "use_listen_notify_worker": False
        }


async def create_transaction_atomic(
    tx_id: str,
    tenant_id: str,
    created_by: str,
    actor_role: str,
    jenis_transaksi: str,
    payload: dict,
    total_nominal: int,
    metode_pembayaran: str,
    nama_pihak: str,
    keterangan: str,
    idempotency_key: str,
    items: list,
    outbox_events: list,
    # Optional fields
    discount_type: str = None,
    discount_value: float = 0,
    discount_amount: int = 0,
    subtotal_before_discount: int = 0,
    subtotal_after_discount: int = 0,
    include_vat: bool = False,
    vat_amount: int = 0,
    grand_total: int = 0,
    # SAK EMKM fields
    status_pembayaran: str = None,
    nominal_dibayar: int = None,
    sisa_piutang_hutang: int = None,
    jatuh_tempo: int = None,
    kontak_pihak: str = None,
    pihak_type: str = None,
    lokasi_gudang: str = None,
    kategori_arus_kas: str = "operasi",
    is_prive: bool = False,
    is_modal: bool = False,
    rekening_id: str = None,
    rekening_type: str = None
) -> dict:
    """
    Create transaction + items + outbox in ONE atomic DB call.
    Uses PostgreSQL stored function create_transaction_atomic().
    Target: <150ms total (down from 280-350ms)

    Returns:
        {
            "success": bool,
            "transaction_id": str,
            "created_at": datetime,
            "items_count": int,
            "outbox_count": int,
            "execution_time_ms": float,
            "is_idempotent": bool
        }
    """
    import json
    import time

    t_start = time.perf_counter()

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            # Convert to JSON strings
            payload_json = json.dumps(payload, ensure_ascii=False)
            items_json = json.dumps(items, ensure_ascii=False)
            outbox_json = json.dumps(outbox_events, ensure_ascii=False)

            row = await conn.fetchrow(
                """
                SELECT * FROM create_transaction_atomic(
                    p_id := $1,
                    p_tenant_id := $2,
                    p_created_by := $3,
                    p_actor_role := $4,
                    p_jenis_transaksi := $5,
                    p_payload := $6::JSONB,
                    p_total_nominal := $7::BIGINT,
                    p_metode_pembayaran := $8,
                    p_nama_pihak := $9,
                    p_keterangan := $10,
                    p_discount_type := $11,
                    p_discount_value := $12::FLOAT,
                    p_discount_amount := $13::BIGINT,
                    p_subtotal_before_discount := $14::BIGINT,
                    p_subtotal_after_discount := $15::BIGINT,
                    p_include_vat := $16::BOOLEAN,
                    p_vat_amount := $17::BIGINT,
                    p_grand_total := $18::BIGINT,
                    p_idempotency_key := $19,
                    p_status_pembayaran := $20,
                    p_nominal_dibayar := $21::BIGINT,
                    p_sisa_piutang_hutang := $22::BIGINT,
                    p_jatuh_tempo := $23::BIGINT,
                    p_kontak_pihak := $24,
                    p_pihak_type := $25,
                    p_lokasi_gudang := $26,
                    p_kategori_arus_kas := $27,
                    p_is_prive := $28::BOOLEAN,
                    p_is_modal := $29::BOOLEAN,
                    p_rekening_id := $30,
                    p_rekening_type := $31,
                    p_items := $32::JSONB,
                    p_outbox_events := $33::JSONB
                )
                """,
                tx_id, tenant_id, created_by, actor_role, jenis_transaksi,
                payload_json, total_nominal, metode_pembayaran, nama_pihak, keterangan,
                discount_type, discount_value, discount_amount,
                subtotal_before_discount, subtotal_after_discount,
                include_vat, vat_amount, grand_total, idempotency_key,
                status_pembayaran, nominal_dibayar, sisa_piutang_hutang, jatuh_tempo,
                kontak_pihak, pihak_type, lokasi_gudang, kategori_arus_kas,
                is_prive, is_modal, rekening_id, rekening_type,
                items_json, outbox_json
            )

            elapsed_ms = (time.perf_counter() - t_start) * 1000

            if row:
                result = {
                    "success": True,
                    "transaction_id": row["transaction_id"],
                    "created_at": row["created_at"],
                    "items_count": row["items_count"],
                    "outbox_count": row["outbox_count"],
                    "execution_time_ms": row["execution_time_ms"],
                    "is_idempotent": row["is_idempotent"],
                    "total_elapsed_ms": elapsed_ms
                }
                logger.info(f"✅ Atomic transaction created: {tx_id} in {elapsed_ms:.1f}ms (DB: {row['execution_time_ms']:.1f}ms)")
                return result
            else:
                logger.error(f"❌ Atomic transaction returned no result")
                return {"success": False, "error": "No result returned"}

    except Exception as e:
        elapsed_ms = (time.perf_counter() - t_start) * 1000
        logger.error(f"❌ Atomic transaction error: {e} (after {elapsed_ms:.1f}ms)")
        return {"success": False, "error": str(e)}
