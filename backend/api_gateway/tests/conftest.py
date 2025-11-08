"""
Test Configuration and Fixtures
Provides reusable test fixtures for auth testing
"""
import pytest
import asyncio
from typing import AsyncGenerator, Dict
from httpx import AsyncClient
from fastapi.testclient import TestClient

# Import your app
import sys
sys.path.insert(0, '/app/backend/api_gateway')
from app.main import app


# ======================
# Async Event Loop Setup
# ======================

@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for async tests"""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


# ======================
# HTTP Client Fixtures
# ======================

@pytest.fixture
async def async_client() -> AsyncGenerator[AsyncClient, None]:
    """Async HTTP client for testing API endpoints"""
    async with AsyncClient(app=app, base_url="http://test") as client:
        yield client


@pytest.fixture
def sync_client():
    """Sync HTTP client for simple tests"""
    return TestClient(app)


# ======================
# Test User Fixtures
# ======================

@pytest.fixture
def test_user_data() -> Dict[str, str]:
    """Valid test user registration data"""
    return {
        "email": "pytest_user@test.com",
        "password": "TestPass123!",
        "username": "pytest_user",
        "name": "Pytest User"
    }


@pytest.fixture
def invalid_password_data() -> Dict[str, str]:
    """Invalid password (no special char)"""
    return {
        "email": "pytest_invalid@test.com",
        "password": "TestPass123",  # Missing special char
        "username": "pytest_invalid",
        "name": "Pytest Invalid"
    }


@pytest.fixture
def weak_password_data() -> Dict[str, str]:
    """Weak password (too short)"""
    return {
        "email": "pytest_weak@test.com",
        "password": "Test1!",  # Too short
        "username": "pytest_weak",
        "name": "Pytest Weak"
    }


# ======================
# Auth Token Fixtures
# ======================

@pytest.fixture
async def registered_user_token(async_client: AsyncClient, test_user_data: Dict) -> str:
    """Register a user and return their access token"""
    # Register user
    response = await async_client.post("/api/auth/register", json=test_user_data)
    
    if response.status_code == 200:
        data = response.json()
        return data["data"]["access_token"]
    
    # If user already exists, try to get token another way
    # For now, return None and let test handle it
    return None


@pytest.fixture
def invalid_token() -> str:
    """Invalid JWT token for testing auth failures"""
    return "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.invalid.token"


@pytest.fixture
def expired_token() -> str:
    """Expired JWT token (intentionally old)"""
    # This is a token with exp set to past date
    return "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1c2VyX2lkIjoidGVzdCIsImV4cCI6MTYwMDAwMDAwMH0.invalid"


# ======================
# Database Cleanup
# ======================

@pytest.fixture(autouse=True)
async def cleanup_test_data():
    """
    Cleanup test data after each test
    Note: In production, you'd want proper DB cleanup
    """
    yield
    # Add cleanup logic here if needed
    pass


# ======================
# Mock Service Fixtures
# ======================

@pytest.fixture
def mock_auth_service_response():
    """Mock successful auth service gRPC response"""
    return {
        "success": True,
        "user_id": "test-user-id",
        "email": "test@test.com",
        "access_token": "mock.jwt.token",
        "refresh_token": "mock.refresh.token"
    }


@pytest.fixture
def mock_invalid_token_response():
    """Mock invalid token validation response"""
    return {
        "valid": False,
        "error": "Invalid token"
    }
