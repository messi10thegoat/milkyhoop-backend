"""
Product Analytics Queries
SQL queries for top products and low-sell products analytics

Phase 2 Implementation - Financial Analytics
"""

import logging

logger = logging.getLogger(__name__)


async def get_top_products(prisma, tenant_id: str, start_ts: int, end_ts: int, limit: int = 10):
    """
    Query top selling products by quantity
    
    Args:
        prisma: Prisma client instance
        tenant_id: Tenant ID for isolation
        start_ts: Start timestamp (unix seconds)
        end_ts: End timestamp (unix seconds)
        limit: Maximum number of products to return
        
    Returns:
        List[dict] with product_name, quantity_sold, revenue, etc.
    """
    logger.info(f"📊 get_top_products: tenant={tenant_id}, range={start_ts}-{end_ts}, limit={limit}")
    
    try:
        # Convert unix seconds to milliseconds for database query
        start_ts_ms = start_ts * 1000
        end_ts_ms = end_ts * 1000
        
        # SQL query to get top selling products by quantity
        query = """
        SELECT 
            ip.nama_produk as product_name,
            SUM(ip.jumlah) as total_quantity_sold,
            SUM(ip.subtotal) as total_revenue,
            COUNT(DISTINCT th.id) as transaction_count,
            ip.satuan as unit
        FROM item_transaksi ip
        JOIN transaksi_harian th ON th.id = ip.transaksi_id
        WHERE th.tenant_id = $1
          AND th.jenis_transaksi = 'penjualan'
          AND th.timestamp >= $2
          AND th.timestamp <= $3
          AND th.status != 'deleted'
        GROUP BY ip.nama_produk, ip.satuan
        ORDER BY total_quantity_sold DESC
        LIMIT $4;
        """
        
        # Execute raw SQL query via Prisma
        results = await prisma.query_raw(query, tenant_id, start_ts_ms, end_ts_ms, limit)
        
        logger.info(f"✅ Found {len(results)} top products")
        return results
        
    except Exception as e:
        logger.error(f"❌ get_top_products failed: {e}", exc_info=True)
        return []


async def get_low_sell_products(
    prisma, 
    tenant_id: str, 
    start_ts: int, 
    end_ts: int, 
    threshold: float = 10.0, 
    limit: int = 10
):
    """
    Query low-selling products (low turnover ratio)
    
    Args:
        prisma: Prisma client instance
        tenant_id: Tenant ID for isolation
        start_ts: Start timestamp (unix seconds)
        end_ts: End timestamp (unix seconds)
        threshold: Turnover percentage threshold (e.g., 10.0 = 10%)
        limit: Maximum number of products to return
        
    Returns:
        List[dict] with product_name, quantity_sold, current_stock, turnover_percentage, etc.
    """
    logger.info(f"📊 get_low_sell_products: tenant={tenant_id}, threshold={threshold}%, limit={limit}")
    
    try:
        # Convert unix seconds to milliseconds for database query
        start_ts_ms = start_ts * 1000
        end_ts_ms = end_ts * 1000
        
        # SQL query to get products with low turnover (sales vs stock)
        query = """
        SELECT 
            p.produk_id as product_name,
            p.jumlah as current_stock,
            p.satuan as unit,
            COALESCE(SUM(ip.jumlah), 0) as quantity_sold,
            COALESCE(SUM(ip.subtotal), 0) as revenue,
            CASE 
                WHEN p.jumlah > 0 THEN (COALESCE(SUM(ip.jumlah), 0) / p.jumlah) * 100
                ELSE 0
            END as turnover_percentage
        FROM persediaan p
        LEFT JOIN item_transaksi ip ON ip.nama_produk = p.produk_id
        LEFT JOIN transaksi_harian th ON th.id = ip.transaksi_id 
            AND th.tenant_id = p.tenant_id
            AND th.jenis_transaksi = 'penjualan'
            AND th.timestamp >= $2
            AND th.timestamp <= $3
            AND th.status != 'deleted'
        WHERE p.tenant_id = $1
        GROUP BY p.produk_id, p.jumlah, p.satuan
        HAVING CASE 
            WHEN p.jumlah > 0 THEN (COALESCE(SUM(ip.jumlah), 0) / p.jumlah) * 100
            ELSE 0
        END < $4
        ORDER BY turnover_percentage ASC
        LIMIT $5;
        """
        
        # Execute raw SQL query via Prisma
        results = await prisma.query_raw(query, tenant_id, start_ts_ms, end_ts_ms, threshold, limit)
        
        logger.info(f"✅ Found {len(results)} low-sell products")
        return results
        
    except Exception as e:
        logger.error(f"❌ get_low_sell_products failed: {e}", exc_info=True)
        return []