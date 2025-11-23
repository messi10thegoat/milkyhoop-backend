import os
import sys
import asyncio
from typing import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Import routers
from .routers import health
from .routers import chat
from .routers import session
from .routers import auth
from .routers import customer
from .routers import transactions
from .routers import products
from .routers import suppliers
from backend.api_gateway.app.routers import ragcrud_test
from backend.api_gateway.app.routers import ragllm_test
from backend.api_gateway.app.routers import raginject_test
from backend.api_gateway.app.routers import flow
from backend.api_gateway.app.routers import onboarding
from backend.api_gateway.app.routers import setup_chat
from backend.api_gateway.app.routers import public_chat
from backend.api_gateway.app.routers import tenant_chat

# Import authentication middleware
from .middleware.auth_middleware import AuthMiddleware

# Import tenant_orchestrator proto stubs
from backend.api_gateway.libs.milkyhoop_protos import tenant_orchestrator_pb2, tenant_orchestrator_pb2_grpc

# Import auth_client singleton from separate module (avoids circular import)
from backend.api_gateway.app.services.auth_instance import auth_client

# Logging setup
structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer(),
    ],
    logger_factory=structlog.PrintLoggerFactory(file=sys.stdout)
)

logger = structlog.get_logger()

# Prisma binary path
os.environ["PRISMA_QUERY_ENGINE_BINARY"] = "/app/backend/api_gateway/libs/milkyhoop_prisma/engine/query-engine-debian-openssl-3.5.x"

# Prisma client (Python)
from backend.api_gateway.libs.milkyhoop_prisma import Prisma

prisma = Prisma()

# Prisma lifecycle (startup & shutdown)
@asynccontextmanager
async def prisma_lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    retries = 5
    for attempt in range(1, retries + 1):
        try:
            await prisma.connect()
            print("Prisma connected.")
            break
        except Exception as e:
            print(f"Retry {attempt}/{retries} connecting Prisma failed: {e}")
            await asyncio.sleep(3)
    else:
        print("All Prisma connection attempts failed. Continuing without DB.")
    yield
    try:
        await prisma.disconnect()
        print("Prisma disconnected.")
    except Exception as e:
        print(f"Error disconnecting Prisma: {e}")

# FastAPI app
app = FastAPI(
    title="MilkyHoop API Gateway",
    description="Enterprise Multi-Tenant Chatbot Platform with Phase 2 Authentication",
    version="3.0.0",
    lifespan=prisma_lifespan
)

# Add Authentication Middleware
app.add_middleware(AuthMiddleware)

# Add CORS Middleware for Frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000", "https://milkyhoop.com", "https://dev.milkyhoop.com", "http://localhost:3001"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

@app.on_event("startup")
async def startup_event():
    """Initialize auth service connection on startup"""
    logger.info("API Gateway starting up...")
    try:
        await auth_client.connect()
        logger.info("Connected to Auth Service successfully")
    except Exception as e:
        logger.error(f"Failed to connect to Auth Service: {e}")

@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup auth service connection on shutdown"""
    logger.info("API Gateway shutting down...")
    try:
        await auth_client.close()
        logger.info("Auth Service connection closed")
    except Exception as e:
        logger.error(f"Error closing Auth Service connection: {e}")

# Include routers - Industry Standard Route Structure
app.include_router(health.router, prefix="/health", tags=["health"])
app.include_router(chat.router, prefix="/chat", tags=["chat"])
app.include_router(auth.router, prefix="/api/auth", tags=["authentication"])
app.include_router(session.router, tags=["authentication"])
app.include_router(customer.router, prefix="", tags=["customer"])
app.include_router(ragcrud_test.router, prefix="/api/test/ragcrud", tags=["ragcrud"])
app.include_router(ragllm_test.router, prefix="/api/test/ragllm", tags=["ragllm"]) 
app.include_router(raginject_test.router, prefix="/api/test/raginject", tags=["raginject"])
app.include_router(flow.router, prefix="/api/flow", tags=["flow"])
app.include_router(onboarding.router, prefix="/api/onboarding", tags=["onboarding"])

# NEW: Split routers for Setup Mode and Customer Mode
app.include_router(setup_chat.router, prefix="/api/setup", tags=["setup"])
app.include_router(public_chat.router, prefix="", tags=["public"])
app.include_router(tenant_chat.router, prefix="/api/tenant", tags=["tenant"])

# Transaction form-based router
app.include_router(transactions.router, prefix="/api/transactions", tags=["transactions"])

# Autocomplete routers
app.include_router(products.router, prefix="/api/products", tags=["products"])
app.include_router(suppliers.router, prefix="/api/suppliers", tags=["suppliers"])

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
            "public": ["/health", "/healthz", "/docs", "/", "/{tenant_id}/chat"],
            "session": ["/api/auth/logout", "/api/auth/logout-all", "/api/auth/sessions"]
        },
        "routes": {
            "setup_mode": "POST /api/setup/chat (authenticated)",
            "customer_mode": "POST /{tenant_id}/chat (public)",
            "tenant_mode": "POST /api/tenant/{tenant_id}/chat (authenticated)"
        }
    }

@app.get("/healthz")
async def healthz():
    return {"status": "healthy", "phase": "2", "middleware": "active"}