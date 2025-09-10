import os
import sys
import asyncio
from typing import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

# Import routers
from .routers import health
from .routers import chat
from .routers import session
# from .routers import customer  # DISABLED: proto dependency issues
from backend.api_gateway.app.routers import ragcrud_test
from backend.api_gateway.app.routers import ragllm_test
from backend.api_gateway.app.routers import raginject_test
from backend.api_gateway.app.routers import flow
from backend.api_gateway.app.routers import onboarding

# Import authentication middleware
from .middleware.auth_middleware import AuthMiddleware

# ✅ Logging setup
structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer(),
    ],
    logger_factory=structlog.PrintLoggerFactory(file=sys.stdout)
)

# ✅ Prisma binary path
os.environ["PRISMA_QUERY_ENGINE_BINARY"] = "/app/libs/milkyhoop_prisma/engine/query-engine-debian-openssl-3.0.x"

# ⛓️ Prisma client (Python)
from backend.api_gateway.libs.milkyhoop_prisma import Prisma

prisma = Prisma()

# 🔁 Prisma lifecycle (startup & shutdown)
@asynccontextmanager
async def prisma_lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    retries = 5
    for attempt in range(1, retries + 1):
        try:
            await prisma.connect()
            print("✅ Prisma connected.")
            break
        except Exception as e:
            print(f"⚠️ Retry {attempt}/{retries} connecting Prisma failed: {e}")
            await asyncio.sleep(3)
    else:
        print("❌ All Prisma connection attempts failed. Continuing without DB.")
    yield
    try:
        await prisma.disconnect()
        print("✅ Prisma disconnected.")
    except Exception as e:
        print(f"⚠️ Error disconnecting Prisma: {e}")

# 🚀 FastAPI app
app = FastAPI(
    title="MilkyHoop API Gateway",
    description="Enterprise Multi-Tenant Chatbot Platform with Phase 2 Authentication",
    version="3.0.0",
    lifespan=prisma_lifespan
)

# 🔐 Add Authentication Middleware
# Per Phase 2 documentation: middleware registration AFTER app creation, BEFORE routers
app.add_middleware(AuthMiddleware)

# 📍 Include routers
app.include_router(health.router, prefix="/health", tags=["health"])
app.include_router(chat.router, prefix="/chat", tags=["chat"])
app.include_router(session.router, prefix="/api/auth", tags=["authentication"])
# app.include_router(customer.router, prefix="/api/customer", tags=["customer"])  # DISABLED
app.include_router(ragcrud_test.router, prefix="/api/test/ragcrud", tags=["ragcrud"])
app.include_router(ragllm_test.router, prefix="/api/test/ragllm", tags=["ragllm"]) 
app.include_router(raginject_test.router, prefix="/api/test/raginject", tags=["raginject"])
app.include_router(flow.router, prefix="/api/flow", tags=["flow"])
app.include_router(onboarding.router, prefix="/api/onboarding", tags=["onboarding"])

@app.get("/")
async def root():
    return {
        "message": "MilkyHoop API Gateway v3.0.0",
        "status": "operational",
        "phase": "Phase 2 - Authentication Active",
        "features": {
            "authentication": "Active - JWT + Session Management",
            "session_management": "Active - Redis Storage", 
            "middleware": "Active - Path-based Access Control",
            "multi_tenant": "Active",
            "chatbot_services": "Active"
        },
        "endpoints": {
            "protected": ["/chat/", "/api/setup/", "/api/test/", "/api/auth/"],
            "public": ["/health", "/healthz", "/docs", "/"],
            "session": ["/api/auth/logout", "/api/auth/logout-all", "/api/auth/sessions"]
        }
    }

@app.get("/healthz")
async def healthz():
    return {"status": "healthy", "phase": "2", "middleware": "active"}
