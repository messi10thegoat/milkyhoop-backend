"""
Inventory Helpers
=================
Shared inventory operations for all sales flows (invoices, receipts, POS).
This is the CANONICAL implementation — all flows should call these helpers
instead of implementing their own COGS/inventory logic.

Architecture:
- record_inventory_outbound(): Sale flows (invoice, receipt, POS)
- record_inventory_inbound(): Purchase flows (bill)
- resolve_inventory_accounts(): Product-level → tenant-default account resolution

References:
- milkyhoop-inventory skill: Inventory & COGS Integrity Framework
- milkyhoop-ironlaws skill: Accounting Iron Laws
"""
import logging
from decimal import Decimal
from typing import Optional
from uuid import UUID, uuid4

logger = logging.getLogger(__name__)

async def resolve_inventory_accounts(
    conn, tenant_id: str, product_id: UUID
) -> dict:
    """
    Resolve COGS and Inventory GL accounts.
    Priority: product-level → tenant default.
    Returns {"cogs_account_id": uuid, "inventory_account_id": uuid}.
    """
    product_accounts = await conn.fetchrow(
        "SELECT cogs_account_id, inventory_account_id FROM products WHERE id = $1",
        product_id
    )

    cogs_acct = product_accounts["cogs_account_id"] if product_accounts else None
    inv_acct = product_accounts["inventory_account_id"] if product_accounts else None

    if not cogs_acct:
        cogs_acct = await conn.fetchval(
            "SELECT id FROM chart_of_accounts WHERE tenant_id = $1 AND account_code = '5-10100' AND is_active = true",
            tenant_id
        )
    if not inv_acct:
        inv_acct = await conn.fetchval(
            "SELECT id FROM chart_of_accounts WHERE tenant_id = $1 AND account_code = '1-10600' AND is_active = true",
            tenant_id
        )

    return {"cogs_account_id": cogs_acct, "inventory_account_id": inv_acct}

async def get_cost_for_sale(
    conn, tenant_id: str, product_id: UUID
) -> dict:
    """
    Get unit cost for a sale using WAC with fallback to purchase_price.
    Returns {"unit_cost": Decimal, "cost_source": str}.
    """
    avg_cost = await conn.fetchval(
        "SELECT get_weighted_average_cost($1, $2)",
        tenant_id, product_id
    )
    cost_source = "WEIGHTED_AVG"

    if not avg_cost or avg_cost == 0:
        avg_cost = await conn.fetchval(
            "SELECT COALESCE(purchase_price_amount, purchase_price, 0) FROM products WHERE id = $1",
            product_id
        )
        cost_source = "PURCHASE_PRICE"

    return {
        "unit_cost": Decimal(str(avg_cost)) if avg_cost else Decimal("0"),
        "cost_source": cost_source,
    }

async def record_inventory_outbound(
    conn,
    tenant_id: str,
    product_id: UUID,
    product_code: Optional[str],
    product_name: str,
    warehouse_id: UUID,
    quantity: float,
    source_type: str,
    source_id: UUID,
    source_number: str,
    user_id: Optional[UUID],
    notes: str,
    receipt_date=None,
) -> dict:
    """
    Standard outbound flow: get WAC, insert inventory_ledger, create COGS journal.
    DB trigger auto-updates warehouse_stock.

    MUST be called within an existing transaction (conn.transaction()).

    Returns {
        "ledger_id": uuid,
        "journal_id": uuid | None,
        "unit_cost": Decimal,
        "total_cost": int,
        "cost_source": str,
    }
    """
    from datetime import date as date_type
    movement_date = receipt_date or date_type.today()

    # 1. Get weighted average cost
    cost_info = await get_cost_for_sale(conn, tenant_id, product_id)
    unit_cost = cost_info["unit_cost"]
    cost_source = cost_info["cost_source"]
    total_cost = int(float(quantity) * float(unit_cost))

    if total_cost == 0:
        logger.warning(
            f"Zero COGS for product {product_id} (source: {cost_source}). "
            f"Skipping COGS journal but still recording ledger movement."
        )

    # 2. Get current balance
    current_balance = await conn.fetchval(
        "SELECT COALESCE(SUM(quantity_in - quantity_out), 0) FROM inventory_ledger WHERE tenant_id = $1 AND product_id = $2",
        tenant_id, product_id
    )
    new_balance = float(current_balance) - quantity

    # Get average_cost snapshot
    avg_cost_snapshot = await conn.fetchval(
        "SELECT get_weighted_average_cost($1, $2)",
        tenant_id, product_id
    ) or unit_cost

    # 3. Insert inventory_ledger (trigger handles warehouse_stock)
    ledger_id = uuid4()
    await conn.execute(
        """
        INSERT INTO inventory_ledger (
            id, tenant_id, product_id, product_code, product_name,
            movement_type, movement_date, source_type, source_id, source_number,
            quantity_in, quantity_out, quantity_balance,
            unit_cost, total_cost, average_cost,
            warehouse_id, created_by, notes
        ) VALUES (
            $1, $2, $3, $4, $5,
            'SALE', $6, $7, $8, $9,
            0, $10, $11,
            $12, $13, $14,
            $15, $16, $17
        )
        """,
        ledger_id, tenant_id, product_id, product_code, product_name,
        movement_date, source_type, source_id, source_number,
        quantity, new_balance,
        unit_cost, total_cost, avg_cost_snapshot,
        warehouse_id, user_id, notes,
    )

    # 4. Create COGS journal (if cost > 0)
    journal_id = None
    if total_cost > 0:
        accounts = await resolve_inventory_accounts(conn, tenant_id, product_id)
        hpp_acct = accounts["cogs_account_id"]
        inv_acct = accounts["inventory_account_id"]

        if hpp_acct and inv_acct:
            journal_id = uuid4()

            # Determine journal source_type based on flow
            journal_source_type = {
                "SALES_INVOICE": "SALES_INVOICE_COGS",
                "POS_SALE": "SALES_RECEIPT_COGS",
            }.get(source_type, f"{source_type}_COGS")

            await conn.execute(
                """
                INSERT INTO journal_entries (
                    id, tenant_id, journal_number, journal_date,
                    description, source_type, source_id,
                    total_debit, total_credit, status, created_by
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $8, 'DRAFT', $9)
                """,
                journal_id, tenant_id,
                f"COGS-{source_number}",
                movement_date,
                f"HPP {source_number}",
                journal_source_type, source_id,
                total_cost, user_id,
            )

            await conn.execute(
                """
                INSERT INTO journal_lines (journal_id, account_id, memo, debit, credit, line_number)
                VALUES ($1, $2, 'HPP Barang Dagang', $3, 0, 1),
                       ($1, $4, 'Persediaan Barang Dagang', 0, $3, 2)
                """,
                journal_id, hpp_acct, total_cost, inv_acct,
            )

    # 5. Link journal to ledger

            # Post the journal (triggers hash chain: Law 20)
            await conn.execute(
                "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1",
                journal_id
            )
    if journal_id:
        await conn.execute(
            "UPDATE inventory_ledger SET journal_id = $1 WHERE id = $2",
            journal_id, ledger_id
        )

    return {
        "ledger_id": ledger_id,
        "journal_id": journal_id,
        "unit_cost": unit_cost,
        "total_cost": total_cost,
        "cost_source": cost_source,
    }

async def record_inventory_inbound(
    conn,
    tenant_id: str,
    product_id: UUID,
    product_code: Optional[str],
    product_name: str,
    warehouse_id: UUID,
    quantity: float,
    unit_cost: float,
    source_type: str,
    source_id: UUID,
    source_number: str,
    user_id: Optional[UUID],
    notes: str,
    movement_date=None,
) -> dict:
    """
    Standard inbound flow: calculate new WAC, insert inventory_ledger.
    DB trigger auto-updates warehouse_stock.
    GL journal is NOT created here — caller (bills_service) handles GL.

    Returns {"ledger_id": uuid, "new_average_cost": Decimal}.
    """
    from datetime import date as date_type
    if movement_date is None:
        movement_date = date_type.today()

    quantity_dec = Decimal(str(quantity))
    unit_cost_dec = Decimal(str(unit_cost))
    total_cost = quantity_dec * unit_cost_dec

    # Calculate new WAC
    balance_row = await conn.fetchrow(
        """
        SELECT COALESCE(SUM(quantity_in) - SUM(quantity_out), 0) as balance
        FROM inventory_ledger WHERE tenant_id = $1 AND product_id = $2
        """,
        tenant_id, product_id
    )
    current_balance = Decimal(str(balance_row["balance"]))
    new_balance = current_balance + quantity_dec

    avg_cost_row = await conn.fetchrow(
        """
        SELECT
            COALESCE(SUM(quantity_in * unit_cost), 0) as total_value,
            COALESCE(SUM(quantity_in) - SUM(quantity_out), 0) as total_qty
        FROM inventory_ledger WHERE tenant_id = $1 AND product_id = $2
        """,
        tenant_id, product_id
    )

    if avg_cost_row and Decimal(str(avg_cost_row["total_qty"])) > 0:
        old_value = Decimal(str(avg_cost_row["total_value"]))
        old_qty = Decimal(str(avg_cost_row["total_qty"]))
        new_avg_cost = (old_value + total_cost) / (old_qty + quantity_dec)
    else:
        new_avg_cost = unit_cost_dec

    # Insert inventory_ledger
    ledger_id = uuid4()
    await conn.execute(
        """
        INSERT INTO inventory_ledger (
            id, tenant_id, product_id, product_code, product_name,
            movement_type, movement_date, source_type, source_id, source_number,
            quantity_in, quantity_out, quantity_balance,
            unit_cost, total_cost, average_cost,
            warehouse_id, created_by, notes
        ) VALUES (
            $1, $2, $3, $4, $5,
            'PURCHASE', $6, $7, $8, $9,
            $10, 0, $11,
            $12, $13, $14,
            $15, $16, $17
        )
        """,
        ledger_id, tenant_id, product_id, product_code, product_name,
        movement_date, source_type, source_id, source_number,
        quantity_dec, new_balance,
        unit_cost_dec, total_cost, new_avg_cost,
        warehouse_id, user_id, notes,
    )

    return {
        "ledger_id": ledger_id,
        "new_average_cost": new_avg_cost,
    }



async def record_inventory_reversal(
    conn,
    tenant_id: str,
    source_type: str,
    source_id: UUID,
    reversal_journal_id: UUID,
    created_by: Optional[UUID] = None,
    reversal_date=None,
    notes_prefix: str = "VOID",
) -> list:
    """
    Reverse all inventory_ledger entries for a given source.

    Finds original entries by source_type + source_id, creates mirror entries
    (swap quantity_in <-> quantity_out), links journal_id to reversal journal.

    Architecture (milkyhoop-inventory Rule 9):
    - Original PURCHASE (qty_in) -> reversal PURCHASE_RETURN (qty_out)
    - Original SALE (qty_out) -> reversal VOID_REVERSAL (qty_in)
    - source_type = "{original}_VOID" (e.g. BILL_VOID, SALES_INVOICE_VOID)
    - journal_id = reversal_journal_id (NOT original — D5 fix)
    - WAC: snapshot only, no recalc on outbound (Rule 3)

    MUST be called within an existing transaction (conn.transaction()).

    Returns list of {"product_id", "ledger_id", "quantity_reversed", "direction"}.
    """
    from datetime import date as date_type

    movement_date = reversal_date or date_type.today()

    # Find original inventory_ledger entries for this source
    original_entries = await conn.fetch(
        """
        SELECT id, product_id, product_code, product_name,
               quantity_in, quantity_out, unit_cost, total_cost,
               warehouse_id, source_number
        FROM inventory_ledger
        WHERE tenant_id = $1 AND source_type = $2 AND source_id = $3
        ORDER BY created_at
        """,
        tenant_id, source_type, source_id,
    )

    if not original_entries:
        logger.info(
            f"No inventory_ledger entries found for {source_type}:{source_id} — "
            "skipping inventory reversal"
        )
        return []

    void_source_type = f"{source_type}_VOID"
    results = []

    for entry in original_entries:
        product_id = entry["product_id"]
        orig_qty_in = Decimal(str(entry["quantity_in"] or 0))
        orig_qty_out = Decimal(str(entry["quantity_out"] or 0))
        unit_cost = Decimal(str(entry["unit_cost"] or 0))

        # Mirror: swap quantity_in and quantity_out
        rev_qty_in = orig_qty_out    # if original was outbound, reversal restores stock
        rev_qty_out = orig_qty_in    # if original was inbound, reversal removes stock

        quantity_reversed = orig_qty_in if orig_qty_in > 0 else orig_qty_out

        # Determine movement_type
        if orig_qty_in > 0:
            movement_type = "PURCHASE_RETURN"   # Reversing inbound (purchase void)
        else:
            movement_type = "VOID_REVERSAL"     # Reversing outbound (restoring stock)

        # Calculate current running balance
        balance_row = await conn.fetchrow(
            """
            SELECT COALESCE(SUM(quantity_in) - SUM(quantity_out), 0) AS balance
            FROM inventory_ledger
            WHERE tenant_id = $1 AND product_id = $2
            """,
            tenant_id, product_id,
        )
        current_balance = Decimal(str(balance_row["balance"])) if balance_row else Decimal("0")
        new_balance = current_balance + rev_qty_in - rev_qty_out

        # WAC snapshot — no recalc on outbound (milkyhoop-inventory Rule 3)
        avg_cost = await conn.fetchval(
            "SELECT get_weighted_average_cost($1, $2)",
            tenant_id, product_id,
        )
        avg_cost = Decimal(str(avg_cost)) if avg_cost else unit_cost

        total_cost = quantity_reversed * unit_cost

        # Insert reversal entry — DB trigger auto-updates warehouse_stock
        ledger_id = uuid4()
        await conn.execute(
            """
            INSERT INTO inventory_ledger (
                id, tenant_id, product_id, product_code, product_name,
                movement_type, movement_date, source_type, source_id, source_number,
                quantity_in, quantity_out, quantity_balance,
                unit_cost, total_cost, average_cost,
                warehouse_id, journal_id, created_by, notes
            ) VALUES (
                $1, $2, $3, $4, $5,
                $6, $7, $8, $9, $10,
                $11, $12, $13,
                $14, $15, $16,
                $17, $18, $19, $20
            )
            """,
            ledger_id, tenant_id, product_id,
            entry["product_code"], entry["product_name"],
            movement_type, movement_date,
            void_source_type, source_id, entry["source_number"],
            rev_qty_in, rev_qty_out, new_balance,
            unit_cost, total_cost, avg_cost,
            entry["warehouse_id"], reversal_journal_id, created_by,
            f"{notes_prefix} - inventory reversal",
        )

        results.append({
            "product_id": product_id,
            "ledger_id": ledger_id,
            "quantity_reversed": quantity_reversed,
            "direction": "outbound" if rev_qty_out > 0 else "inbound",
        })

        logger.info(
            f"Inventory reversal: {void_source_type} product={product_id} "
            f"qty_in={rev_qty_in} qty_out={rev_qty_out} balance={new_balance}"
        )

    return results
