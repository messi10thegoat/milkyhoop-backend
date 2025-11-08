"""
Test Session Management Endpoints
Tests: /api/session/* endpoints
"""
import pytest
from httpx import AsyncClient


@pytest.mark.integration
@pytest.mark.asyncio
async def test_list_sessions_authenticated(async_client: AsyncClient):
    """Test listing sessions for authenticated user"""
    import time
    user_data = {
        "email": f"session_test_{int(time.time())}@test.com",
        "password": "TestPass123!",
        "username": f"session_{int(time.time())}",
        "name": "Session Test"
    }
    
    reg_response = await async_client.post("/api/auth/register", json=user_data)
    token = reg_response.json()["data"]["access_token"]
    
    headers = {"Authorization": f"Bearer {token}"}
    response = await async_client.get("/api/session/list", headers=headers)
    
    assert response.status_code == 200
    data = response.json()
    assert "sessions" in data or "success" in data


@pytest.mark.integration
@pytest.mark.asyncio
async def test_session_created_on_registration(async_client: AsyncClient):
    """Test that session is created during registration"""
    import time
    user_data = {
        "email": f"newsession_{int(time.time())}@test.com",
        "password": "TestPass123!",
        "username": f"newsession_{int(time.time())}",
        "name": "New Session"
    }
    
    reg_response = await async_client.post("/api/auth/register", json=user_data)
    assert reg_response.status_code == 200
    
    data = reg_response.json()["data"]
    # Refresh token indicates session was created
    assert "refresh_token" in data
