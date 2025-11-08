"""
Test Public Endpoints (No Auth Required)
Tests: Health checks and public routes
"""
import pytest
from httpx import AsyncClient


@pytest.mark.integration
@pytest.mark.asyncio
async def test_health_endpoint(async_client: AsyncClient):
    """Test health endpoint is accessible"""
    response = await async_client.get("/healthz")
    
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "phase" in data


@pytest.mark.integration
@pytest.mark.asyncio
async def test_customer_endpoint_public(async_client: AsyncClient):
    """Test customer chat endpoint is public (no auth)"""
    response = await async_client.post(
        "/bca/chat",
        json={"message": "test query", "session_id": "test-session"}
    )
    
    # Should not require auth (200 or 404 if not implemented)
    assert response.status_code in [200, 404]
