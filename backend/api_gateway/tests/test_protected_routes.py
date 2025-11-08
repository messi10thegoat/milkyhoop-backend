"""
Test Protected Routes Authentication
Tests: Middleware and auth dependencies
"""
import pytest
from httpx import AsyncClient


@pytest.mark.integration
@pytest.mark.asyncio
async def test_protected_route_no_token(async_client: AsyncClient):
    """Test protected route blocks access without token"""
    response = await async_client.get("/api/session/list")
    
    assert response.status_code == 401
    data = response.json()
    assert "error" in data or "detail" in data


@pytest.mark.integration
@pytest.mark.asyncio
async def test_protected_route_invalid_token(async_client: AsyncClient, invalid_token: str):
    """Test protected route blocks access with invalid token"""
    headers = {"Authorization": f"Bearer {invalid_token}"}
    response = await async_client.get("/api/session/list", headers=headers)
    
    assert response.status_code == 401


@pytest.mark.integration
@pytest.mark.asyncio
async def test_protected_route_valid_token(async_client: AsyncClient):
    """Test protected route allows access with valid token"""
    # First register a user to get valid token
    import time
    user_data = {
        "email": f"protected_test_{int(time.time())}@test.com",
        "password": "TestPass123!",
        "username": f"protected_{int(time.time())}",
        "name": "Protected Test"
    }
    
    reg_response = await async_client.post("/api/auth/register", json=user_data)
    assert reg_response.status_code == 200
    
    token = reg_response.json()["data"]["access_token"]
    
    # Now try to access protected route
    headers = {"Authorization": f"Bearer {token}"}
    response = await async_client.get("/api/session/list", headers=headers)
    
    assert response.status_code == 200
    data = response.json()
    assert "sessions" in data or "success" in data


@pytest.mark.integration
@pytest.mark.asyncio
async def test_public_route_no_auth_required(async_client: AsyncClient):
    """Test public route accessible without token"""
    response = await async_client.get("/healthz")
    
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
