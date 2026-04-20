"""
Status derivation helpers — single source of truth for doc_status.

REPLACES: inline ternary doc_status derivations scattered across services.
ALIGNED WITH: compute_ap/ar_outstanding() DB functions.

Usage:
    from app.services.status_helpers import derive_doc_status
    response["doc_status"] = derive_doc_status(row)

v1.1 (2026-04-20): Fixed priority order — status_v2 checked before
operational_status to prevent false "draft" on posted bills with
desynced operational_status column.
"""


def derive_doc_status(row: dict) -> str:
    """
    Derive doc_status from row data. Single source of truth.

    Priority order (CRITICAL — do not reorder):
    1. Computed status from CASE expression (most reliable when present)
    2. status_v2 (bills-specific lifecycle — authoritative for posted state)
    3. accounting_status (cross-check)
    4. operational_status (legacy fallback — LEAST reliable)

    Returns: "draft" | "posted" | "void"
    """
    # Check computed status first (from CASE expression in SELECT)
    status = (row.get("status") or "").lower()
    if status in ("void", "voided"):
        return "void"
    if status == "draft":
        return "draft"

    # Check status_v2 — authoritative for bills lifecycle
    # MUST be checked BEFORE operational_status (which can be desynced)
    status_v2 = (row.get("status_v2") or "").lower()
    if status_v2 in ("void", "voided"):
        return "void"
    if status_v2 == "draft":
        return "draft"
    if status_v2 == "posted":
        return "posted"

    # Check accounting_status
    acc_status = (row.get("accounting_status") or "").upper()
    if acc_status == "POSTED":
        return "posted"

    # Check operational_status (legacy — least reliable)
    op_status = (row.get("operational_status") or "").upper()
    if op_status == "VOID":
        return "void"
    if op_status == "DRAFT":
        return "draft"
    if op_status in ("POSTED", "COMPLETED", "RECEIVED"):
        return "posted"

    # If status is a known posted-like value
    if status in ("posted", "paid", "partial", "overdue", "unpaid", "applied"):
        return "posted"

    # Default: safer to assume draft than posted
    return "draft"


# Filter constant — consistent with compute_ap/ar_outstanding() DB functions
EXCLUDE_DRAFT_VOID = "NOT IN (draft, void)"
