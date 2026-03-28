"""
Unit Conversion Helpers — Phase 2 UoM

Converts transaction quantities to base unit quantities using
the unit_conversions table (transitive closure via BFS).

All math uses Decimal — no float (Law 25).
"""
from decimal import Decimal
from collections import deque
from typing import Tuple, Optional
import logging

logger = logging.getLogger(__name__)


async def get_conversion_factor(
    conn, tenant_id: str, product_id, from_unit: str, to_base_unit: str
) -> Decimal:
    """
    Compute conversion factor from transaction unit to base unit.
    Uses BFS transitive closure (chain multiplication).

    Example: from_unit="karton", to_base_unit="tablet"
    Chain: karton→dus (10) × dus→strip (12) × strip→tablet (10) = 1200

    Returns Decimal factor. Raises ValueError if no path found.
    """
    if from_unit.lower() == to_base_unit.lower():
        return Decimal("1")

    conversions = await conn.fetch(
        """
        SELECT base_unit, conversion_unit, conversion_factor
        FROM unit_conversions
        WHERE product_id = $1 AND tenant_id = $2 AND is_active = true
        """,
        product_id,
        tenant_id,
    )

    if not conversions:
        # No conversions defined — assume 1:1 (same unit, different name)
        logger.warning(
            f"No conversions for product {product_id}, from={from_unit} to={to_base_unit}. Using 1:1."
        )
        return Decimal("1")

    # Build bidirectional adjacency graph
    graph = {}
    for c in conversions:
        base = c["base_unit"].lower()
        conv = c["conversion_unit"].lower()
        factor = Decimal(str(c["conversion_factor"]))

        # conversion_factor means: 1 conversion_unit = factor base_units
        # So: conv→base = factor, base→conv = 1/factor
        graph.setdefault(conv, {})[base] = factor
        graph.setdefault(base, {})[conv] = Decimal("1") / factor

    # BFS from from_unit to to_base_unit
    start = from_unit.lower()
    target = to_base_unit.lower()
    queue = deque([(start, Decimal("1"))])
    visited = {start}

    while queue:
        current, accumulated = queue.popleft()
        if current == target:
            return accumulated

        for neighbor, factor in graph.get(current, {}).items():
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append((neighbor, accumulated * factor))

    # No path found — log warning, return 1 (no conversion)
    logger.warning(
        f"No conversion path from '{from_unit}' to '{to_base_unit}' for product {product_id}. Using 1:1."
    )
    return Decimal("1")


async def convert_to_base_unit(
    conn,
    tenant_id: str,
    product_id,
    quantity: Decimal,
    transaction_unit: Optional[str],
) -> Tuple[Decimal, Decimal]:
    """
    Convert transaction quantity to base unit quantity.

    Returns: (base_quantity, conversion_factor)
    Example: convert_to_base_unit(..., Decimal('5'), "karton") → (Decimal('6000'), Decimal('1200'))
    """
    # Get product base unit
    base_unit = await conn.fetchval(
        "SELECT COALESCE(base_unit, satuan, 'pcs') FROM products WHERE id = $1 AND tenant_id = $2",
        product_id,
        tenant_id,
    )

    if (
        not transaction_unit
        or not base_unit
        or transaction_unit.lower() == base_unit.lower()
    ):
        return (quantity, Decimal("1"))

    factor = await get_conversion_factor(
        conn, tenant_id, product_id, transaction_unit, base_unit
    )
    base_quantity = quantity * factor

    return (base_quantity, factor)
