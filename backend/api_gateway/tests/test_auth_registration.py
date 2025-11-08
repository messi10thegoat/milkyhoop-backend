"""
Test User Registration Endpoint
Tests: /api/auth/register
"""
import pytest
from httpx import AsyncClient


@pytest.mark.integration
@pytest.mark.asyncio
async def test_register_valid_user(async_client: AsyncClient, test_user_data):
    """Test successful user registration with valid data"""
    # Generate unique email to avoid conflicts
    import time
    test_user_data["email"] = f"test_{int(time.time())}@test.com"
    test_user_data["username"] = f"test_{int(time.time())}"
    
    response = await async_client.post("/api/auth/register", json=test_user_data)
    
    assert response.status_code == 200
    data = response.json()
    
    assert data["success"] is True
    assert "data" in data
    assert "access_token" in data["data"]
    assert "refresh_token" in data["data"]
    assert "user_id" in data["data"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_register_invalid_password(async_client: AsyncClient, invalid_password_data):
    """Test registration fails with invalid password (no special char)"""
    import time
    invalid_password_data["email"] = f"invalid_{int(time.time())}@test.com"
    invalid_password_data["username"] = f"invalid_{int(time.time())}"
    
    response = await async_client.post("/api/auth/register", json=invalid_password_data)
    
    # Should fail due to password validation
    assert response.status_code in [400, 422, 500]  # Accept various error codes


@pytest.mark.integration
@pytest.mark.asyncio
async def test_register_weak_password(async_client: AsyncClient, weak_password_data):
    """Test registration fails with weak password (too short)"""
    import time
    weak_password_data["email"] = f"weak_{int(time.time())}@test.com"
    weak_password_data["username"] = f"weak_{int(time.time())}"
    
    response = await async_client.post("/api/auth/register", json=weak_password_data)
    
    # Should fail due to password length
    assert response.status_code in [400, 422, 500]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_register_missing_fields(async_client: AsyncClient):
    """Test registration fails with missing required fields"""
    incomplete_data = {
        "email": "incomplete@test.com"
        # Missing password, username, name
    }
    
    response = await async_client.post("/api/auth/register", json=incomplete_data)
    
    assert response.status_code == 422  # Unprocessable Entity


@pytest.mark.integration
@pytest.mark.asyncio
async def test_register_duplicate_email(async_client: AsyncClient):
    """Test registration fails when email already exists"""
    import time
    unique_data = {
        "email": f"duplicate_{int(time.time())}@test.com",
        "password": "TestPass123!",
        "username": f"duplicate_{int(time.time())}",
        "name": "Duplicate Test"
    }
    
    # First registration should succeed
    response1 = await async_client.post("/api/auth/register", json=unique_data)
    assert response1.status_code == 200
    
    # Second registration with same email should fail
    response2 = await async_client.post("/api/auth/register", json=unique_data)
    assert response2.status_code in [400, 409]  # Bad Request or Conflict
