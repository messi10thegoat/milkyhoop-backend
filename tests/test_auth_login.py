import sys
import pytest
from httpx import AsyncClient, ASGITransport
from fastapi import status

# ✅ Pastikan PYTHONPATH benar
sys.path.insert(0, "/app")
sys.path.insert(0, "/app/backend/services/auth-service")

# ✅ Import langsung app & prisma
from http_server import app
from app.prisma import init_prisma, prisma


@pytest.mark.asyncio
async def test_login_invalid_credentials():
    print("🔥 TEST DEBUG: prisma connected =", prisma.is_connected())

    # ✅ Fix utama: connect dulu
    await init_prisma()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.post("/login", json={"email": "salah@milkyhoop.com", "password": "salah"})
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    await prisma.disconnect()
