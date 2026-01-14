"""
Product Search Service with Fuzzy Matching
Uses rapidfuzz for Levenshtein distance-based product name matching
"""

import logging
from typing import List, Dict, Optional
from rapidfuzz import fuzz
from prisma_client import prisma

logger = logging.getLogger(__name__)


async def search_products_fuzzy(
    tenant_id: str,
    query: str,
    limit: int = 10
) -> List[Dict]:
    """
    Search products by name using fuzzy matching (Levenshtein distance)

    Sprint 2.1: Refactored to use Products table (master data)
    Joins with Persediaan to get current stock levels

    Args:
        tenant_id: Tenant identifier for multi-tenant isolation
        query: Product name to search (natural language input)
        limit: Maximum number of results to return (default: 10)

    Returns:
        List of product matches sorted by similarity score (highest first)
        Each match contains:
        - produk_id: Product ID (UUID from Products table)
        - nama_produk: Product name
        - satuan: Unit of measure
        - current_stock: Available stock quantity
        - similarity_score: 0-100 (percentage match)
        - lokasi_gudang: Primary warehouse location

    Similarity Thresholds (as per Sprint 2.1 spec):
    - >90%: Exact match → auto-select
    - 70-90%: Ambiguous → ask user to choose
    - 60-69%: Soft duplicate warning (for new product creation)
    - <60%: No match → propose new product
    """

    try:
        # Fetch all products for this tenant from Products table
        # Join with Persediaan to get stock levels
        # Use raw SQL since Prisma client hasn't been regenerated yet
        products_raw = await prisma.query_raw(
            """
            SELECT
                p.id as produk_id,
                p.nama_produk,
                p.satuan,
                COALESCE(SUM(pers.jumlah), 0) as current_stock,
                COALESCE(pers.lokasi_gudang, 'Gudang Utama') as lokasi_gudang
            FROM products p
            LEFT JOIN persediaan pers ON pers.product_id = p.id
            WHERE p.tenant_id = $1
            GROUP BY p.id, p.nama_produk, p.satuan, pers.lokasi_gudang
            """,
            tenant_id
        )

        if not products_raw:
            logger.info(f"No products found for tenant {tenant_id}")
            return []

        # Calculate similarity scores for each product
        matches = []
        query_lower = query.lower().strip()

        for product in products_raw:
            nama_produk = product['nama_produk'] or ""
            nama_lower = nama_produk.lower().strip()

            # Use token_sort_ratio for better matching of reordered words
            # Example: "kopi susu" matches "susu kopi" with high score
            similarity = fuzz.token_sort_ratio(query_lower, nama_lower)

            matches.append({
                'produk_id': product['produk_id'],
                'nama_produk': nama_produk,
                'satuan': product['satuan'] or "pcs",
                'current_stock': float(product['current_stock'] or 0),
                'similarity_score': int(similarity),
                'lokasi_gudang': product['lokasi_gudang'] or "Gudang Utama",
            })

        # Sort by similarity score (descending) and limit results
        matches.sort(key=lambda x: x['similarity_score'], reverse=True)
        top_matches = matches[:limit]

        # Log search results for debugging
        if top_matches:
            top_score = top_matches[0]['similarity_score']
            logger.info(
                f"Product search: query='{query}' tenant={tenant_id} "
                f"found={len(matches)} top_score={top_score}%"
            )
            if top_score >= 90:
                logger.info(f"  → EXACT MATCH: {top_matches[0]['nama_produk']}")
            elif top_score >= 70:
                logger.info(f"  → AMBIGUOUS ({len([m for m in top_matches if m['similarity_score'] >= 70])} matches)")
            else:
                logger.info(f"  → NO MATCH (top score <70%)")
        else:
            logger.info(f"Product search: query='{query}' tenant={tenant_id} found=0")

        return top_matches

    except Exception as e:
        logger.error(f"Error in search_products_fuzzy: {e}", exc_info=True)
        raise


async def validate_product_exists(tenant_id: str, produk_id: str) -> Optional[Dict]:
    """
    Validate that a product exists and return its details

    Args:
        tenant_id: Tenant identifier
        produk_id: Product identifier to validate

    Returns:
        Product details dict or None if not found
    """
    try:
        product = await prisma.persediaan.find_first(
            where={
                'tenantId': tenant_id,  # Prisma uses camelCase
                'produkId': produk_id,  # Prisma uses camelCase
            }
        )

        if not product:
            return None

        return {
            'produk_id': product.produkId,
            'nama_produk': product.produkId or "",  # produkId IS the product name
            'satuan': product.satuan or "pcs",
            'current_stock': float(product.jumlah or 0),
            'lokasi_gudang': product.lokasiGudang or "Gudang Utama",
        }

    except Exception as e:
        logger.error(f"Error in validate_product_exists: {e}", exc_info=True)
        return None


async def check_duplicate_product_name(tenant_id: str, nama_produk: str, threshold: int = 60) -> List[Dict]:
    """
    Check for potential duplicate product names before creating new product

    Args:
        tenant_id: Tenant identifier
        nama_produk: Proposed product name
        threshold: Minimum similarity score to consider as potential duplicate (default: 60%)

    Returns:
        List of potential duplicates with similarity >= threshold
    """
    try:
        all_matches = await search_products_fuzzy(tenant_id, nama_produk, limit=100)

        # Filter matches above threshold
        duplicates = [m for m in all_matches if m['similarity_score'] >= threshold]

        if duplicates:
            logger.warning(
                f"Potential duplicate product: '{nama_produk}' similar to "
                f"{len(duplicates)} existing products (threshold={threshold}%)"
            )
            for dup in duplicates[:3]:  # Log top 3
                logger.warning(f"  - {dup['nama_produk']} ({dup['similarity_score']}%)")

        return duplicates

    except Exception as e:
        logger.error(f"Error in check_duplicate_product_name: {e}", exc_info=True)
        return []


async def create_product(tenant_id: str, nama_produk: str, satuan: str, kategori: str = None, barcode: str = None) -> Optional[str]:
    """
    Create a new product in Products table (Sprint 2.1)

    Args:
        tenant_id: Tenant identifier
        nama_produk: Product name (unique per tenant)
        satuan: Unit of measure (pcs, kg, liter, etc.)
        kategori: Optional product category
        barcode: Optional product barcode from scanner

    Returns:
        Product ID (UUID) if created successfully, None if failed
    """
    try:
        # Check if product already exists
        existing = await prisma.query_raw(
            "SELECT id::text FROM products WHERE tenant_id = $1 AND nama_produk = $2",
            tenant_id, nama_produk
        )

        if existing and len(existing) > 0:
            existing_id = existing[0]['id']
            # If barcode provided and product exists, update barcode
            if barcode:
                await prisma.query_raw(
                    "UPDATE products SET barcode = $1 WHERE id = $2::uuid",
                    barcode, existing_id
                )
                logger.info(f"✅ Updated barcode for existing product '{nama_produk}': {barcode}")
            else:
                logger.warning(f"Product '{nama_produk}' already exists for tenant {tenant_id}")
            return existing_id

        # Create new product with barcode
        result = await prisma.query_raw(
            """
            INSERT INTO products (tenant_id, nama_produk, satuan, kategori, barcode)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING id::text
            """,
            tenant_id, nama_produk, satuan, kategori, barcode
        )

        product_id = result[0]['id'] if (result and len(result) > 0) else None

        if product_id:
            logger.info(f"✅ Created new product: '{nama_produk}' (id={product_id}, satuan={satuan}, barcode={barcode})")
        else:
            logger.error(f"❌ Failed to create product '{nama_produk}'")

        return product_id

    except Exception as e:
        logger.error(f"Error in create_product: {e}", exc_info=True)
        return None