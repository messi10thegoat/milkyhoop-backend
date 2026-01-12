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
from .routers import inventory
from .routers import members
from .routers import invoices
from .routers import bills
from backend.api_gateway.app.routers import ragcrud_test
from backend.api_gateway.app.routers import ragllm_test
from backend.api_gateway.app.routers import raginject_test
from backend.api_gateway.app.routers import flow
from backend.api_gateway.app.routers import onboarding
from backend.api_gateway.app.routers import setup_chat
from backend.api_gateway.app.routers import public_chat
from backend.api_gateway.app.routers import tenant_chat
from .routers import reports
from .routers import dashboard
from .routers import qr_auth
from .routers import device
from .routers import mfa

# Import middleware
from .middleware.auth_middleware import AuthMiddleware
from .middleware.rate_limit_middleware import RateLimitMiddleware
from .middleware.rbac_middleware import RBACMiddleware
from .middleware.security_headers_middleware import SecurityHeadersMiddleware
from .middleware.account_lockout_middleware import AccountLockoutMiddleware
from .middleware.request_id_middleware import RequestIDMiddleware
from .middleware.waf_middleware import WAFMiddleware
from .middleware.tenant_validation_middleware import TenantValidationMiddleware

# Import FLE middleware (optional - for PII encryption)
try:
    from .services.crypto.fle_middleware import FLEMiddleware, PIIMaskingMiddleware

    FLE_AVAILABLE = True
except ImportError:
    FLE_AVAILABLE = False

# Import centralized config
from .config import settings

# Import tenant_orchestrator proto stubs

# Import auth_client singleton from separate module (avoids circular import)
from backend.api_gateway.app.services.auth_instance import auth_client

# Logging setup
structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer(),
    ],
    logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
)

logger = structlog.get_logger()

# Prisma binary path
os.environ[
    "PRISMA_QUERY_ENGINE_BINARY"
] = "/app/backend/api_gateway/libs/milkyhoop_prisma/engine/query-engine-debian-openssl-3.5.x"

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


# FastAPI app with environment-aware configuration
# Disable OpenAPI/Swagger in production for security
_is_production = settings.ENVIRONMENT == "production"

app = FastAPI(
    title="MilkyHoop API Gateway",
    description="Enterprise Multi-Tenant Chatbot Platform with Phase 2 Authentication",
    version="3.0.0",
    lifespan=prisma_lifespan,
    # Security: Disable interactive docs in production
    docs_url=None if _is_production else "/docs",
    redoc_url=None if _is_production else "/redoc",
    openapi_url=None if _is_production else "/openapi.json",
)

# Validate configuration on startup
config_errors = settings.validate()
if config_errors and settings.ENVIRONMENT == "production":
    for error in config_errors:
        print(f"CONFIG ERROR: {error}")
    # In production, fail fast if secrets not configured
    # raise RuntimeError("Missing required configuration. Check environment variables.")

# ===========================================
# MIDDLEWARE CHAIN (order matters - last added runs first)
# ===========================================

# 0. FLE Middleware (Field-Level Encryption for PII) - Optional
if FLE_AVAILABLE and settings.FLE_ENABLED:
    app.add_middleware(FLEMiddleware, enabled=True)
    app.add_middleware(PIIMaskingMiddleware)
    print("FLE Middleware enabled - PII encryption active")

# 1. Security Headers (runs last, adds headers to response)
app.add_middleware(SecurityHeadersMiddleware)

# 2. Request ID Tracking (for audit trail)
app.add_middleware(RequestIDMiddleware)

# 3. WAF (Web Application Firewall - blocks attacks)
app.add_middleware(WAFMiddleware, enabled=True, strict_mode=False)

# 4. Rate Limiting (outermost protection)
app.add_middleware(RateLimitMiddleware)

# 5. Account Lockout (brute force protection)
app.add_middleware(AccountLockoutMiddleware)

# 6. Tenant Validation (prevents IDOR attacks - runs after Auth sets user)
app.add_middleware(TenantValidationMiddleware)

# 7. RBAC (role-based access control)
app.add_middleware(RBACMiddleware)

# 8. Authentication (validates tokens)
app.add_middleware(AuthMiddleware)

# 9. CORS (handles cross-origin requests)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=settings.CORS_ALLOW_HEADERS,  # Explicit list, not "*"
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
app.include_router(
    raginject_test.router, prefix="/api/test/raginject", tags=["raginject"]
)
app.include_router(flow.router, prefix="/api/flow", tags=["flow"])
app.include_router(onboarding.router, prefix="/api/onboarding", tags=["onboarding"])

# NEW: Split routers for Setup Mode and Customer Mode
app.include_router(setup_chat.router, prefix="/api/setup", tags=["setup"])
app.include_router(public_chat.router, prefix="", tags=["public"])
app.include_router(tenant_chat.router, prefix="/api/tenant", tags=["tenant"])

# Transaction form-based router
app.include_router(
    transactions.router, prefix="/api/transactions", tags=["transactions"]
)

# Autocomplete routers
app.include_router(products.router, prefix="/api/products", tags=["products"])
app.include_router(suppliers.router, prefix="/api/suppliers", tags=["suppliers"])

# Inventory management router
app.include_router(inventory.router, prefix="/api/inventory", tags=["inventory"])

# Members/Customer management router (for POS)
app.include_router(members.router, prefix="/api/members", tags=["members"])

# Invoices router (Faktur Pembelian list)
app.include_router(invoices.router, prefix="/api/invoices", tags=["invoices"])

# Bills router (Faktur Pembelian CRUD + Payments)
app.include_router(bills.router, prefix="/api/bills", tags=["bills"])

# SAK EMKM Financial Reports router
app.include_router(reports.router, prefix="/api/reports", tags=["reports"])

# Dashboard Summary router (aggregated KPIs)
app.include_router(dashboard.router, prefix="/api/dashboard", tags=["dashboard"])

# QR Login System (Phase: QR Auth)
app.include_router(qr_auth.router, tags=["qr-auth"])
app.include_router(device.router, tags=["devices"])

# MFA (Multi-Factor Authentication) - ISO 27001:2022 A.8.5
app.include_router(mfa.router, tags=["mfa"])


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
            "chatbot_services": "Active",
            "field_level_encryption": "Active"
            if (FLE_AVAILABLE and settings.FLE_ENABLED)
            else "Disabled",
        },
        "endpoints": {
            "protected": ["/chat/", "/api/setup/", "/api/test/", "/api/auth/"],
            "public": ["/health", "/healthz", "/docs", "/", "/{tenant_id}/chat"],
            "session": [
                "/api/auth/logout",
                "/api/auth/logout-all",
                "/api/auth/sessions",
            ],
        },
        "routes": {
            "setup_mode": "POST /api/setup/chat (authenticated)",
            "customer_mode": "POST /{tenant_id}/chat (public)",
            "tenant_mode": "POST /api/tenant/{tenant_id}/chat (authenticated)",
        },
    }


@app.get("/healthz")
async def healthz():
    return {"status": "healthy", "phase": "2", "middleware": "active"}
