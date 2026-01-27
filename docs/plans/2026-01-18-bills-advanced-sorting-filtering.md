# Bills Advanced Sorting & Filtering Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add compound sorting and system filter presets to the bills (Faktur Pembelian) module.

**Architecture:** Extend existing bills endpoint to support multi-field sorting via comma-separated `sort` parameter. Add hardcoded system presets that frontend can reference. Keep backward compatibility with existing `sort_by`/`sort_order` params.

**Tech Stack:** FastAPI, asyncpg, PostgreSQL, Pydantic

**Scope:** Phase 1 only (High Impact, Low Effort items)
- ✅ Full Compound Sort
- ✅ System Presets (read-only)
- ❌ User Saved Presets (Phase 2)
- ❌ Full-text Search (Phase 2)
- ❌ Smart Defaults (Phase 3)

---

## Task 1: Add Sort Parameter Parser Utility

**Files:**
- Create: `backend/api_gateway/app/utils/sorting.py`

**Step 1: Create the sorting utility module**

```python
"""
Sorting utilities for API endpoints.

Provides compound sort parsing and SQL generation.
"""

from typing import List, Tuple, Optional
import re


def parse_sort_param(sort: str) -> List[Tuple[str, str]]:
    """
    Parse comma-separated sort string into list of (field, order) tuples.

    Args:
        sort: Comma-separated string like "status:asc,amount:desc,updated_at:desc"

    Returns:
        List of tuples: [("status", "asc"), ("amount", "desc"), ("updated_at", "desc")]

    Examples:
        >>> parse_sort_param("status:asc,amount:desc")
        [("status", "asc"), ("amount", "desc")]

        >>> parse_sort_param("created_at:desc")
        [("created_at", "desc")]

        >>> parse_sort_param("")
        [("created_at", "desc")]  # Default
    """
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

            # Validate order
            if order not in ("asc", "desc"):
                order = "desc"
        else:
            # No order specified, default to desc
            field = part.strip().lower()
            order = "desc"

        # Basic field name validation (alphanumeric and underscore only)
        if re.match(r'^[a-z_][a-z0-9_]*$', field):
            result.append((field, order))

    # Fallback if nothing valid parsed
    if not result:
        return [("created_at", "desc")]

    return result


def build_order_by_clause(
    sort_fields: List[Tuple[str, str]],
    field_mapping: dict,
    default_field: str = "created_at"
) -> str:
    """
    Build SQL ORDER BY clause from parsed sort fields.

    Args:
        sort_fields: List of (field, order) tuples from parse_sort_param()
        field_mapping: Dict mapping API field names to SQL expressions
        default_field: Fallback field if no valid fields found

    Returns:
        SQL ORDER BY clause without "ORDER BY" prefix

    Example:
        >>> field_mapping = {
        ...     "created_at": "created_at",
        ...     "status": "CASE WHEN status = 'overdue' THEN 1 ... END",
        ...     "amount": "COALESCE(grand_total, amount)"
        ... }
        >>> build_order_by_clause([("status", "asc"), ("amount", "desc")], field_mapping)
        "CASE WHEN status = 'overdue' THEN 1 ... END ASC NULLS LAST, COALESCE(grand_total, amount) DESC NULLS LAST"
    """
    clauses = []

    for field, order in sort_fields:
        sql_expr = field_mapping.get(field)
        if sql_expr is None:
            continue

        direction = "DESC" if order == "desc" else "ASC"
        null_pos = "NULLS LAST" if direction == "ASC" else "NULLS FIRST"

        clauses.append(f"{sql_expr} {direction} {null_pos}")

    if not clauses:
        # Fallback to default
        default_expr = field_mapping.get(default_field, default_field)
        return f"{default_expr} DESC NULLS FIRST"

    return ", ".join(clauses)
```

**Step 2: Create __init__.py for utils if needed**

```bash
mkdir -p /root/milkyhoop-dev/backend/api_gateway/app/utils
touch /root/milkyhoop-dev/backend/api_gateway/app/utils/__init__.py
```

**Step 3: Commit**

```bash
git add backend/api_gateway/app/utils/
git commit -m "feat(bills): add sorting utility for compound sort parsing"
```

---

## Task 2: Update Bills Router with Compound Sort Parameter

**Files:**
- Modify: `backend/api_gateway/app/routers/bills.py:97-150`

**Step 1: Add import and update list_bills endpoint**

Add to imports at top of file:
```python
from ..utils.sorting import parse_sort_param
```

**Step 2: Update the list_bills function signature**

Replace the existing sort parameters:
```python
# OLD (lines 106-113):
sort_by: Literal[
    "created_at", "date", "number", "supplier", "due_date",
    "amount", "balance", "updated_at", "vendor_name", "invoice_number"
] = Query(
    "created_at",
    description="Sort field: created_at, date (issue_date), number (invoice_number), "
                "supplier (vendor_name), due_date, amount, balance (amount_due), updated_at"
),
sort_order: Literal["asc", "desc"] = Query("desc", description="Sort order"),
```

With:
```python
sort: str = Query(
    default="created_at:desc",
    description="Comma-separated sort fields. Format: field:order,field:order. "
                "Fields: created_at, date, number, supplier, due_date, amount, "
                "balance, status, updated_at. Example: status:asc,amount:desc"
),
# Keep legacy params for backward compatibility
sort_by: Optional[str] = Query(None, description="[DEPRECATED] Use 'sort' param instead"),
sort_order: Optional[str] = Query(None, description="[DEPRECATED] Use 'sort' param instead"),
```

**Step 3: Update the service call**

Replace in the try block:
```python
# Parse sort parameter (with legacy fallback)
if sort_by and not sort.startswith(sort_by):
    # Legacy mode: convert old params to new format
    legacy_order = sort_order or "desc"
    sort_fields = [(sort_by, legacy_order)]
else:
    sort_fields = parse_sort_param(sort)

result = await service.list_bills(
    tenant_id=ctx["tenant_id"],
    skip=skip,
    limit=limit,
    status=status,
    search=search,
    sort_fields=sort_fields,  # Changed from sort_by/sort_order
    due_date_from=due_date_from,
    due_date_to=due_date_to,
    vendor_id=vendor_id
)
```

**Step 4: Commit**

```bash
git add backend/api_gateway/app/routers/bills.py
git commit -m "feat(bills): add compound sort parameter to list endpoint"
```

---

## Task 3: Update Bills Service for Compound Sort

**Files:**
- Modify: `backend/api_gateway/app/services/bills_service.py:166-290`

**Step 1: Update list_bills method signature**

Change:
```python
# OLD:
async def list_bills(
    self,
    tenant_id: str,
    skip: int = 0,
    limit: int = 20,
    status: str = "all",
    search: Optional[str] = None,
    sort_by: str = "created_at",
    sort_order: str = "desc",
    ...
):
```

To:
```python
async def list_bills(
    self,
    tenant_id: str,
    skip: int = 0,
    limit: int = 20,
    status: str = "all",
    search: Optional[str] = None,
    sort_fields: List[Tuple[str, str]] = None,  # Changed
    due_date_from: Optional[date] = None,
    due_date_to: Optional[date] = None,
    vendor_id: Optional[UUID] = None,
):
    # Default sort if not provided
    if sort_fields is None:
        sort_fields = [("created_at", "desc")]
```

**Step 2: Add import at top of file**

```python
from typing import Optional, List, Tuple
from ..utils.sorting import build_order_by_clause
```

**Step 3: Update the sort field mapping and ORDER BY generation**

Replace the existing sort_by validation block (around line 230-252):
```python
# Build compound ORDER BY clause
# Maps API field names to SQL expressions
field_mapping = {
    "created_at": "created_at",
    "date": "issue_date",
    "number": "invoice_number",
    "supplier": "vendor_name",
    "due_date": "due_date",
    "amount": "COALESCE(grand_total, amount)",
    "balance": "(COALESCE(amount, 0) - COALESCE(amount_paid, 0))",
    "updated_at": "updated_at",
    # Status ordering: overdue(1) > unpaid(2) > partial(3) > paid(4) > draft(5) > void(6)
    "status": """CASE
        WHEN status = 'void' THEN 6
        WHEN amount_paid >= amount THEN 4
        WHEN amount_paid > 0 AND due_date < CURRENT_DATE THEN 1
        WHEN amount_paid > 0 THEN 3
        WHEN due_date < CURRENT_DATE THEN 1
        ELSE 2
    END""",
    # Legacy aliases for backward compatibility
    "vendor_name": "vendor_name",
    "invoice_number": "invoice_number",
}

order_by_clause = build_order_by_clause(sort_fields, field_mapping)
```

**Step 4: Update the query to use new ORDER BY**

Change:
```python
ORDER BY {sort_field} {sort_dir}{null_handling}
```

To:
```python
ORDER BY {order_by_clause}
```

**Step 5: Commit**

```bash
git add backend/api_gateway/app/services/bills_service.py
git commit -m "feat(bills): implement compound sort in service layer"
```

---

## Task 4: Add System Presets Endpoint

**Files:**
- Modify: `backend/api_gateway/app/routers/bills.py` (add new endpoint)

**Step 1: Define system presets constant**

Add after imports, before router definition:
```python
# System filter presets (read-only, available to all users)
BILLS_SYSTEM_PRESETS = [
    {
        "id": "system:urgent",
        "name": "Jatuh Tempo Terdekat",
        "description": "Tagihan yang mendekati atau sudah lewat jatuh tempo",
        "config": {
            "sort": "due_date:asc,balance:desc",
            "filters": {"status": ["unpaid", "partial", "overdue"]}
        },
        "is_system": True,
        "icon": "clock"
    },
    {
        "id": "system:recently-paid",
        "name": "Terakhir Dibayar",
        "description": "Tagihan yang baru saja dibayar",
        "config": {
            "sort": "updated_at:desc",
            "filters": {"status": ["paid", "partial"]}
        },
        "is_system": True,
        "icon": "check-circle"
    },
    {
        "id": "system:largest-outstanding",
        "name": "Tagihan Terbesar",
        "description": "Tagihan dengan saldo terbesar",
        "config": {
            "sort": "balance:desc",
            "filters": {"status": ["unpaid", "partial", "overdue"]}
        },
        "is_system": True,
        "icon": "trending-up"
    },
    {
        "id": "system:newest",
        "name": "Terbaru",
        "description": "Tagihan terbaru berdasarkan tanggal dibuat",
        "config": {
            "sort": "created_at:desc",
            "filters": {}
        },
        "is_system": True,
        "icon": "plus-circle"
    },
    {
        "id": "system:by-supplier",
        "name": "Per Supplier",
        "description": "Diurutkan berdasarkan nama supplier",
        "config": {
            "sort": "supplier:asc,due_date:asc",
            "filters": {"status": ["unpaid", "partial", "overdue"]}
        },
        "is_system": True,
        "icon": "users"
    },
]
```

**Step 2: Add presets endpoint**

Add new endpoint after the `/summary` endpoint:
```python
# =============================================================================
# FILTER PRESETS
# =============================================================================
@router.get("/presets", response_model=dict)
async def get_filter_presets(request: Request):
    """
    Get available filter presets for bills.

    Returns system presets that are available to all users.
    User-specific presets will be added in a future release.

    **Usage:**
    1. Fetch presets on page load
    2. Display as quick-filter buttons/chips
    3. When user clicks a preset, apply its `config.sort` and `config.filters`
    """
    try:
        get_user_context(request)  # Validate auth

        return {
            "success": True,
            "data": {
                "system_presets": BILLS_SYSTEM_PRESETS,
                "user_presets": [],  # TODO: Phase 2 - saved user presets
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting presets: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get presets")
```

**Step 3: Commit**

```bash
git add backend/api_gateway/app/routers/bills.py
git commit -m "feat(bills): add system filter presets endpoint"
```

---

## Task 5: Add Preset Application Endpoint (Optional Enhancement)

**Files:**
- Modify: `backend/api_gateway/app/routers/bills.py`

**Step 1: Add endpoint to fetch bills using a preset**

```python
@router.get("/presets/{preset_id}/apply", response_model=BillListResponse)
async def apply_preset(
    request: Request,
    preset_id: str,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    search: Optional[str] = Query(None),
):
    """
    Fetch bills using a preset's configuration.

    This is a convenience endpoint that applies a preset's sort and filters.
    Equivalent to calling GET /api/bills with the preset's config.
    """
    try:
        ctx = get_user_context(request)

        # Find preset
        preset = None
        for p in BILLS_SYSTEM_PRESETS:
            if p["id"] == preset_id:
                preset = p
                break

        if not preset:
            raise HTTPException(status_code=404, detail=f"Preset '{preset_id}' not found")

        config = preset["config"]
        sort_fields = parse_sort_param(config.get("sort", "created_at:desc"))
        filters = config.get("filters", {})

        # Map preset filters to service params
        status_filter = "all"
        if filters.get("status"):
            # If multiple statuses, we need custom handling
            # For now, use first status or 'all'
            statuses = filters["status"]
            if len(statuses) == 1:
                status_filter = statuses[0]
            # TODO: Support multiple status filter in service

        service = await get_bills_service()
        result = await service.list_bills(
            tenant_id=ctx["tenant_id"],
            skip=skip,
            limit=limit,
            status=status_filter,
            search=search,
            sort_fields=sort_fields,
        )

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error applying preset {preset_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to apply preset")
```

**Step 2: Commit**

```bash
git add backend/api_gateway/app/routers/bills.py
git commit -m "feat(bills): add preset application endpoint"
```

---

## Task 6: Rebuild and Test

**Step 1: Rebuild API gateway**

```bash
docker compose build api_gateway
docker compose up -d api_gateway
```

**Step 2: Wait for healthy status**

```bash
sleep 10
curl -s http://localhost:8001/healthz
```

Expected: `{"status":"healthy",...}`

**Step 3: Test compound sort**

```bash
# Test single sort (backward compatible)
curl -s "http://localhost:8001/api/bills?sort=created_at:desc&limit=3" \
  -H "Authorization: Bearer $TOKEN" | jq '.items[:2] | .[].invoice_number'

# Test compound sort
curl -s "http://localhost:8001/api/bills?sort=status:asc,amount:desc&limit=5" \
  -H "Authorization: Bearer $TOKEN" | jq '.items | .[].status'

# Test presets endpoint
curl -s "http://localhost:8001/api/bills/presets" \
  -H "Authorization: Bearer $TOKEN" | jq '.data.system_presets | .[].name'
```

**Step 4: Final commit**

```bash
git add -A
git commit -m "feat(bills): complete compound sort and system presets implementation"
```

---

## Summary of Changes

| File | Change |
|------|--------|
| `backend/api_gateway/app/utils/sorting.py` | NEW - Sort parsing utilities |
| `backend/api_gateway/app/routers/bills.py` | Updated - Add `sort` param, presets endpoint |
| `backend/api_gateway/app/services/bills_service.py` | Updated - Compound sort support |

## API Changes

### Modified Endpoints

**GET /api/bills**
- New param: `sort` (string) - Comma-separated sort fields, e.g., `status:asc,amount:desc`
- Deprecated: `sort_by`, `sort_order` (still work for backward compatibility)

### New Endpoints

**GET /api/bills/presets**
- Returns system filter presets
- Response: `{ system_presets: [...], user_presets: [] }`

**GET /api/bills/presets/{preset_id}/apply**
- Apply a preset and return filtered bills
- Params: `skip`, `limit`, `search`

## Future Work (Phase 2+)

- [ ] User-saved presets (database table + CRUD)
- [ ] Full-text search with relevance ranking
- [ ] Smart defaults based on usage patterns
- [ ] Apply presets to other modules (invoices, expenses)
