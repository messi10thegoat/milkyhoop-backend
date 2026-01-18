"""
Sorting utilities for API endpoints.

Provides compound sort parsing and SQL generation.
"""

from typing import List, Tuple
import re


def parse_sort_param(sort: str) -> List[Tuple[str, str]]:
    """Parse comma-separated sort string into list of (field, order) tuples."""
    if not sort or not sort.strip():
        return [("created_at", "desc")]

    result = []
    parts = sort.split(",")

    for part in parts:
        part = part.strip()
        if not part:
            continue

        if ":" in part:
            field, order = part.split(":", 1)
            field = field.strip().lower()
            order = order.strip().lower()
            if order not in ("asc", "desc"):
                order = "desc"
        else:
            field = part.strip().lower()
            order = "desc"

        if re.match(r'^[a-z_][a-z0-9_]*$', field):
            result.append((field, order))

    if not result:
        return [("created_at", "desc")]

    return result


def build_order_by_clause(
    sort_fields: List[Tuple[str, str]],
    field_mapping: dict,
    default_field: str = "created_at"
) -> str:
    """Build SQL ORDER BY clause from parsed sort fields."""
    clauses = []

    for field, order in sort_fields:
        sql_expr = field_mapping.get(field)
        if sql_expr is None:
            continue

        direction = "DESC" if order == "desc" else "ASC"
        null_pos = "NULLS LAST" if direction == "ASC" else "NULLS FIRST"
        clauses.append(f"{sql_expr} {direction} {null_pos}")

    if not clauses:
        default_expr = field_mapping.get(default_field, default_field)
        return f"{default_expr} DESC NULLS FIRST"

    return ", ".join(clauses)
