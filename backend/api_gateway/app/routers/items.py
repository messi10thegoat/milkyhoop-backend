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
from typing import Optional
import logging
import asyncpg
import json
import uuid
from uuid import UUID

from ..schemas.items import (
    CreateItemRequest,
    UpdateItemRequest,
    CreateItemResponse,
    UpdateItemResponse,
    DeleteItemResponse,
    ItemListResponse,
    ItemListItem,
    ItemListConversion,
    ItemDetailResponse,
    UnitListResponse,
    CreateUnitRequest,
    CreateUnitResponse,
    AccountsResponse,
    AccountOption,
    TaxOptionsResponse,
    TaxOption,
    ItemsSummaryResponse,
    ItemsStatsResponse,
    ItemsStatsStockResponse,
    CoaAccountOption,
    CoaAccountsResponse,
    ItemTransaction,
    ItemTransactionsResponse,
    RelatedDocument,
    ItemRelatedResponse,
    CreateCategoryRequest,
    CreateCategoryResponse,
    CategoryListResponse,
    ItemActivity,
    ItemActivityResponse,
    DEFAULT_UNITS,
    SALES_ACCOUNTS,
    PURCHASE_ACCOUNTS,
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
    if not hasattr(request.state, "user") or not request.state.user:
        raise HTTPException(status_code=401, detail="Authentication required")

    tenant_id = request.state.user.get("tenant_id")
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Invalid user context")

    return tenant_id


# Connection pool for newer endpoints
_pool = None


async def get_pool():
    """Get or create connection pool."""
    global _pool
    if _pool is None:
        db_config = settings.get_db_config()
        _pool = await asyncpg.create_pool(
            **db_config, min_size=2, max_size=10, command_timeout=30
        )
    return _pool


def get_user_context(request):
    """Extract and validate user context from request."""
    if not hasattr(request.state, "user") or not request.state.user:
        raise HTTPException(status_code=401, detail="Authentication required")

    user = request.state.user
    tenant_id = user.get("tenant_id")
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Invalid user context")

    return {
        "tenant_id": tenant_id,
        "user_id": user.get("user_id"),
        "username": user.get("username") or user.get("email"),
    }


# =============================================================================
# LIST ITEMS
# =============================================================================


@router.get("/items", response_model=ItemListResponse)
async def list_items(
    request: Request,
    item_type: Optional[str] = Query(
        None, description="Filter by type: goods, service"
    ),
    track_inventory: Optional[bool] = Query(
        None, description="Filter by track_inventory"
    ),
    search: Optional[str] = Query(None, description="Search by name or barcode"),
    kategori: Optional[str] = Query(None, description="Filter by category"),
    stock_status: Optional[str] = Query(
        None, description="Filter by stock status: in_stock, low_stock, out_of_stock"
    ),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """
    List all items with optional filtering.

    Use track_inventory=true to get only items that are tracked in inventory.
    """
    tenant_id = get_tenant_id(request)
    conn = None

    try:
        conn = await get_db_connection()

        # Build query with filters - include conversions as JSON array
        # Use subquery for persediaan to prevent duplicate rows when multiple stock entries exist
        query_parts = [
            """SELECT p.*, per.jumlah as current_stock, per.total_nilai as stock_value,
            v.name as vendor_name,
            CASE
                WHEN p.track_inventory = true AND COALESCE(per.jumlah, 0) > 0
                     AND p.reorder_level IS NOT NULL AND COALESCE(per.jumlah, 0) <= p.reorder_level
                THEN true
                ELSE false
            END as low_stock,
            COALESCE(
                (SELECT json_agg(json_build_object(
                    'conversion_unit', uc.conversion_unit,
                    'conversion_factor', uc.conversion_factor,
                    'sales_price', uc.sales_price,
                    'purchase_price', uc.purchase_price
                ) ORDER BY uc.conversion_factor)
                FROM unit_conversions uc
                WHERE uc.product_id = p.id AND uc.tenant_id = p.tenant_id AND uc.is_active = true),
                '[]'::json
            ) as conversions"""
        ]
        query_parts.append("FROM products p")
        query_parts.append(
            """LEFT JOIN LATERAL (
                SELECT SUM(jumlah) as jumlah, SUM(total_nilai) as total_nilai
                FROM persediaan
                WHERE product_id = p.id AND tenant_id = p.tenant_id
            ) per ON true"""
        )
        query_parts.append(
            "LEFT JOIN vendors v ON v.id::text = p.preferred_vendor_id::text AND v.tenant_id = p.tenant_id"
        )
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
            query_parts.append(
                f"AND (p.nama_produk ILIKE ${param_idx} OR p.barcode ILIKE ${param_idx})"
            )
            params.append(f"%{search}%")
            param_idx += 1

        if stock_status:
            query_parts.append("AND p.item_type = 'goods' AND p.track_inventory = true")
            if stock_status == "in_stock":
                query_parts.append(
                    "AND COALESCE(per.jumlah, 0) > COALESCE(p.reorder_level, 0)"
                )
            elif stock_status == "low_stock":
                query_parts.append(
                    "AND COALESCE(p.reorder_level, 0) > 0 AND COALESCE(per.jumlah, 0) > 0 AND COALESCE(per.jumlah, 0) <= COALESCE(p.reorder_level, 0)"
                )
            elif stock_status == "out_of_stock":
                query_parts.append("AND (per.jumlah IS NULL OR per.jumlah = 0)")

        # Count total - use simplified query for count
        count_parts = [part for part in query_parts if not part.startswith("SELECT")]
        count_query = "SELECT COUNT(*) FROM products p " + " ".join(count_parts[1:])
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
            # Parse conversions from JSON
            conversions_data = row.get("conversions") or []
            if isinstance(conversions_data, str):
                conversions_data = json.loads(conversions_data)

            conversions = (
                [
                    ItemListConversion(
                        conversion_unit=c["conversion_unit"],
                        conversion_factor=c["conversion_factor"],
                        purchase_price=c.get("purchase_price"),
                        sales_price=c.get("sales_price"),
                    )
                    for c in conversions_data
                ]
                if conversions_data
                else []
            )

            items.append(
                ItemListItem(
                    id=row["id"],
                    name=row["nama_produk"],
                    item_type=row.get("item_type", "goods"),
                    track_inventory=row.get("track_inventory", True),
                    base_unit=row.get("base_unit") or row["satuan"],
                    barcode=row.get("barcode"),
                    kategori=row.get("kategori"),
                    deskripsi=row.get("deskripsi"),
                    is_returnable=row.get("is_returnable", True),
                    sales_price=row.get("sales_price") or row.get("harga_jual"),
                    purchase_price=row.get("purchase_price"),
                    image_url=row.get("image_url"),
                    reorder_level=float(row["reorder_level"])
                    if row.get("reorder_level")
                    else None,
                    reorder_point=int(row["reorder_level"])
                    if row.get("reorder_level")
                    else 0,
                    vendor_name=row.get("vendor_name"),
                    sales_tax=row.get("sales_tax"),
                    purchase_tax=row.get("purchase_tax"),
                    current_stock=row.get("current_stock"),
                    stock_value=row.get("stock_value"),
                    low_stock=row.get("low_stock", False),
                    conversions=conversions,
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                )
            )

        return ItemListResponse(
            success=True,
            items=items,
            total=total or 0,
            has_more=(offset + limit) < (total or 0),
        )

    except Exception as e:
        logger.error(f"Error listing items: {e}")
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
            tenant_id,
            body.name,
        )
        if existing:
            raise HTTPException(
                status_code=409, detail="Item with this name already exists"
            )

        # Check for duplicate barcode
        if body.barcode:
            existing_barcode = await conn.fetchrow(
                "SELECT id FROM products WHERE barcode = $1", body.barcode
            )
            if existing_barcode:
                raise HTTPException(
                    status_code=409, detail="Item with this barcode already exists"
                )

        # Start transaction
        async with conn.transaction():
            # Insert item
            insert_query = """
                INSERT INTO products (
                    tenant_id, nama_produk, satuan, base_unit, kategori, deskripsi, barcode,
                    item_type, track_inventory, is_returnable,
                    sales_account, purchase_account, sales_tax, purchase_tax,
                    sales_price, purchase_price, harga_jual,
                    image_url, reorder_level, preferred_vendor_id,
                    sales_account_id, purchase_account_id,
                    created_at, updated_at
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7,
                    $8, $9, $10,
                    $11, $12, $13, $14,
                    $15, $16, $17,
                    $18, $19, $20,
                    $21, $22,
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
                body.sales_price,  # harga_jual = sales_price for backwards compat
                body.image_url,
                body.reorder_level,
                body.preferred_vendor_id,
                body.sales_account_id,
                body.purchase_account_id,
            )

            # Insert unit conversions (goods only)
            if body.item_type == "goods" and body.conversions:
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
                        conv.sales_price,
                    )

            # Create initial stock entry (if track_inventory)
            if body.track_inventory and body.item_type == "goods":
                # Check if stock entry already exists
                existing_stock = await conn.fetchval(
                    "SELECT id FROM persediaan WHERE tenant_id = $1 AND product_id = $2 AND lokasi_gudang = 'gudang_utama'",
                    tenant_id,
                    item_id,
                )
                if not existing_stock:
                    await conn.execute(
                        """
                        INSERT INTO persediaan (
                            id, tenant_id, product_id, produk_id, lokasi_gudang, jumlah,
                            nilai_per_unit, total_nilai, created_at, updated_at
                        ) VALUES ($1, $2, $3, $4, 'gudang_utama', 0, $5, 0, NOW(), NOW())
                        """,
                        str(uuid.uuid4()),
                        tenant_id,
                        item_id,
                        str(item_id),  # produk_id for backwards compat
                        body.purchase_price or 0,
                    )

            # Log activity
            user_id = request.state.user.get("user_id")
            user_name = request.state.user.get("username") or request.state.user.get(
                "email"
            )
            await conn.execute(
                """
                INSERT INTO item_activities (item_id, tenant_id, type, description, actor_id, actor_name)
                VALUES ($1, $2, 'created', 'Item dibuat', $3, $4)
                """,
                item_id,
                tenant_id,
                user_id if user_id else None,
                user_name,
            )

        logger.info(f"Created item {body.name} (id={item_id}) for tenant {tenant_id}")

        return CreateItemResponse(
            success=True,
            message=f"Item '{body.name}' berhasil ditambahkan",
            data={
                "id": str(item_id),
                "name": body.name,
                "item_type": body.item_type,
                "track_inventory": body.track_inventory,
            },
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
            str(item_id),
            tenant_id,
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Item not found")

        # Check for duplicate name (if changing)
        if body.name:
            duplicate = await conn.fetchrow(
                "SELECT id FROM products WHERE tenant_id = $1 AND nama_produk = $2 AND id != $3",
                tenant_id,
                body.name,
                str(item_id),
            )
            if duplicate:
                raise HTTPException(
                    status_code=409, detail="Item with this name already exists"
                )

        # Check for duplicate barcode (if changing)
        if body.barcode:
            duplicate_barcode = await conn.fetchrow(
                "SELECT id FROM products WHERE barcode = $1 AND id != $2",
                body.barcode,
                str(item_id),
            )
            if duplicate_barcode:
                raise HTTPException(
                    status_code=409, detail="Item with this barcode already exists"
                )

        # Fetch old values for change tracking
        old_item = await conn.fetchrow(
            """SELECT nama_produk, sales_price, harga_jual, purchase_price,
                      base_unit, satuan, reorder_level, item_type, track_inventory,
                      kategori, deskripsi, barcode, is_returnable,
                      sales_tax, purchase_tax, image_url
               FROM products WHERE id = $1 AND tenant_id = $2""",
            str(item_id),
            tenant_id,
        )

        async with conn.transaction():
            # Build update query dynamically
            updates = []
            params = []
            param_idx = 1

            field_mappings = {
                "name": "nama_produk",
                "item_type": "item_type",
                "track_inventory": "track_inventory",
                "base_unit": "base_unit",
                "barcode": "barcode",
                "kategori": "kategori",
                "deskripsi": "deskripsi",
                "is_returnable": "is_returnable",
                "sales_account": "sales_account",
                "purchase_account": "purchase_account",
                "sales_account_id": "sales_account_id",
                "purchase_account_id": "purchase_account_id",
                "sales_tax": "sales_tax",
                "purchase_tax": "purchase_tax",
                "sales_price": "sales_price",
                "purchase_price": "purchase_price",
                "image_url": "image_url",
                "reorder_level": "reorder_level",
                "preferred_vendor_id": "preferred_vendor_id",
            }

            body_dict = body.model_dump(exclude_unset=True, exclude={"conversions"})

            for field, db_field in field_mappings.items():
                if field in body_dict:
                    updates.append(f"{db_field} = ${param_idx}")
                    params.append(body_dict[field])
                    param_idx += 1

                    # Keep harga_jual in sync with sales_price
                    if field == "sales_price":
                        updates.append(f"harga_jual = ${param_idx}")
                        params.append(body_dict[field])
                        param_idx += 1

                    # Keep satuan in sync with base_unit
                    if field == "base_unit":
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
                    str(item_id),
                    tenant_id,
                )

                # Insert/update new conversions
                base_unit = (
                    body.base_unit
                    or (
                        await conn.fetchval(
                            "SELECT base_unit FROM products WHERE id = $1", str(item_id)
                        )
                    )
                    or "pcs"
                )

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
                            conv.conversion_unit,
                            conv.conversion_factor,
                            conv.purchase_price,
                            conv.sales_price,
                            conv.is_active,
                            str(conv.id),
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
                            tenant_id,
                            str(item_id),
                            base_unit,
                            conv.conversion_unit,
                            conv.conversion_factor,
                            conv.purchase_price,
                            conv.sales_price,
                        )

            # Log activity
            body_dict_for_log = body.model_dump(
                exclude_unset=True, exclude={"conversions"}
            )
            if body_dict_for_log:
                change_parts = []
                field_labels = {
                    "name": ("Nama", "nama_produk"),
                    "sales_price": ("Harga jual", "sales_price", True),
                    "purchase_price": ("Harga beli", "purchase_price", True),
                    "base_unit": ("Satuan", "base_unit"),
                    "reorder_level": ("Titik reorder", "reorder_level"),
                    "kategori": ("Kategori", "kategori"),
                    "deskripsi": ("Deskripsi", "deskripsi"),
                    "barcode": ("Barcode", "barcode"),
                    "sales_tax": ("Pajak jual", "sales_tax"),
                    "purchase_tax": ("Pajak beli", "purchase_tax"),
                }

                only_price = True

                for field, meta in field_labels.items():
                    if field in body_dict_for_log:
                        label = meta[0]
                        db_col = meta[1]
                        is_price = len(meta) > 2 and meta[2]
                        old_val = old_item.get(db_col) if old_item else None
                        new_val = body_dict_for_log[field]

                        if str(old_val) != str(new_val) and not (
                            old_val is None and new_val is None
                        ):
                            if is_price:
                                old_display = (
                                    f"Rp {int(old_val):,}".replace(",", ".")
                                    if old_val
                                    else "0"
                                )
                                new_display = (
                                    f"Rp {int(new_val):,}".replace(",", ".")
                                    if new_val
                                    else "0"
                                )
                            else:
                                old_display = str(old_val) if old_val else "-"
                                new_display = str(new_val) if new_val else "-"
                            change_parts.append(
                                f"{label}: {old_display} \u2192 {new_display}"
                            )
                            if field not in ("sales_price", "purchase_price"):
                                only_price = False

                # Determine activity type
                if (
                    only_price
                    and change_parts
                    and all("Harga" in p for p in change_parts)
                ):
                    activity_type = "price_changed"
                    activity_desc = "Harga diubah"
                else:
                    activity_type = "updated"
                    activity_desc = "Item diperbarui"

                details = ", ".join(change_parts) if change_parts else None
                user_id = request.state.user.get("user_id")
                user_name = request.state.user.get(
                    "username"
                ) or request.state.user.get("email")

                if change_parts:  # Only log if there were actual changes
                    await conn.execute(
                        """
                        INSERT INTO item_activities (item_id, tenant_id, type, description, details, actor_id, actor_name)
                        VALUES ($1, $2, $3, $4, $5, $6, $7)
                        """,
                        str(item_id),
                        tenant_id,
                        activity_type,
                        activity_desc,
                        details,
                        user_id if user_id else None,
                        user_name,
                    )

        logger.info(f"Updated item {item_id} for tenant {tenant_id}")

        return UpdateItemResponse(
            success=True, message="Item berhasil diperbarui", data={"id": str(item_id)}
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
            str(item_id),
            tenant_id,
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Item not found")

        # Delete item (cascades to unit_conversions, item_pricing, persediaan)
        await conn.execute(
            "DELETE FROM products WHERE id = $1 AND tenant_id = $2",
            str(item_id),
            tenant_id,
        )

        logger.info(f"Deleted item {item_id} for tenant {tenant_id}")

        return DeleteItemResponse(
            success=True, message=f"Item '{existing['nama_produk']}' berhasil dihapus"
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
            row["base_unit"]
            for row in rows
            if row["base_unit"] and row["base_unit"].lower() not in default_lower
        ]

        return UnitListResponse(
            success=True,
            default_units=DEFAULT_UNITS,
            custom_units=sorted(set(custom_units)),
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
        success=True, message=f"Unit '{body.name}' siap digunakan", unit=body.name
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
        AccountOption(value=acc, label=acc, type="income") for acc in SALES_ACCOUNTS
    ]
    purchases = [
        AccountOption(
            value=acc,
            label=acc,
            type="expense" if acc != "Cost of Goods Sold" else "cogs",
        )
        for acc in PURCHASE_ACCOUNTS
    ]

    return AccountsResponse(
        success=True, sales_accounts=sales, purchase_accounts=purchases
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
                id=str(row["id"]),
                code=row["account_code"],
                name=row["name"],
                account_type=row["account_type"],
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
                id=str(row["id"]),
                code=row["account_code"],
                name=row["name"],
                account_type=row["account_type"],
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
                id=str(row["id"]),
                code=row["account_code"],
                name=row["name"],
                account_type=row["account_type"],
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
        TaxOption(value="", label="Tidak Ada", rate=0),
        TaxOption(value="PPN_11", label="PPN 11%", rate=11),
        TaxOption(value="PPN_12", label="PPN 12%", rate=12),
    ]

    service_taxes = [
        TaxOption(value="", label="Tidak Ada", rate=0),
        TaxOption(value="PPN_11", label="PPN 11%", rate=11),
        TaxOption(value="PPN_12", label="PPN 12%", rate=12),
        TaxOption(value="PPH_23_2", label="PPh 23 - 2% (Jasa)", rate=2),
        TaxOption(value="PPH_23_15", label="PPh 23 - 15% (Dividen/Royalti)", rate=15),
    ]

    return TaxOptionsResponse(
        success=True, goods_taxes=goods_taxes, service_taxes=service_taxes
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
                COUNT(*) FILTER (WHERE p.item_type = 'goods') as goods_count,
                COUNT(*) FILTER (WHERE p.item_type = 'service') as service_count,
                COUNT(*) FILTER (WHERE p.track_inventory = true) as tracked_count,
                COUNT(*) FILTER (
                    WHERE p.track_inventory = true
                      AND COALESCE(per.jumlah, 0) > COALESCE(p.reorder_level, 0)
                ) as in_stock_count,
                COUNT(*) FILTER (
                    WHERE p.track_inventory = true
                      AND COALESCE(per.jumlah, 0) > 0
                      AND p.reorder_level IS NOT NULL
                      AND COALESCE(per.jumlah, 0) <= p.reorder_level
                ) as low_stock_count,
                COUNT(*) FILTER (
                    WHERE p.track_inventory = true
                      AND COALESCE(per.jumlah, 0) <= 0
                ) as out_of_stock_count
            FROM products p
            LEFT JOIN LATERAL (
                SELECT SUM(jumlah) as jumlah, SUM(total_nilai) as total_nilai
                FROM persediaan
                WHERE product_id = p.id AND tenant_id = p.tenant_id
            ) per ON true
            WHERE p.tenant_id = $1
        """
        row = await conn.fetchrow(summary_query, tenant_id)

        return ItemsSummaryResponse(
            success=True,
            data={
                "total": row["total"] or 0,
                "goods_count": row["goods_count"] or 0,
                "service_count": row["service_count"] or 0,
                "tracked_count": row["tracked_count"] or 0,
                "in_stock_count": row["in_stock_count"] or 0,
                "low_stock_count": row["low_stock_count"] or 0,
                "out_of_stock_count": row["out_of_stock_count"] or 0,
            },
        )

    except Exception as e:
        logger.error(f"Error getting summary: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn:
            await conn.close()


# =============================================================================
# STATS
# =============================================================================


@router.get("/items/stats", response_model=ItemsStatsResponse)
async def get_items_stats(request: Request):
    """
    Get aggregate item statistics.

    Stock counts are only for items with item_type='goods' AND track_inventory=true.
    - inStock: current_stock > reorder_level
    - lowStock: current_stock > 0 AND current_stock <= reorder_level
    - outOfStock: current_stock = 0 OR current_stock IS NULL
    """
    tenant_id = get_tenant_id(request)
    conn = None

    try:
        conn = await get_db_connection()

        stats_query = """
            SELECT
                COUNT(*) as total_items,
                COUNT(*) FILTER (WHERE p.item_type = 'goods') as total_goods,
                COUNT(*) FILTER (WHERE p.item_type = 'service') as total_services,
                COUNT(*) FILTER (
                    WHERE p.item_type = 'goods'
                      AND p.track_inventory = true
                      AND COALESCE(per.jumlah, 0) > COALESCE(p.reorder_level, 0)
                ) as in_stock,
                COUNT(*) FILTER (
                    WHERE p.item_type = 'goods'
                      AND p.track_inventory = true
                      AND COALESCE(per.jumlah, 0) > 0
                      AND COALESCE(p.reorder_level, 0) > 0
                      AND COALESCE(per.jumlah, 0) <= COALESCE(p.reorder_level, 0)
                ) as low_stock,
                COUNT(*) FILTER (
                    WHERE p.item_type = 'goods'
                      AND p.track_inventory = true
                      AND (per.jumlah IS NULL OR per.jumlah = 0)
                ) as out_of_stock
            FROM products p
            LEFT JOIN LATERAL (
                SELECT SUM(jumlah) as jumlah, SUM(total_nilai) as total_nilai
                FROM persediaan
                WHERE product_id = p.id AND tenant_id = p.tenant_id
            ) per ON true
            WHERE p.tenant_id = $1
        """
        row = await conn.fetchrow(stats_query, tenant_id)

        return ItemsStatsResponse(
            totalItems=row["total_items"] or 0,
            totalGoods=row["total_goods"] or 0,
            totalServices=row["total_services"] or 0,
            stock=ItemsStatsStockResponse(
                inStock=row["in_stock"] or 0,
                lowStock=row["low_stock"] or 0,
                outOfStock=row["out_of_stock"] or 0,
            ),
        )

    except Exception as e:
        logger.error(f"Error getting item stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn:
            await conn.close()


# =============================================================================
# CATEGORIES
# =============================================================================


@router.get("/items/categories", response_model=CategoryListResponse)
async def list_categories(request: Request):
    """Get list of categories for the current tenant."""
    tenant_id = get_tenant_id(request)
    conn = None

    try:
        conn = await get_db_connection()

        query = """
            SELECT DISTINCT kategori
            FROM products
            WHERE tenant_id = $1 AND kategori IS NOT NULL AND kategori != ''
            ORDER BY kategori ASC
        """
        rows = await conn.fetch(query, tenant_id)
        categories = [row["kategori"] for row in rows]

        return CategoryListResponse(success=True, categories=categories)

    except Exception as e:
        logger.error(f"Error listing categories: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn:
            await conn.close()


@router.post("/items/categories", response_model=CreateCategoryResponse)
async def create_category(request: Request, body: CreateCategoryRequest):
    """
    Register a new category. Categories are stored as strings on products.
    This endpoint validates uniqueness and returns the category name.
    """
    tenant_id = get_tenant_id(request)
    conn = None

    try:
        conn = await get_db_connection()

        # Check if category already exists
        existing = await conn.fetchval(
            "SELECT kategori FROM products WHERE tenant_id = $1 AND kategori = $2 LIMIT 1",
            tenant_id,
            body.name,
        )
        if existing:
            raise HTTPException(status_code=409, detail="Kategori sudah ada")

        return CreateCategoryResponse(
            success=True,
            message=f"Kategori '{body.name}' siap digunakan",
            category=body.name,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating category: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn:
            await conn.close()


# =============================================================================
# ACTIVITY LOG
# =============================================================================


@router.get("/items/autocomplete")
async def autocomplete_items(
    request: Request,
    q: str = Query(default="", description="Search query"),
    limit: int = Query(20, ge=1, le=50),
    item_type: Optional[str] = Query(None, description="Filter by item type"),
):
    """Quick item search for autocomplete in forms."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        async with pool.acquire() as conn:
            type_filter = ""
            params = [ctx["tenant_id"], f"%{q}%", limit]
            if item_type:
                type_filter = " AND item_type = $4"
                params.append(item_type)

            rows = await conn.fetch(
                f"""
                SELECT id, item_code as code, nama_produk as name, item_type, base_unit as unit, sales_price as selling_price, purchase_price
                FROM products
                WHERE tenant_id = $1
                  AND (nama_produk ILIKE $2 OR item_code ILIKE $2 OR sku ILIKE $2)
                  AND status = 'active'
                  AND deleted_at IS NULL
                  {type_filter}
                ORDER BY name ASC
                LIMIT $3
            """,
                *params,
            )

            items = [
                {
                    "id": str(row["id"]),
                    "code": row["code"],
                    "name": row["name"],
                    "type": row["item_type"],
                    "unit": row["unit"],
                    "selling_price": row["selling_price"],
                    "purchase_price": row["purchase_price"],
                }
                for row in rows
            ]
            return {"success": True, "items": items}
    except Exception as e:
        logger.error(f"Error in autocomplete: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Autocomplete failed")


# =============================================================================
# ITEMS NEXT CODE (for form auto-generation)
# =============================================================================


@router.get("/items/next-code")
async def get_next_item_code(
    request: Request,
    item_type: str = Query(
        "product", description="Item type: product, service, inventory"
    ),
):
    """Get the next available item code for auto-generation."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        async with pool.acquire() as conn:
            # Prefix based on item type
            prefix_map = {
                "product": "PRD",
                "service": "SVC",
                "inventory": "INV",
                "non_inventory": "NI",
            }
            prefix = prefix_map.get(item_type, "ITM")

            row = await conn.fetchrow(
                """
                SELECT item_code as code FROM products
                WHERE tenant_id = $1 AND item_code LIKE $2 AND deleted_at IS NULL
                ORDER BY item_code DESC LIMIT 1
            """,
                ctx["tenant_id"],
                f"{prefix}%",
            )

            import re

            if row and row["code"]:
                match = re.search(r"([0-9]+)$", row["code"])
                if match:
                    num = int(match.group(1)) + 1
                    next_code = f"{prefix}{num:05d}"
                else:
                    next_code = f"{prefix}00001"
            else:
                next_code = f"{prefix}00001"

            return {"success": True, "next_code": next_code}
    except Exception as e:
        logger.error(f"Error getting next code: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get next code")


# =============================================================================
# ITEMS EXPORT
# =============================================================================


@router.get("/items/export")
async def export_items(
    request: Request,
    format: str = Query("csv", description="Export format: csv, xlsx"),
):
    """Export items to CSV or Excel format."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    item_code as code, nama_produk as name, item_type, base_unit as unit, sku,
                    sales_price as selling_price, purchase_price, 0 as current_stock,
                    kategori as category, deskripsi as description,
                    CASE WHEN status = 'active' THEN true ELSE false END as is_active
                FROM products
                WHERE tenant_id = $1 AND deleted_at IS NULL
                ORDER BY item_code ASC
            """,
                ctx["tenant_id"],
            )

            if format == "csv":
                import io
                import csv

                output = io.StringIO()
                writer = csv.writer(output)
                writer.writerow(
                    [
                        "Code",
                        "Name",
                        "Type",
                        "Unit",
                        "SKU",
                        "Selling Price",
                        "Purchase Price",
                        "Stock",
                        "Category",
                        "Description",
                        "Active",
                    ]
                )
                for row in rows:
                    writer.writerow(
                        [
                            row["code"],
                            row["name"],
                            row["item_type"],
                            row["unit"],
                            row["sku"],
                            row["selling_price"],
                            row["purchase_price"],
                            row["current_stock"],
                            row["category"],
                            row["description"],
                            "Yes" if row["is_active"] else "No",
                        ]
                    )
                content = output.getvalue()
                from fastapi.responses import Response

                return Response(
                    content=content,
                    media_type="text/csv",
                    headers={
                        "Content-Disposition": "attachment; filename=items_export.csv"
                    },
                )
            else:
                return {
                    "success": True,
                    "message": "Export format not supported yet",
                    "count": len(rows),
                }
    except Exception as e:
        logger.error(f"Error exporting items: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Export failed")


# =============================================================================
# ITEMS BULK IMPORT
# =============================================================================


@router.post("/items/bulk-import")
async def bulk_import_items(request: Request):
    """
    Bulk import items from parsed CSV data.
    Expects JSON body with array of item data.
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        body = await request.json()
        items_data = body.get("items", [])

        if not items_data:
            raise HTTPException(status_code=400, detail="No items provided")

        created = 0
        updated = 0
        errors = []

        async with pool.acquire() as conn:
            for idx, item in enumerate(items_data):
                try:
                    code = item.get("code")
                    name = item.get("name")

                    if not name:
                        errors.append({"row": idx + 1, "error": "Name is required"})
                        continue

                    # Check if item exists by code
                    existing = None
                    if code:
                        existing = await conn.fetchrow(
                            "SELECT id FROM products WHERE tenant_id = $1 AND item_code = $2 AND deleted_at IS NULL",
                            ctx["tenant_id"],
                            code,
                        )

                    if existing:
                        # Update existing
                        await conn.execute(
                            """
                            UPDATE products SET
                                nama_produk = $1,
                                item_type = COALESCE($2, item_type),
                                base_unit = COALESCE($3, base_unit),
                                sales_price = COALESCE($4, sales_price),
                                purchase_price = COALESCE($5, purchase_price),
                                updated_at = NOW()
                            WHERE id = $6 AND tenant_id = $7
                        """,
                            name,
                            item.get("type"),
                            item.get("unit"),
                            item.get("selling_price"),
                            item.get("purchase_price"),
                            existing["id"],
                            ctx["tenant_id"],
                        )
                        updated += 1
                    else:
                        # Create new
                        import uuid as uuid_mod

                        new_id = str(uuid_mod.uuid4())
                        await conn.execute(
                            """
                            INSERT INTO products (
                                id, tenant_id, item_code, nama_produk, satuan, item_type, base_unit,
                                sales_price, purchase_price, status, created_at, updated_at
                            ) VALUES ($1, $2, $3, $4, $6, $5, $6, $7, $8, 'active', NOW(), NOW())
                        """,
                            new_id,
                            ctx["tenant_id"],
                            code,
                            name,
                            item.get("type", "product"),
                            item.get("unit", "pcs"),
                            item.get("selling_price", 0),
                            item.get("purchase_price", 0),
                        )
                        created += 1

                except Exception as row_error:
                    errors.append({"row": idx + 1, "error": str(row_error)})

        return {
            "success": True,
            "created": created,
            "updated": updated,
            "errors": errors,
            "total_processed": len(items_data),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in bulk import: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Bulk import failed")


# =============================================================================
# ITEMS STATUS UPDATE
# =============================================================================


@router.patch("/items/{item_id}/status")
async def update_item_status(request: Request, item_id: str):
    """Update item active/inactive status."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        body = await request.json()
        is_active = body.get("is_active", True)

        async with pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE products SET status = CASE WHEN $1 THEN 'active' ELSE 'inactive' END, updated_at = NOW()
                WHERE id = $2::uuid AND tenant_id = $3
            """,
                is_active,
                item_id,
                ctx["tenant_id"],
            )

            if result == "UPDATE 0":
                raise HTTPException(status_code=404, detail="Item not found")

            return {
                "success": True,
                "message": "Status updated",
                "is_active": is_active,
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating status: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update status")


# =============================================================================
# ITEMS STOCK ADJUSTMENT
# =============================================================================


@router.get("/items/{item_id}/activity", response_model=ItemActivityResponse)
async def get_item_activity(
    request: Request,
    item_id: UUID,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """Get activity log / audit trail for an item."""
    tenant_id = get_tenant_id(request)
    conn = None

    try:
        conn = await get_db_connection()

        # Verify item exists
        item_exists = await conn.fetchval(
            "SELECT id FROM products WHERE id = $1 AND tenant_id = $2",
            str(item_id),
            tenant_id,
        )
        if not item_exists:
            raise HTTPException(status_code=404, detail="Item not found")

        # Get total count
        total = await conn.fetchval(
            "SELECT COUNT(*) FROM item_activities WHERE item_id = $1 AND tenant_id = $2",
            str(item_id),
            tenant_id,
        )

        # Get activities
        query = """
            SELECT id, type, description, details, actor_name, timestamp
            FROM item_activities
            WHERE item_id = $1 AND tenant_id = $2
            ORDER BY timestamp DESC
            LIMIT $3 OFFSET $4
        """
        rows = await conn.fetch(query, str(item_id), tenant_id, limit, offset)

        activities = [
            ItemActivity(
                id=str(row["id"]),
                type=row["type"],
                description=row["description"],
                details=row.get("details"),
                actor_name=row.get("actor_name"),
                timestamp=row["timestamp"].isoformat() if row["timestamp"] else None,
            )
            for row in rows
        ]

        return ItemActivityResponse(
            success=True,
            activities=activities,
            total=total or 0,
            has_more=(offset + limit) < (total or 0),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting item activity: {e}")
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
            LEFT JOIN LATERAL (
                SELECT SUM(jumlah) as jumlah, SUM(total_nilai) as total_nilai
                FROM persediaan
                WHERE product_id = p.id AND tenant_id = p.tenant_id
            ) per ON true
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
                "id": str(c["id"]),
                "base_unit": c["base_unit"],
                "conversion_unit": c["conversion_unit"],
                "conversion_factor": c["conversion_factor"],
                "purchase_price": c.get("purchase_price"),
                "sales_price": c.get("sales_price"),
                "is_active": c.get("is_active", True),
            }
            for c in conversion_rows
        ]

        return ItemDetailResponse(
            success=True,
            data={
                "id": str(row["id"]),
                "name": row["nama_produk"],
                "item_type": row.get("item_type", "goods"),
                "track_inventory": row.get("track_inventory", True),
                "base_unit": row.get("base_unit") or row["satuan"],
                "barcode": row.get("barcode"),
                "kategori": row.get("kategori"),
                "deskripsi": row.get("deskripsi"),
                "is_returnable": row.get("is_returnable", True),
                "image_url": row.get("image_url"),
                "reorder_level": float(row["reorder_level"])
                if row.get("reorder_level")
                else None,
                "reorder_point": int(row["reorder_level"])
                if row.get("reorder_level")
                else 0,
                "preferred_vendor_id": str(row["preferred_vendor_id"])
                if row.get("preferred_vendor_id")
                else None,
                "sales_account": row.get("sales_account", "Sales"),
                "purchase_account": row.get("purchase_account", "Cost of Goods Sold"),
                "sales_account_id": str(row["sales_account_id"])
                if row.get("sales_account_id")
                else None,
                "purchase_account_id": str(row["purchase_account_id"])
                if row.get("purchase_account_id")
                else None,
                "sales_tax": row.get("sales_tax"),
                "purchase_tax": row.get("purchase_tax"),
                "sales_price": row.get("sales_price") or row.get("harga_jual"),
                "purchase_price": row.get("purchase_price"),
                "current_stock": row.get("current_stock"),
                "stock_value": row.get("stock_value"),
                "conversions": conversions,
                "created_at": row["created_at"].isoformat()
                if row["created_at"]
                else None,
                "updated_at": row["updated_at"].isoformat()
                if row["updated_at"]
                else None,
            },
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
# TRANSACTIONS (Tab Riwayat)
# =============================================================================


@router.get("/items/{item_id}/transactions", response_model=ItemTransactionsResponse)
async def get_item_transactions(
    request: Request,
    item_id: UUID,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """Get transaction history for an item (purchases, sales, adjustments)."""
    tenant_id = get_tenant_id(request)
    conn = None

    try:
        conn = await get_db_connection()

        # Verify item exists
        item_exists = await conn.fetchval(
            "SELECT id FROM products WHERE id = $1 AND tenant_id = $2",
            str(item_id),
            tenant_id,
        )
        if not item_exists:
            raise HTTPException(status_code=404, detail="Item not found")

        # Query transaction history from item_transaksi + transaksi_harian
        tx_query = """
            SELECT
                it.id::text,
                to_char(to_timestamp(th.timestamp / 1000), 'YYYY-MM-DD') as date,
                th.jenis_transaksi as transaction_type,
                th.id as document_number,
                CASE
                    WHEN th.jenis_transaksi IN ('pembelian', 'purchase', 'stock_in', 'adjustment_in', 'penerimaan_barang')
                    THEN ABS(COALESCE(it.jumlah, 0))
                    ELSE -ABS(COALESCE(it.jumlah, 0))
                END as qty_change,
                it.harga_satuan as unit_price,
                it.subtotal as total,
                it.keterangan as notes
            FROM item_transaksi it
            JOIN transaksi_harian th ON th.id = it.transaksi_id
            WHERE it.produk_id = $1 AND th.tenant_id = $2
            ORDER BY th.timestamp DESC, th.created_at DESC
            LIMIT $3 OFFSET $4
        """
        rows = await conn.fetch(tx_query, str(item_id), tenant_id, limit, offset)

        # Get total count
        count = await conn.fetchval(
            """SELECT COUNT(*) FROM item_transaksi it
               JOIN transaksi_harian th ON th.id = it.transaksi_id
               WHERE it.produk_id = $1 AND th.tenant_id = $2""",
            str(item_id),
            tenant_id,
        )

        transactions = [
            ItemTransaction(
                id=row.get("id"),
                date=row["date"] or "",
                transaction_type=row.get("transaction_type", "unknown"),
                document_number=row.get("document_number"),
                qty_change=float(row.get("qty_change", 0)),
                unit_price=float(row["unit_price"]) if row.get("unit_price") else None,
                total=float(row["total"]) if row.get("total") else None,
                notes=row.get("notes"),
            )
            for row in rows
        ]

        return ItemTransactionsResponse(
            success=True,
            transactions=transactions,
            total=count or 0,
            has_more=(offset + limit) < (count or 0),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting item transactions: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn:
            await conn.close()


# =============================================================================
# RELATED DOCUMENTS (Tab Terkait)
# =============================================================================


@router.get("/items/{item_id}/related", response_model=ItemRelatedResponse)
async def get_item_related(request: Request, item_id: UUID):
    """Get related documents (invoices, bills) for an item."""
    tenant_id = get_tenant_id(request)
    conn = None

    try:
        conn = await get_db_connection()

        # Verify item exists
        item_exists = await conn.fetchval(
            "SELECT id FROM products WHERE id = $1 AND tenant_id = $2",
            str(item_id),
            tenant_id,
        )
        if not item_exists:
            raise HTTPException(status_code=404, detail="Item not found")

        # Related invoices (sales)
        invoices = []
        try:
            inv_query = """
                SELECT
                    si.id::text,
                    si.invoice_number as document_number,
                    si.invoice_date::text as date,
                    si.customer_name as counterparty,
                    sii.quantity as qty,
                    sii.unit_price,
                    sii.total as total,
                    si.status
                FROM sales_invoice_items sii
                JOIN sales_invoices si ON si.id = sii.invoice_id
                WHERE sii.item_id = $1 AND si.tenant_id = $2
                ORDER BY si.invoice_date DESC
                LIMIT 20
            """
            inv_rows = await conn.fetch(inv_query, str(item_id), tenant_id)
            invoices = [
                RelatedDocument(
                    id=row["id"],
                    document_type="invoice",
                    document_number=row.get("document_number"),
                    date=row.get("date"),
                    counterparty=row.get("counterparty"),
                    qty=float(row["qty"]) if row.get("qty") else None,
                    unit_price=float(row["unit_price"])
                    if row.get("unit_price")
                    else None,
                    total=float(row["total"]) if row.get("total") else None,
                    status=row.get("status"),
                )
                for row in inv_rows
            ]
        except Exception as e:
            logger.warning(f"Could not fetch related invoices: {e}")

        # Related bills (purchases)
        bills = []
        try:
            bill_query = """
                SELECT
                    b.id::text,
                    b.invoice_number as document_number,
                    b.issue_date::text as date,
                    b.vendor_name as counterparty,
                    bi.quantity as qty,
                    bi.unit_price,
                    bi.total as total,
                    b.status
                FROM bill_items bi
                JOIN bills b ON b.id = bi.bill_id
                WHERE bi.product_id = $1 AND b.tenant_id = $2
                ORDER BY b.issue_date DESC
                LIMIT 20
            """
            bill_rows = await conn.fetch(bill_query, str(item_id), tenant_id)
            bills = [
                RelatedDocument(
                    id=row["id"],
                    document_type="bill",
                    document_number=row.get("document_number"),
                    date=row.get("date"),
                    counterparty=row.get("counterparty"),
                    qty=float(row["qty"]) if row.get("qty") else None,
                    unit_price=float(row["unit_price"])
                    if row.get("unit_price")
                    else None,
                    total=float(row["total"]) if row.get("total") else None,
                    status=row.get("status"),
                )
                for row in bill_rows
            ]
        except Exception as e:
            logger.warning(f"Could not fetch related bills: {e}")

        return ItemRelatedResponse(
            success=True,
            invoices=invoices,
            bills=bills,
            purchase_orders=[],  # PO table may not exist yet
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting related documents: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn:
            await conn.close()


# =============================================================================
# DUPLICATE ITEM
# =============================================================================


@router.post("/items/{item_id}/duplicate", response_model=CreateItemResponse)
async def duplicate_item(request: Request, item_id: UUID):
    """Duplicate an existing item with '(Copy)' suffix. Does not copy stock or images."""
    tenant_id = get_tenant_id(request)
    conn = None

    try:
        conn = await get_db_connection()

        # Fetch original item
        item_row = await conn.fetchrow(
            "SELECT * FROM products WHERE id = $1 AND tenant_id = $2",
            str(item_id),
            tenant_id,
        )
        if not item_row:
            raise HTTPException(status_code=404, detail="Item not found")

        # Generate copy name
        original_name = item_row["nama_produk"]
        copy_name = f"{original_name} (Copy)"

        # Ensure unique name
        suffix = 1
        while await conn.fetchval(
            "SELECT id FROM products WHERE tenant_id = $1 AND nama_produk = $2",
            tenant_id,
            copy_name,
        ):
            suffix += 1
            copy_name = f"{original_name} (Copy {suffix})"

        async with conn.transaction():
            # Insert duplicate (without stock, image, barcode)
            new_id = await conn.fetchval(
                """
                INSERT INTO products (
                    tenant_id, nama_produk, satuan, base_unit, kategori, deskripsi,
                    item_type, track_inventory, is_returnable,
                    sales_account, purchase_account, sales_tax, purchase_tax,
                    sales_price, purchase_price, harga_jual,
                    reorder_level, preferred_vendor_id,
                    sales_account_id, purchase_account_id,
                    created_at, updated_at
                ) VALUES (
                    $1, $2, $3, $4, $5, $6,
                    $7, $8, $9,
                    $10, $11, $12, $13,
                    $14, $15, $16,
                    $17, $18,
                    $19, $20,
                    NOW(), NOW()
                )
                RETURNING id
                """,
                tenant_id,
                copy_name,
                item_row.get("satuan"),
                item_row.get("base_unit") or item_row.get("satuan"),
                item_row.get("kategori"),
                item_row.get("deskripsi"),
                item_row.get("item_type", "goods"),
                item_row.get("track_inventory", True),
                item_row.get("is_returnable", True),
                item_row.get("sales_account", "Sales"),
                item_row.get("purchase_account", "Cost of Goods Sold"),
                item_row.get("sales_tax"),
                item_row.get("purchase_tax"),
                item_row.get("sales_price"),
                item_row.get("purchase_price"),
                item_row.get("sales_price"),  # harga_jual
                float(item_row["reorder_level"])
                if item_row.get("reorder_level")
                else None,
                str(item_row["preferred_vendor_id"])
                if item_row.get("preferred_vendor_id")
                else None,
                str(item_row["sales_account_id"])
                if item_row.get("sales_account_id")
                else None,
                str(item_row["purchase_account_id"])
                if item_row.get("purchase_account_id")
                else None,
            )

            # Duplicate unit conversions
            conversions = await conn.fetch(
                "SELECT * FROM unit_conversions WHERE product_id = $1 AND tenant_id = $2 AND is_active = true",
                str(item_id),
                tenant_id,
            )
            for conv in conversions:
                await conn.execute(
                    """
                    INSERT INTO unit_conversions (
                        tenant_id, product_id, base_unit, conversion_unit, conversion_factor,
                        purchase_price, sales_price, is_active, created_at, updated_at
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, true, NOW(), NOW())
                    """,
                    tenant_id,
                    new_id,
                    conv["base_unit"],
                    conv["conversion_unit"],
                    conv["conversion_factor"],
                    conv.get("purchase_price"),
                    conv.get("sales_price"),
                )

            # Create initial stock entry if tracked
            if item_row.get("track_inventory") and item_row.get("item_type") == "goods":
                await conn.execute(
                    """
                    INSERT INTO persediaan (
                        id, tenant_id, product_id, produk_id, lokasi_gudang, jumlah,
                        nilai_per_unit, total_nilai, created_at, updated_at
                    ) VALUES ($1, $2, $3, $4, 'gudang_utama', 0, $5, 0, NOW(), NOW())
                    """,
                    str(uuid.uuid4()),
                    tenant_id,
                    new_id,
                    str(new_id),
                    item_row.get("purchase_price") or 0,
                )

        logger.info(f"Duplicated item {item_id} -> {new_id} for tenant {tenant_id}")

        return CreateItemResponse(
            success=True,
            message=f"Item '{copy_name}' berhasil diduplikasi",
            data={
                "id": str(new_id),
                "name": copy_name,
                "item_type": item_row.get("item_type", "goods"),
                "source_id": str(item_id),
            },
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error duplicating item: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn:
            await conn.close()


# Alias for inventory_asset (frontend compatibility)
@router.get("/items/accounts/inventory_asset", response_model=CoaAccountsResponse)
async def list_inventory_asset_accounts(request: Request):
    """Alias for /items/accounts/inventory - frontend compatibility."""
    return await list_inventory_accounts(request)


@router.get("/items/accounts/cogs", response_model=CoaAccountsResponse)
async def list_cogs_accounts(request: Request):
    """
    Get list of COGS (Cost of Goods Sold) accounts from Chart of Accounts.

    Filters for expense accounts related to cost of goods sold.
    """
    tenant_id = get_tenant_id(request)
    conn = None

    try:
        conn = await get_db_connection()

        query = """
            SELECT id, account_code, name, account_type
            FROM chart_of_accounts
            WHERE tenant_id = $1
              AND account_type IN ('EXPENSE', 'COGS')
              AND is_active = true
              AND (
                  name ILIKE '%%hpp%%'
                  OR name ILIKE '%%harga pokok%%'
                  OR name ILIKE '%%cost of goods%%'
                  OR name ILIKE '%%cogs%%'
                  OR account_code LIKE '5-1%%'
              )
            ORDER BY account_code ASC
        """
        rows = await conn.fetch(query, tenant_id)

        accounts = [
            CoaAccountOption(
                id=str(row["id"]),
                code=row["account_code"],
                name=row["name"],
                account_type=row["account_type"],
            )
            for row in rows
        ]

        return CoaAccountsResponse(success=True, accounts=accounts)
    except Exception as e:
        logger.error(f"Error fetching COGS accounts: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn:
            await conn.close()


@router.post("/items/{item_id}/stock-adjustment")
async def create_stock_adjustment(request: Request, item_id: str):
    """Create a stock adjustment for an item."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        body = await request.json()

        adjustment_type = body.get(
            "type", "adjustment"
        )  # adjustment, increase, decrease
        quantity = body.get("quantity", 0)
        reason = body.get("reason", "Manual adjustment")
        reference = body.get("reference")

        async with pool.acquire() as conn:
            # Get current stock
            item = await conn.fetchrow(
                "SELECT id, name, current_stock FROM items WHERE id = $1 AND tenant_id = $2",
                item_id,
                ctx["tenant_id"],
            )
            if not item:
                raise HTTPException(status_code=404, detail="Item not found")

            old_stock = item["current_stock"] or 0

            if adjustment_type == "increase":
                new_stock = old_stock + abs(quantity)
            elif adjustment_type == "decrease":
                new_stock = max(0, old_stock - abs(quantity))
            else:
                new_stock = quantity

            # Update stock
            await conn.execute(
                """
                UPDATE items SET current_stock = $1, updated_at = NOW()
                WHERE id = $2 AND tenant_id = $3
            """,
                new_stock,
                item_id,
                ctx["tenant_id"],
            )

            # Log adjustment (simplified - in production, use stock_adjustments table)
            logger.info(
                f"Stock adjusted for {item_id}: {old_stock} -> {new_stock}, reason: {reason}"
            )

            return {
                "success": True,
                "message": "Stock adjusted",
                "old_stock": old_stock,
                "new_stock": new_stock,
                "adjustment": new_stock - old_stock,
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error adjusting stock: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to adjust stock")


# =============================================================================
# ITEMS JOURNAL ENTRIES
# =============================================================================


@router.get("/items/{item_id}/journal-entries")
async def get_item_journal_entries(
    request: Request,
    item_id: str,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
):
    """Get journal entries related to an item."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        offset = (page - 1) * limit

        async with pool.acquire() as conn:
            item = await conn.fetchrow(
                "SELECT id, name FROM items WHERE id = $1 AND tenant_id = $2",
                item_id,
                ctx["tenant_id"],
            )
            if not item:
                raise HTTPException(status_code=404, detail="Item not found")

            # Get journal entries that reference this item
            rows = await conn.fetch(
                """
                SELECT
                    j.id, j.journal_number, j.journal_date, j.description,
                    jl.account_id, jl.debit, jl.credit,
                    a.name as account_name
                FROM journals j
                JOIN journal_lines jl ON jl.journal_id = j.id
                LEFT JOIN accounts a ON a.id = jl.account_id
                WHERE j.tenant_id = $1
                  AND jl.item_id::text = $2
                ORDER BY j.journal_date DESC, j.created_at DESC
                LIMIT $3 OFFSET $4
            """,
                ctx["tenant_id"],
                item_id,
                limit,
                offset,
            )

            entries = [
                {
                    "id": str(row["id"]),
                    "journal_number": row["journal_number"],
                    "date": row["journal_date"].isoformat()
                    if row["journal_date"]
                    else None,
                    "description": row["description"],
                    "account_name": row["account_name"],
                    "debit": row["debit"],
                    "credit": row["credit"],
                }
                for row in rows
            ]

            return {"success": True, "entries": entries, "page": page, "limit": limit}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting journal entries: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get journal entries")
