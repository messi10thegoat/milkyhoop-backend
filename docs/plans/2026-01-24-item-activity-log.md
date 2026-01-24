# Item Activity Log Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add an audit trail / activity log for items, recording every mutation (create, update, price change, status change, stock adjustment) and exposing it via `GET /api/items/:id/activity`.

**Architecture:** New `item_activities` table with denormalized actor_name. Activity logging is done inline within the existing create/update endpoints in the items router (no separate service class needed since items logic is already inline). A new GET endpoint reads from this table with pagination.

**Tech Stack:** PostgreSQL (asyncpg), FastAPI, Pydantic schemas

---

### Task 1: Database Migration

**Files:**
- Create: `backend/migrations/V080__item_activities.sql`

**Step 1: Write the migration**

```sql
-- V080__item_activities.sql
-- Item Activity Log: audit trail for item mutations

CREATE TABLE IF NOT EXISTS item_activities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    item_id UUID NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    tenant_id TEXT NOT NULL,
    type VARCHAR(20) NOT NULL,
    description VARCHAR(255) NOT NULL,
    details TEXT,
    actor_id UUID,
    actor_name VARCHAR(255),
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata JSONB DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_item_activities_item_id ON item_activities(item_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_item_activities_tenant ON item_activities(tenant_id, item_id);

-- Backfill existing items with 'created' activity
INSERT INTO item_activities (item_id, tenant_id, type, description, timestamp)
SELECT id, tenant_id, 'created', 'Item dibuat', COALESCE(created_at, NOW())
FROM products
WHERE id NOT IN (SELECT DISTINCT item_id FROM item_activities);
```

**Step 2: Verify migration file exists**

Run: `ls backend/migrations/V080__item_activities.sql`
Expected: File listed

**Step 3: Commit**

```bash
git add backend/migrations/V080__item_activities.sql
git commit -m "feat(items): add item_activities table migration V080"
```

---

### Task 2: Pydantic Schemas for Activity Response

**Files:**
- Modify: `backend/api_gateway/app/schemas/items.py` (add at end)

**Step 1: Add activity schemas**

Add these models to the end of `items.py`:

```python
class ItemActivity(BaseModel):
    id: str
    type: str
    description: str
    actor_name: Optional[str] = None
    timestamp: str
    details: Optional[str] = None

class ItemActivityResponse(BaseModel):
    success: bool
    activities: List[ItemActivity]
    total: int
    has_more: bool
```

**Step 2: Add import to router imports**

In `items.py` router, add `ItemActivity, ItemActivityResponse` to the import from schemas.

**Step 3: Commit**

```bash
git add backend/api_gateway/app/schemas/items.py
git commit -m "feat(items): add ItemActivity and ItemActivityResponse schemas"
```

---

### Task 3: GET Activity Endpoint

**Files:**
- Modify: `backend/api_gateway/app/routers/items.py` (add endpoint section)

**Step 1: Add the activity endpoint**

Add before the `# GET ITEM DETAIL` section (so path doesn't conflict with `{item_id}` catch-all). Insert after the CATEGORIES section, before GET ITEM DETAIL:

```python
# =============================================================================
# ACTIVITY LOG
# =============================================================================

@router.get("/items/{item_id}/activity", response_model=ItemActivityResponse)
async def get_item_activity(
    request: Request,
    item_id: UUID,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0)
):
    """Get activity log / audit trail for an item."""
    tenant_id = get_tenant_id(request)
    conn = None

    try:
        conn = await get_db_connection()

        # Verify item exists
        item_exists = await conn.fetchval(
            "SELECT id FROM products WHERE id = $1 AND tenant_id = $2",
            str(item_id), tenant_id
        )
        if not item_exists:
            raise HTTPException(status_code=404, detail="Item not found")

        # Get total count
        total = await conn.fetchval(
            "SELECT COUNT(*) FROM item_activities WHERE item_id = $1 AND tenant_id = $2",
            str(item_id), tenant_id
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
                id=str(row['id']),
                type=row['type'],
                description=row['description'],
                details=row.get('details'),
                actor_name=row.get('actor_name'),
                timestamp=row['timestamp'].isoformat() if row['timestamp'] else None
            )
            for row in rows
        ]

        return ItemActivityResponse(
            success=True,
            activities=activities,
            total=total or 0,
            has_more=(offset + limit) < (total or 0)
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting item activity: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn:
            await conn.close()
```

**Step 2: Update imports at top of router**

Add `ItemActivity, ItemActivityResponse` to the import line from `..schemas.items`.

**Step 3: Commit**

```bash
git add backend/api_gateway/app/routers/items.py
git commit -m "feat(items): add GET /items/{item_id}/activity endpoint"
```

---

### Task 4: Log Activity on Item Creation

**Files:**
- Modify: `backend/api_gateway/app/routers/items.py` (modify `create_item`)

**Step 1: Add activity insert inside the create_item transaction**

Inside `create_item`, after the stock entry creation (line ~354), still within the `async with conn.transaction():` block, add:

```python
            # Log activity
            user_id = request.state.user.get("user_id")
            user_name = request.state.user.get("username") or request.state.user.get("email")
            await conn.execute(
                """
                INSERT INTO item_activities (item_id, tenant_id, type, description, actor_id, actor_name)
                VALUES ($1, $2, 'created', 'Item dibuat', $3, $4)
                """,
                item_id, tenant_id,
                user_id if user_id else None,
                user_name
            )
```

**Step 2: Commit**

```bash
git add backend/api_gateway/app/routers/items.py
git commit -m "feat(items): log activity on item creation"
```

---

### Task 5: Log Activity on Item Update

**Files:**
- Modify: `backend/api_gateway/app/routers/items.py` (modify `update_item`)

**Step 1: Fetch old values before update**

In `update_item`, after the duplicate name/barcode checks and before `async with conn.transaction():`, add:

```python
        # Fetch old values for change tracking
        old_item = await conn.fetchrow(
            """SELECT nama_produk, sales_price, harga_jual, purchase_price,
                      base_unit, satuan, reorder_level, item_type, track_inventory,
                      kategori, deskripsi, barcode, is_returnable,
                      sales_tax, purchase_tax, image_url
               FROM products WHERE id = $1 AND tenant_id = $2""",
            str(item_id), tenant_id
        )
```

**Step 2: Add activity logging after the update, inside the transaction**

After the conversions update block (line ~516), still inside `async with conn.transaction():`, add:

```python
            # Log activity
            body_dict_for_log = body.model_dump(exclude_unset=True, exclude={'conversions'})
            if body_dict_for_log:
                change_parts = []
                field_labels = {
                    'name': ('Nama', 'nama_produk'),
                    'sales_price': ('Harga jual', 'sales_price', True),
                    'purchase_price': ('Harga beli', 'purchase_price', True),
                    'base_unit': ('Satuan', 'base_unit'),
                    'reorder_level': ('Titik reorder', 'reorder_level'),
                    'kategori': ('Kategori', 'kategori'),
                    'deskripsi': ('Deskripsi', 'deskripsi'),
                    'barcode': ('Barcode', 'barcode'),
                    'sales_tax': ('Pajak jual', 'sales_tax'),
                    'purchase_tax': ('Pajak beli', 'purchase_tax'),
                }

                only_price = True
                only_status = True

                for field, meta in field_labels.items():
                    if field in body_dict_for_log:
                        label = meta[0]
                        db_col = meta[1]
                        is_price = len(meta) > 2 and meta[2]
                        old_val = old_item.get(db_col) if old_item else None
                        new_val = body_dict_for_log[field]

                        if old_val != new_val:
                            if is_price:
                                old_display = f"Rp {int(old_val):,}".replace(",", ".") if old_val else "0"
                                new_display = f"Rp {int(new_val):,}".replace(",", ".") if new_val else "0"
                            else:
                                old_display = str(old_val) if old_val else "-"
                                new_display = str(new_val) if new_val else "-"
                            change_parts.append(f"{label}: {old_display} → {new_display}")
                            if field not in ('sales_price', 'purchase_price'):
                                only_price = False
                            if field != 'status':
                                only_status = False

                # Determine activity type
                if only_price and any(f in body_dict_for_log for f in ('sales_price', 'purchase_price')):
                    activity_type = 'price_changed'
                    activity_desc = 'Harga diubah'
                else:
                    activity_type = 'updated'
                    activity_desc = 'Item diperbarui'
                    only_status = False

                details = ", ".join(change_parts) if change_parts else None
                user_id = request.state.user.get("user_id")
                user_name = request.state.user.get("username") or request.state.user.get("email")

                await conn.execute(
                    """
                    INSERT INTO item_activities (item_id, tenant_id, type, description, details, actor_id, actor_name)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    """,
                    str(item_id), tenant_id, activity_type, activity_desc, details,
                    user_id if user_id else None,
                    user_name
                )
```

**Step 3: Commit**

```bash
git add backend/api_gateway/app/routers/items.py
git commit -m "feat(items): log activity on item update with change details"
```

---

### Task 6: Test the Endpoint

**Files:**
- Create: `backend/api_gateway/tests/test_item_activity.py`

**Step 1: Write the test file**

```python
"""Tests for item activity log endpoint."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
from datetime import datetime, timezone

from fastapi.testclient import TestClient


@pytest.fixture
def mock_request():
    request = MagicMock()
    request.state.user = {
        "tenant_id": "test-tenant",
        "user_id": str(uuid4()),
        "username": "Test User",
        "email": "test@example.com"
    }
    return request


class TestItemActivityEndpoint:
    """Test GET /items/{item_id}/activity"""

    @pytest.mark.asyncio
    async def test_activity_returns_list(self, mock_request):
        """Activity endpoint returns paginated list of activities."""
        from app.routers.items import get_item_activity

        item_id = uuid4()
        now = datetime.now(timezone.utc)

        mock_conn = AsyncMock()
        mock_conn.fetchval = AsyncMock(side_effect=[item_id, 2])
        mock_conn.fetch = AsyncMock(return_value=[
            {
                "id": uuid4(),
                "type": "created",
                "description": "Item dibuat",
                "details": None,
                "actor_name": "Test User",
                "timestamp": now
            },
            {
                "id": uuid4(),
                "type": "updated",
                "description": "Item diperbarui",
                "details": "Harga jual: Rp 100.000 → Rp 120.000",
                "actor_name": "Test User",
                "timestamp": now
            }
        ])
        mock_conn.close = AsyncMock()

        with patch("app.routers.items.get_db_connection", return_value=mock_conn):
            result = await get_item_activity(mock_request, item_id, limit=50, offset=0)

        assert result.success is True
        assert len(result.activities) == 2
        assert result.total == 2
        assert result.has_more is False
        assert result.activities[0].type == "created"
        assert result.activities[1].details == "Harga jual: Rp 100.000 → Rp 120.000"

    @pytest.mark.asyncio
    async def test_activity_item_not_found(self, mock_request):
        """Activity endpoint returns 404 for non-existent item."""
        from app.routers.items import get_item_activity
        from fastapi import HTTPException

        item_id = uuid4()

        mock_conn = AsyncMock()
        mock_conn.fetchval = AsyncMock(return_value=None)
        mock_conn.close = AsyncMock()

        with patch("app.routers.items.get_db_connection", return_value=mock_conn):
            with pytest.raises(HTTPException) as exc_info:
                await get_item_activity(mock_request, item_id, limit=50, offset=0)
            assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_activity_pagination(self, mock_request):
        """Activity endpoint respects limit/offset and has_more."""
        from app.routers.items import get_item_activity

        item_id = uuid4()
        now = datetime.now(timezone.utc)

        mock_conn = AsyncMock()
        mock_conn.fetchval = AsyncMock(side_effect=[item_id, 5])  # 5 total
        mock_conn.fetch = AsyncMock(return_value=[
            {"id": uuid4(), "type": "updated", "description": "Item diperbarui",
             "details": None, "actor_name": "User", "timestamp": now}
        ])
        mock_conn.close = AsyncMock()

        with patch("app.routers.items.get_db_connection", return_value=mock_conn):
            result = await get_item_activity(mock_request, item_id, limit=2, offset=0)

        assert result.total == 5
        assert result.has_more is True
```

**Step 2: Run tests**

Run: `cd /root/milkyhoop-dev && python -m pytest backend/api_gateway/tests/test_item_activity.py -v`
Expected: All 3 tests PASS

**Step 3: Commit**

```bash
git add backend/api_gateway/tests/test_item_activity.py
git commit -m "test(items): add unit tests for item activity endpoint"
```

---

### Task 7: Verify Full Integration

**Step 1: Check router loads without import errors**

Run: `cd /root/milkyhoop-dev && python -c "from backend.api_gateway.app.routers.items import router; print('OK')"` or alternatively `cd /root/milkyhoop-dev/backend/api_gateway && python -c "from app.routers.items import router; print('OK')"`
Expected: `OK`

**Step 2: Verify migration SQL syntax**

Run: `cd /root/milkyhoop-dev && python -c "open('backend/migrations/V080__item_activities.sql').read(); print('Migration file readable')"`
Expected: `Migration file readable`

**Step 3: Final commit (if any fixups needed)**

---

## Summary of Changes

| File | Action | Purpose |
|------|--------|---------|
| `backend/migrations/V080__item_activities.sql` | Create | Table + indexes + backfill |
| `backend/api_gateway/app/schemas/items.py` | Modify | Add `ItemActivity`, `ItemActivityResponse` |
| `backend/api_gateway/app/routers/items.py` | Modify | Add GET endpoint, logging in create/update |
| `backend/api_gateway/tests/test_item_activity.py` | Create | Unit tests |
