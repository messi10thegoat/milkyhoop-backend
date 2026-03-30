"""
Status derivation helpers — single source of truth for doc_status.

REPLACES: inline ternary doc_status derivations scattered across services.
ALIGNED WITH: compute_ap/ar_outstanding() DB functions.

Usage:
    from app.services.status_helpers import derive_doc_status
    response["doc_status"] = derive_doc_status(row)
"""


def derive_doc_status(row: dict) -> str:
    """
    Derive doc_status from row data. Single source of truth.

    Priority order:
    1. status (computed field from CASE expression — most reliable)
    2. status_v2 (bills-specific lifecycle column)
    3. operational_status (legacy fallback)

    Returns: "draft" | "posted" | "void"
    """
    # Check computed status first (from CASE expression in SELECT)
    status = (row.get("status") or "").lower()
    if status in ("void", "voided"):
        return "void"
    if status == "draft":
        return "draft"

    # Check status_v2 (bills table specific)
    status_v2 = (row.get("status_v2") or "").lower()
    if status_v2 in ("void", "voided"):
        return "void"
    if status_v2 == "draft":
        return "draft"

    # Check operational_status (legacy)
    op_status = (row.get("operational_status") or "").upper()
    if op_status == "VOID":
        return "void"
    if op_status == "DRAFT":
        return "draft"

    # If status is a known posted-like value
    if status in ("posted", "paid", "partial", "overdue", "unpaid", "applied"):
        return "posted"
    if status_v2 in ("posted",):
        return "posted"
    if op_status in ("POSTED", "COMPLETED"):
        return "posted"

    # Default: safer to assume draft than posted
    return "draft"


# Filter constant — consistent with compute_ap/ar_outstanding() DB functions
EXCLUDE_DRAFT_VOID = "NOT IN (draft, void)"
