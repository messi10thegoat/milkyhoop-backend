"""Tests for item activity log endpoint."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
from datetime import datetime, timezone

from fastapi import HTTPException


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
                "details": "Harga jual: Rp 100.000 \u2192 Rp 120.000",
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
        assert result.activities[0].description == "Item dibuat"
        assert result.activities[1].type == "updated"
        assert result.activities[1].details == "Harga jual: Rp 100.000 \u2192 Rp 120.000"

    @pytest.mark.asyncio
    async def test_activity_item_not_found(self, mock_request):
        """Activity endpoint returns 404 for non-existent item."""
        from app.routers.items import get_item_activity

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
