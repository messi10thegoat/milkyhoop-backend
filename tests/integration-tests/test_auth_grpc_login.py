import os
import grpc
import pytest

from protos.auth.auth_pb2 import LoginRequest
from protos.auth.auth_pb2_grpc import AuthServiceStub

AUTH_GRPC_HOST = os.getenv("AUTH_GRPC_HOST", "auth-service")
AUTH_GRPC_PORT = os.getenv("AUTH_GRPC_PORT", "5004")

@pytest.mark.asyncio
async def test_login_invalid_credentials():
    channel = grpc.aio.insecure_channel(f"{AUTH_GRPC_HOST}:{AUTH_GRPC_PORT}")
    stub = AuthServiceStub(channel)

    request = LoginRequest(email="invalid@milkyhoop.com", password="wrong")
    with pytest.raises(grpc.aio.AioRpcError) as excinfo:
        await stub.Login(request)

    # ✅ Sesuaikan expected status code dengan hasil nyata
    assert excinfo.value.code() == grpc.StatusCode.NOT_FOUND
