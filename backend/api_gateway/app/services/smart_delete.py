"""
smart_delete — shared utility for CRUD delete with ledger footprint check.

Rule: if entity has ANY ledger/transaction footprint → raise HTTPException (user must deactivate).
Otherwise → hard delete the entity + cascade cleanup of non-ledger related rows.

Used by:
- items (products)
- customers
- vendors
- future: accounts, warehouses, banks

Usage:
    result = await smart_delete(
        conn,
        tenant_id=ctx["tenant_id"],
        entity_id=str(item_id),
        table="products",
        name_column="nama_produk",
        ledger_checks=[
            ("inventory_ledger", "product_id", None),
            ("sales_invoice_items", "item_id", None),
            ("bill_items", "product_id", None),
        ],
        cascade_tables=[
            ("item_pricing", "product_id"),
            ("unit_conversions", "product_id"),
        ],
        has_footprint_error="Item '{name}' tidak bisa dihapus karena masih memiliki transaksi. Nonaktifkan saja.",
    )
"""
from fastapi import HTTPException
from typing import Optional


async def smart_delete(
    conn,
    tenant_id: str,
    entity_id: str,
    table: str,
    name_column: str,
    ledger_checks: list[tuple[str, str, Optional[str]]],
    cascade_tables: Optional[list[tuple[str, str]]] = None,
    has_footprint_error: str = "Tidak bisa dihapus karena masih memiliki transaksi. Nonaktifkan saja.",
    id_cast: str = "",  # "::uuid" if needed for type mismatch
    extra_exists_filter: str = "",  # extra SQL WHERE clause for entity existence check
) -> dict:
    """
    Args:
        conn: asyncpg connection (must be in transaction for cascade safety)
        tenant_id: tenant UUID/slug for RLS
        entity_id: the row to delete
        table: entity table name (e.g. "products", "customers")
        name_column: name column for response/error (e.g. "nama_produk", "nama", "name")
        ledger_checks: list of (table_name, fk_column, custom_error_msg_or_None)
        cascade_tables: list of (table_name, fk_column) to DELETE before main entity
        has_footprint_error: error message if ANY ledger check returns rows (use {name} placeholder)
        id_cast: e.g. "::uuid" if FK column needs cast
        extra_exists_filter: additional SQL (e.g. "AND deleted_at IS NULL")

    Returns:
        dict with {"success": True, "message": str, "name": str}

    Raises:
        HTTPException 404 if entity not found
        HTTPException 400 if ledger footprint exists
    """
    # 1. Verify entity exists
    existing = await conn.fetchrow(
        f"SELECT {name_column} FROM {table} WHERE id = $1 AND tenant_id = $2 {extra_exists_filter}",
        entity_id,
        tenant_id,
    )
    if not existing:
        raise HTTPException(
            status_code=404, detail=f"{table.rstrip('s').capitalize()} not found"
        )

    entity_name = existing[name_column]

    # 2. Check ledger footprint — raise on first hit (fast fail)
    for check in ledger_checks:
        # Unpack: (table, fk, custom_msg) or (table, fk, custom_msg, cast_override)
        if len(check) == 4:
            check_table, fk_col, custom_msg, cast_override = check
        else:
            check_table, fk_col, custom_msg = check
            cast_override = None
        _cast = cast_override if cast_override is not None else id_cast
        count = await conn.fetchval(
            f"SELECT COUNT(*) FROM {check_table} WHERE {fk_col}{_cast} = $1",
            entity_id,
        )
        if count and count > 0:
            msg = custom_msg or has_footprint_error
            raise HTTPException(
                status_code=400,
                detail=msg.format(name=entity_name, count=count),
            )

    # 3. No footprint — cascade cleanup (delete rows from non-ledger tables)
    if cascade_tables:
        for cascade_table, cascade_fk in cascade_tables:
            await conn.execute(
                f"DELETE FROM {cascade_table} WHERE {cascade_fk}{id_cast} = $1",
                entity_id,
            )

    # 4. Hard delete main entity
    await conn.execute(
        f"DELETE FROM {table} WHERE id = $1 AND tenant_id = $2",
        entity_id,
        tenant_id,
    )

    return {
        "success": True,
        "message": f"'{entity_name}' berhasil dihapus",
        "name": entity_name,
    }
