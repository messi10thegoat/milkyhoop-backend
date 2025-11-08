"""
Test JWT Token Validation
Tests: Token lifecycle and validation
"""
import pytest
from httpx import AsyncClient


@pytest.mark.integration
@pytest.mark.asyncio
async def test_token_contains_user_info(async_client: AsyncClient):
    """Test JWT token contains correct user information"""
    import time
    user_data = {
        "email": f"token_test_{int(time.time())}@test.com",
        "password": "TestPass123!",
        "username": f"token_{int(time.time())}",
        "name": "Token Test"
    }
    
    response = await async_client.post("/api/auth/register", json=user_data)
    assert response.status_code == 200
    
    data = response.json()["data"]
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["email"] == user_data["email"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_token_in_different_formats(async_client: AsyncClient):
    """Test token validation with different header formats"""
    import time
    user_data = {
        "email": f"format_test_{int(time.time())}@test.com",
        "password": "TestPass123!",
        "username": f"format_{int(time.time())}",
        "name": "Format Test"
    }
    
    reg_response = await async_client.post("/api/auth/register", json=user_data)
    token = reg_response.json()["data"]["access_token"]
    
    # Test with Bearer prefix
    headers1 = {"Authorization": f"Bearer {token}"}
    response1 = await async_client.get("/api/session/list", headers=headers1)
    assert response1.status_code == 200
    
    # Test without Bearer prefix (should fail)
    headers2 = {"Authorization": token}
    response2 = await async_client.get("/api/session/list", headers=headers2)
    assert response2.status_code == 401


@pytest.mark.integration
@pytest.mark.asyncio
async def test_malformed_token_rejected(async_client: AsyncClient):
    """Test malformed tokens are rejected"""
    malformed_tokens = [
        "not.a.token",
        "Bearer ",
        "Bearer",
        "",
        "malformed"
    ]
    
    for token in malformed_tokens:
        headers = {"Authorization": f"Bearer {token}"}
        response = await async_client.get("/api/session/list", headers=headers)
        assert response.status_code == 401
