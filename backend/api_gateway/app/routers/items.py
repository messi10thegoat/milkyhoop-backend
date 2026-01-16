"""
Items Router - Master Data for Goods and Services

This router provides CRUD operations for items (products/services).
Items are the master data used in sales and purchase transactions.

Endpoints:
- GET  /items              - List all items
- GET  /items/{id}         - Get item detail
- POST /items              - Create item
- PUT  /items/{id}         - Update item
- DELETE /items/{id}       - Delete item
- GET  /items/units        - Get available units
- POST /items/units        - Create custom unit
- GET  /items/accounts     - Get accounts for dropdowns
- GET  /items/taxes        - Get tax options
"""

from fastapi import APIRouter, HTTPException, Request, Query
from typing import Optional, List
import logging
import asyncpg
from uuid import UUID
from datetime import datetime

from ..schemas.items import (
    CreateItemRequest, UpdateItemRequest,
    CreateItemResponse, UpdateItemResponse, DeleteItemResponse,
    ItemListResponse, ItemListItem, ItemDetailResponse,
    UnitListResponse, CreateUnitRequest, CreateUnitResponse,
    AccountsResponse, AccountOption, TaxOptionsResponse, TaxOption,
    ItemsSummaryResponse, UnitConversionResponse,
    CoaAccountOption, CoaAccountsResponse,
    DEFAULT_UNITS, SALES_ACCOUNTS, PURCHASE_ACCOUNTS
)
from ..config import settings

logger = logging.getLogger(__name__)
router = APIRouter()


# =============================================================================
# DATABASE HELPERS
# =============================================================================

async def get_db_connection():
    """Get database connection using environment variables"""
    db_config = settings.get_db_config()
    return await asyncpg.connect(**db_config)


def get_tenant_id(request: Request) -> str:
    """Extract tenant_id from authenticated request"""
    if not hasattr(request.state, 'user') or not request.state.user:
        raise HTTPException(status_code=401, detail="Authentication required")

    tenant_id = request.state.user.get("tenant_id")
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Invalid user context")

    return tenant_id


# =============================================================================
# LIST ITEMS
# =============================================================================

@router.get("/items", response_model=ItemListResponse)
async def list_items(
    request: Request,
    item_type: Optional[str] = Query(None, description="Filter by type: goods, service"),
    track_inventory: Optional[bool] = Query(None, description="Filter by track_inventory"),
    search: Optional[str] = Query(None, description="Search by name or barcode"),
    kategori: Optional[str] = Query(None, description="Filter by category"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0)
):
    """
    List all items with optional filtering.

    Use track_inventory=true to get only items that are tracked in inventory.
    """
    tenant_id = get_tenant_id(request)
    conn = None

    try:
        conn = await get_db_connection()

        # Build query with filters
        query_parts = ["SELECT p.*, per.jumlah as current_stock, per.total_nilai as stock_value"]
        query_parts.append("FROM products p")
        query_parts.append("LEFT JOIN persediaan per ON per.product_id = p.id AND per.tenant_id = p.tenant_id")
        query_parts.append("WHERE p.tenant_id = $1")

        params = [tenant_id]
        param_idx = 2

        if item_type:
            query_parts.append(f"AND p.item_type = ${param_idx}")
            params.append(item_type)
            param_idx += 1

        if track_inventory is not None:
            query_parts.append(f"AND p.track_inventory = ${param_idx}")
            params.append(track_inventory)
            param_idx += 1

        if kategori:
            query_parts.append(f"AND p.kategori = ${param_idx}")
            params.append(kategori)
            param_idx += 1

        if search:
            query_parts.append(f"AND (p.nama_produk ILIKE ${param_idx} OR p.barcode ILIKE ${param_idx})")
            params.append(f"%{search}%")
            param_idx += 1

        # Count total
        count_query = " ".join(query_parts).replace(
            "SELECT p.*, per.jumlah as current_stock, per.total_nilai as stock_value",
            "SELECT COUNT(*)"
        )
        total = await conn.fetchval(count_query, *params)

        # Add ordering and pagination
        query_parts.append("ORDER BY p.nama_produk ASC")
        query_parts.append(f"LIMIT ${param_idx} OFFSET ${param_idx + 1}")
        params.extend([limit, offset])

        # Execute query
        query = " ".join(query_parts)
        rows = await conn.fetch(query, *params)

        # Transform rows to response
        items = []
        for row in rows:
            items.append(ItemListItem(
                id=row['id'],
                name=row['nama_produk'],
                item_type=row.get('item_type', 'goods'),
                track_inventory=row.get('track_inventory', True),
                base_unit=row.get('base_unit') or row['satuan'],
                barcode=row.get('barcode'),
                kategori=row.get('kategori'),
                is_returnable=row.get('is_returnable', True),
                sales_price=row.get('sales_price') or row.get('harga_jual'),
                purchase_price=row.get('purchase_price'),
                current_stock=row.get('current_stock'),
                stock_value=row.get('stock_value'),
                created_at=row['created_at'],
                updated_at=row['updated_at']
            ))

        return ItemListResponse(
            success=True,
            items=items,
            total=total or 0,
            has_more=(offset + limit) < (total or 0)
        )

    except Exception as e:
        logger.error(f"Error listing items: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn:
            await conn.close()


# =============================================================================
# GET ITEM DETAIL
# =============================================================================

@router.get("/items/{item_id}", response_model=ItemDetailResponse)
async def get_item(request: Request, item_id: UUID):
    """Get detailed information about a specific item including unit conversions."""
    tenant_id = get_tenant_id(request)
    conn = None

    try:
        conn = await get_db_connection()

        # Get item
        item_query = """
            SELECT p.*, per.jumlah as current_stock, per.total_nilai as stock_value
            FROM products p
            LEFT JOIN persediaan per ON per.product_id = p.id AND per.tenant_id = p.tenant_id
            WHERE p.id = $1 AND p.tenant_id = $2
        """
        row = await conn.fetchrow(item_query, str(item_id), tenant_id)

        if not row:
            raise HTTPException(status_code=404, detail="Item not found")

        # Get unit conversions
        conversions_query = """
            SELECT * FROM unit_conversions
            WHERE product_id = $1 AND tenant_id = $2 AND is_active = true
            ORDER BY conversion_factor ASC
        """
        conversion_rows = await conn.fetch(conversions_query, str(item_id), tenant_id)

        conversions = [
            {
                "id": str(c['id']),
                "base_unit": c['base_unit'],
                "conversion_unit": c['conversion_unit'],
                "conversion_factor": c['conversion_factor'],
                "purchase_price": c.get('purchase_price'),
                "sales_price": c.get('sales_price'),
                "is_active": c.get('is_active', True)
            }
            for c in conversion_rows
        ]

        return ItemDetailResponse(
            success=True,
            data={
                "id": str(row['id']),
                "name": row['nama_produk'],
                "item_type": row.get('item_type', 'goods'),
                "track_inventory": row.get('track_inventory', True),
                "base_unit": row.get('base_unit') or row['satuan'],
                "barcode": row.get('barcode'),
                "kategori": row.get('kategori'),
                "deskripsi": row.get('deskripsi'),
                "is_returnable": row.get('is_returnable', True),
                "sales_account": row.get('sales_account', 'Sales'),
                "purchase_account": row.get('purchase_account', 'Cost of Goods Sold'),
                "sales_tax": row.get('sales_tax'),
                "purchase_tax": row.get('purchase_tax'),
                "sales_price": row.get('sales_price') or row.get('harga_jual'),
                "purchase_price": row.get('purchase_price'),
                "current_stock": row.get('current_stock'),
                "stock_value": row.get('stock_value'),
                "conversions": conversions,
                "created_at": row['created_at'].isoformat() if row['created_at'] else None,
                "updated_at": row['updated_at'].isoformat() if row['updated_at'] else None
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting item: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn:
            await conn.close()


# =============================================================================
# CREATE ITEM
# =============================================================================

@router.post("/items", response_model=CreateItemResponse)
async def create_item(request: Request, body: CreateItemRequest):
    """
    Create a new item (product or service).

    For goods with track_inventory=true, an initial stock entry will be created.
    Unit conversions are only allowed for goods.
    """
    tenant_id = get_tenant_id(request)
    conn = None

    try:
        conn = await get_db_connection()

        # Check for duplicate name
        existing = await conn.fetchrow(
            "SELECT id FROM products WHERE tenant_id = $1 AND nama_produk = $2",
            tenant_id, body.name
        )
        if existing:
            raise HTTPException(status_code=409, detail="Item with this name already exists")

        # Check for duplicate barcode
        if body.barcode:
            existing_barcode = await conn.fetchrow(
                "SELECT id FROM products WHERE barcode = $1",
                body.barcode
            )
            if existing_barcode:
                raise HTTPException(status_code=409, detail="Item with this barcode already exists")

        # Start transaction
        async with conn.transaction():
            # Insert item
            insert_query = """
                INSERT INTO products (
                    tenant_id, nama_produk, satuan, base_unit, kategori, deskripsi, barcode,
                    item_type, track_inventory, is_returnable,
                    sales_account, purchase_account, sales_tax, purchase_tax,
                    sales_price, purchase_price, harga_jual,
                    created_at, updated_at
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7,
                    $8, $9, $10,
                    $11, $12, $13, $14,
                    $15, $16, $17,
                    NOW(), NOW()
                )
                RETURNING id
            """
            item_id = await conn.fetchval(
                insert_query,
                tenant_id,
                body.name,
                body.base_unit,  # satuan = base_unit
                body.base_unit,
                body.kategori,
                body.deskripsi,
                body.barcode,
                body.item_type,
                body.track_inventory,
                body.is_returnable,
                body.sales_account,
                body.purchase_account,
                body.sales_tax,
                body.purchase_tax,
                body.sales_price,
                body.purchase_price,
                body.sales_price  # harga_jual = sales_price for backwards compat
            )

            # Insert unit conversions (goods only)
            if body.item_type == 'goods' and body.conversions:
                for conv in body.conversions:
                    await conn.execute(
                        """
                        INSERT INTO unit_conversions (
                            tenant_id, product_id, base_unit, conversion_unit, conversion_factor,
                            purchase_price, sales_price, is_active, created_at, updated_at
                        ) VALUES ($1, $2, $3, $4, $5, $6, $7, true, NOW(), NOW())
                        """,
                        tenant_id,
                        item_id,
                        body.base_unit,
                        conv.conversion_unit,
                        conv.conversion_factor,
                        conv.purchase_price,
                        conv.sales_price
                    )

            # Create initial stock entry (if track_inventory)
            if body.track_inventory and body.item_type == 'goods':
                await conn.execute(
                    """
                    INSERT INTO persediaan (
                        tenant_id, product_id, lokasi_gudang, jumlah,
                        nilai_per_unit, total_nilai, created_at, updated_at
                    ) VALUES ($1, $2, 'gudang_utama', 0, $3, 0, NOW(), NOW())
                    ON CONFLICT (tenant_id, product_id, lokasi_gudang) DO NOTHING
                    """,
                    tenant_id,
                    item_id,
                    body.purchase_price or 0
                )

        logger.info(f"Created item {body.name} (id={item_id}) for tenant {tenant_id}")

        return CreateItemResponse(
            success=True,
            message=f"Item '{body.name}' berhasil ditambahkan",
            data={
                "id": str(item_id),
                "name": body.name,
                "item_type": body.item_type,
                "track_inventory": body.track_inventory
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating item: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn:
            await conn.close()


# =============================================================================
# UPDATE ITEM
# =============================================================================

@router.put("/items/{item_id}", response_model=UpdateItemResponse)
async def update_item(request: Request, item_id: UUID, body: UpdateItemRequest):
    """Update an existing item."""
    tenant_id = get_tenant_id(request)
    conn = None

    try:
        conn = await get_db_connection()

        # Check item exists
        existing = await conn.fetchrow(
            "SELECT id FROM products WHERE id = $1 AND tenant_id = $2",
            str(item_id), tenant_id
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Item not found")

        # Check for duplicate name (if changing)
        if body.name:
            duplicate = await conn.fetchrow(
                "SELECT id FROM products WHERE tenant_id = $1 AND nama_produk = $2 AND id != $3",
                tenant_id, body.name, str(item_id)
            )
            if duplicate:
                raise HTTPException(status_code=409, detail="Item with this name already exists")

        # Check for duplicate barcode (if changing)
        if body.barcode:
            duplicate_barcode = await conn.fetchrow(
                "SELECT id FROM products WHERE barcode = $1 AND id != $2",
                body.barcode, str(item_id)
            )
            if duplicate_barcode:
                raise HTTPException(status_code=409, detail="Item with this barcode already exists")

        async with conn.transaction():
            # Build update query dynamically
            updates = []
            params = []
            param_idx = 1

            field_mappings = {
                'name': 'nama_produk',
                'item_type': 'item_type',
                'track_inventory': 'track_inventory',
                'base_unit': 'base_unit',
                'barcode': 'barcode',
                'kategori': 'kategori',
                'deskripsi': 'deskripsi',
                'is_returnable': 'is_returnable',
                'sales_account': 'sales_account',
                'purchase_account': 'purchase_account',
                'sales_tax': 'sales_tax',
                'purchase_tax': 'purchase_tax',
                'sales_price': 'sales_price',
                'purchase_price': 'purchase_price'
            }

            body_dict = body.model_dump(exclude_unset=True, exclude={'conversions'})

            for field, db_field in field_mappings.items():
                if field in body_dict:
                    updates.append(f"{db_field} = ${param_idx}")
                    params.append(body_dict[field])
                    param_idx += 1

                    # Keep harga_jual in sync with sales_price
                    if field == 'sales_price':
                        updates.append(f"harga_jual = ${param_idx}")
                        params.append(body_dict[field])
                        param_idx += 1

                    # Keep satuan in sync with base_unit
                    if field == 'base_unit':
                        updates.append(f"satuan = ${param_idx}")
                        params.append(body_dict[field])
                        param_idx += 1

            if updates:
                updates.append("updated_at = NOW()")
                params.extend([str(item_id), tenant_id])

                update_query = f"""
                    UPDATE products SET {', '.join(updates)}
                    WHERE id = ${param_idx} AND tenant_id = ${param_idx + 1}
                """
                await conn.execute(update_query, *params)

            # Update conversions if provided
            if body.conversions is not None:
                # Deactivate all existing conversions
                await conn.execute(
                    "UPDATE unit_conversions SET is_active = false WHERE product_id = $1 AND tenant_id = $2",
                    str(item_id), tenant_id
                )

                # Insert/update new conversions
                base_unit = body.base_unit or (await conn.fetchval(
                    "SELECT base_unit FROM products WHERE id = $1", str(item_id)
                )) or 'pcs'

                for conv in body.conversions:
                    if conv.id:
                        # Update existing
                        await conn.execute(
                            """
                            UPDATE unit_conversions SET
                                conversion_unit = $1, conversion_factor = $2,
                                purchase_price = $3, sales_price = $4,
                                is_active = $5, updated_at = NOW()
                            WHERE id = $6
                            """,
                            conv.conversion_unit, conv.conversion_factor,
                            conv.purchase_price, conv.sales_price,
                            conv.is_active, str(conv.id)
                        )
                    else:
                        # Insert new
                        await conn.execute(
                            """
                            INSERT INTO unit_conversions (
                                tenant_id, product_id, base_unit, conversion_unit, conversion_factor,
                                purchase_price, sales_price, is_active, created_at, updated_at
                            ) VALUES ($1, $2, $3, $4, $5, $6, $7, true, NOW(), NOW())
                            """,
                            tenant_id, str(item_id), base_unit,
                            conv.conversion_unit, conv.conversion_factor,
                            conv.purchase_price, conv.sales_price
                        )

        logger.info(f"Updated item {item_id} for tenant {tenant_id}")

        return UpdateItemResponse(
            success=True,
            message="Item berhasil diperbarui",
            data={"id": str(item_id)}
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating item: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn:
            await conn.close()


# =============================================================================
# DELETE ITEM
# =============================================================================

@router.delete("/items/{item_id}", response_model=DeleteItemResponse)
async def delete_item(request: Request, item_id: UUID):
    """Delete an item. This will also delete associated unit conversions and pricing."""
    tenant_id = get_tenant_id(request)
    conn = None

    try:
        conn = await get_db_connection()

        # Check item exists
        existing = await conn.fetchrow(
            "SELECT nama_produk FROM products WHERE id = $1 AND tenant_id = $2",
            str(item_id), tenant_id
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Item not found")

        # Delete item (cascades to unit_conversions, item_pricing, persediaan)
        await conn.execute(
            "DELETE FROM products WHERE id = $1 AND tenant_id = $2",
            str(item_id), tenant_id
        )

        logger.info(f"Deleted item {item_id} for tenant {tenant_id}")

        return DeleteItemResponse(
            success=True,
            message=f"Item '{existing['nama_produk']}' berhasil dihapus"
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting item: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn:
            await conn.close()


# =============================================================================
# UNITS
# =============================================================================

@router.get("/items/units", response_model=UnitListResponse)
async def list_units(request: Request):
    """Get list of available units (default + custom)."""
    tenant_id = get_tenant_id(request)
    conn = None

    try:
        conn = await get_db_connection()

        # Get custom units from existing products
        custom_units_query = """
            SELECT DISTINCT base_unit FROM products WHERE tenant_id = $1 AND base_unit IS NOT NULL
            UNION
            SELECT DISTINCT satuan FROM products WHERE tenant_id = $1 AND satuan IS NOT NULL
            UNION
            SELECT DISTINCT conversion_unit FROM unit_conversions WHERE tenant_id = $1
        """
        rows = await conn.fetch(custom_units_query, tenant_id)

        # Filter out default units
        default_lower = [u.lower() for u in DEFAULT_UNITS]
        custom_units = [
            row['base_unit'] for row in rows
            if row['base_unit'] and row['base_unit'].lower() not in default_lower
        ]

        return UnitListResponse(
            success=True,
            default_units=DEFAULT_UNITS,
            custom_units=sorted(set(custom_units))
        )

    except Exception as e:
        logger.error(f"Error listing units: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn:
            await conn.close()


@router.post("/items/units", response_model=CreateUnitResponse)
async def create_unit(request: Request, body: CreateUnitRequest):
    """
    Create a custom unit.
    Note: Custom units are stored implicitly when used in products.
    This endpoint is for pre-creating units for the dropdown.
    """
    # For now, we just validate the unit name
    # Custom units are created implicitly when products use them
    if body.name.lower() in [u.lower() for u in DEFAULT_UNITS]:
        raise HTTPException(status_code=400, detail="Unit already exists in defaults")

    return CreateUnitResponse(
        success=True,
        message=f"Unit '{body.name}' siap digunakan",
        unit=body.name
    )


# =============================================================================
# ACCOUNTS & TAX OPTIONS
# =============================================================================

@router.get("/items/accounts", response_model=AccountsResponse)
async def list_accounts(request: Request):
    """Get list of accounts for sales and purchase dropdowns."""
    # For now, return static list
    # TODO: Integrate with CoA when available
    sales = [
        AccountOption(value=acc, label=acc, type='income')
        for acc in SALES_ACCOUNTS
    ]
    purchases = [
        AccountOption(value=acc, label=acc, type='expense' if acc != 'Cost of Goods Sold' else 'cogs')
        for acc in PURCHASE_ACCOUNTS
    ]

    return AccountsResponse(
        success=True,
        sales_accounts=sales,
        purchase_accounts=purchases
    )


# =============================================================================
# COA-BASED ACCOUNTS (New endpoints querying Chart of Accounts)
# =============================================================================

@router.get("/items/accounts/sales", response_model=CoaAccountsResponse)
async def list_sales_accounts(request: Request):
    """
    Get list of INCOME accounts from Chart of Accounts for sales.

    Returns actual CoA accounts instead of hardcoded list.
    Use these for item sales_account_id selection.
    """
    tenant_id = get_tenant_id(request)
    conn = None

    try:
        conn = await get_db_connection()

        query = """
            SELECT id, account_code, name, account_type
            FROM chart_of_accounts
            WHERE tenant_id = $1
              AND account_type = 'INCOME'
              AND is_active = true
            ORDER BY account_code ASC
        """
        rows = await conn.fetch(query, tenant_id)

        accounts = [
            CoaAccountOption(
                id=str(row['id']),
                code=row['account_code'],
                name=row['name'],
                account_type=row['account_type']
            )
            for row in rows
        ]

        return CoaAccountsResponse(success=True, accounts=accounts)

    except Exception as e:
        logger.error(f"Error listing sales accounts: {e}")
        raise HTTPException(status_code=500, detail="Failed to list sales accounts")
    finally:
        if conn:
            await conn.close()


@router.get("/items/accounts/purchase", response_model=CoaAccountsResponse)
async def list_purchase_accounts(request: Request):
    """
    Get list of EXPENSE accounts from Chart of Accounts for purchases.

    Returns actual CoA accounts instead of hardcoded list.
    Use these for item purchase_account_id selection.
    """
    tenant_id = get_tenant_id(request)
    conn = None

    try:
        conn = await get_db_connection()

        query = """
            SELECT id, account_code, name, account_type
            FROM chart_of_accounts
            WHERE tenant_id = $1
              AND account_type = 'EXPENSE'
              AND is_active = true
            ORDER BY account_code ASC
        """
        rows = await conn.fetch(query, tenant_id)

        accounts = [
            CoaAccountOption(
                id=str(row['id']),
                code=row['account_code'],
                name=row['name'],
                account_type=row['account_type']
            )
            for row in rows
        ]

        return CoaAccountsResponse(success=True, accounts=accounts)

    except Exception as e:
        logger.error(f"Error listing purchase accounts: {e}")
        raise HTTPException(status_code=500, detail="Failed to list purchase accounts")
    finally:
        if conn:
            await conn.close()


@router.get("/items/accounts/inventory", response_model=CoaAccountsResponse)
async def list_inventory_accounts(request: Request):
    """
    Get list of ASSET accounts suitable for inventory from Chart of Accounts.

    Filters for accounts with 'persediaan' or 'inventory' in name,
    or accounts starting with code '1-104' (inventory assets).
    """
    tenant_id = get_tenant_id(request)
    conn = None

    try:
        conn = await get_db_connection()

        query = """
            SELECT id, account_code, name, account_type
            FROM chart_of_accounts
            WHERE tenant_id = $1
              AND account_type = 'ASSET'
              AND is_active = true
              AND (
                  name ILIKE '%persediaan%'
                  OR name ILIKE '%inventory%'
                  OR account_code LIKE '1-104%'
              )
            ORDER BY account_code ASC
        """
        rows = await conn.fetch(query, tenant_id)

        accounts = [
            CoaAccountOption(
                id=str(row['id']),
                code=row['account_code'],
                name=row['name'],
                account_type=row['account_type']
            )
            for row in rows
        ]

        return CoaAccountsResponse(success=True, accounts=accounts)

    except Exception as e:
        logger.error(f"Error listing inventory accounts: {e}")
        raise HTTPException(status_code=500, detail="Failed to list inventory accounts")
    finally:
        if conn:
            await conn.close()


@router.get("/items/taxes", response_model=TaxOptionsResponse)
async def list_taxes(request: Request):
    """Get list of tax options for goods and services."""
    goods_taxes = [
        TaxOption(value='', label='Tidak Ada', rate=0),
        TaxOption(value='PPN_11', label='PPN 11%', rate=11),
        TaxOption(value='PPN_12', label='PPN 12%', rate=12),
    ]

    service_taxes = [
        TaxOption(value='', label='Tidak Ada', rate=0),
        TaxOption(value='PPN_11', label='PPN 11%', rate=11),
        TaxOption(value='PPN_12', label='PPN 12%', rate=12),
        TaxOption(value='PPH_23_2', label='PPh 23 - 2% (Jasa)', rate=2),
        TaxOption(value='PPH_23_15', label='PPh 23 - 15% (Dividen/Royalti)', rate=15),
    ]

    return TaxOptionsResponse(
        success=True,
        goods_taxes=goods_taxes,
        service_taxes=service_taxes
    )


# =============================================================================
# SUMMARY
# =============================================================================

@router.get("/items/summary", response_model=ItemsSummaryResponse)
async def get_items_summary(request: Request):
    """Get summary counts for items."""
    tenant_id = get_tenant_id(request)
    conn = None

    try:
        conn = await get_db_connection()

        summary_query = """
            SELECT
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE item_type = 'goods') as goods_count,
                COUNT(*) FILTER (WHERE item_type = 'service') as service_count,
                COUNT(*) FILTER (WHERE track_inventory = true) as tracked_count
            FROM products
            WHERE tenant_id = $1
        """
        row = await conn.fetchrow(summary_query, tenant_id)

        return ItemsSummaryResponse(
            success=True,
            data={
                "total": row['total'] or 0,
                "goods_count": row['goods_count'] or 0,
                "service_count": row['service_count'] or 0,
                "tracked_count": row['tracked_count'] or 0
            }
        )

    except Exception as e:
        logger.error(f"Error getting summary: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn:
            await conn.close()
