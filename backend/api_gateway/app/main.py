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
from .routers import items
from .routers import members
from .routers import invoices
from .routers import bills
from .routers import vendors
from .routers import customers
from .routers import tax_codes
from .routers import accounts
from .routers import sales_invoices
from .routers import credit_notes
from .routers import vendor_credits
from .routers import storage_locations
from .routers import bank_accounts
from .routers import bank_transfers
from .routers import stock_adjustments
from .routers import customer_deposits
from .routers import receive_payments
from .routers import purchase_orders
from .routers import price_lists
from .routers import quotes
from .routers import sales_orders
from .routers import currencies
from .routers import bank_reconciliation
from backend.api_gateway.app.routers import ragcrud_test
from backend.api_gateway.app.routers import ragllm_test
from backend.api_gateway.app.routers import raginject_test
from backend.api_gateway.app.routers import flow
from backend.api_gateway.app.routers import onboarding
from backend.api_gateway.app.routers import setup_chat
from backend.api_gateway.app.routers import public_chat
from backend.api_gateway.app.routers import tenant_chat
from .routers import reports
from .routers import accounting_settings
from .routers import dashboard
from .routers import qr_auth
from .routers import device
from .routers import mfa
from .routers import opening_balance

# P4 Core Completion - 7 New Modules
from .routers import warehouses
from .routers import stock_transfers
from .routers import sales_receipts
from .routers import recurring_invoices
from .routers import item_batches
from .routers import item_serials
from .routers import documents

# P5 Tier 1 Professional - 6 New Modules
from .routers import fixed_assets
from .routers import budgets
from .routers import cost_centers
from .routers import recurring_bills
from .routers import vendor_deposits

# P6 Tier 2 Enterprise - 4 New Modules
from .routers import audit
from .routers import approvals
from .routers import cheques
from .routers import financial_ratios

# P7 Tier 3 Corporate - 3 New Modules
from .routers import consolidation
from .routers import intercompany
from .routers import branches

# P8 Manufacturing - 3 New Modules
from .routers import bom
from .routers import production
from .routers import production_costing

# P9 F&B - 3 New Modules
from .routers import recipes
from .routers import kds
from .routers import tables

# Expenses module
from .routers import expenses
from .routers import kasbank
from .routers import expense_extended

# Accounting Kernel (Layer 0) - 4 Core Modules
from .routers import journals
from .routers import ledger
from .routers import fiscal_years
from .routers import periods
from .routers import user
from .routers import bill_payments

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

# 9. CORS - DISABLED (nginx handles CORS to prevent duplicate headers)
# See: nginx sites-enabled/milkyhoop.conf
# app.add_middleware(
#     CORSMiddleware,
#     allow_origins=settings.CORS_ORIGINS,
#     allow_credentials=True,
#     allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
#     allow_headers=settings.CORS_ALLOW_HEADERS,
# )


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

# Items master data router
app.include_router(items.router, prefix="/api", tags=["items"])

# Members/Customer management router (for POS)
app.include_router(members.router, prefix="/api/members", tags=["members"])

# Invoices router (Faktur Pembelian list)
app.include_router(invoices.router, prefix="/api/invoices", tags=["invoices"])

# Bills router (Faktur Pembelian CRUD + Payments)
app.include_router(bills.router, prefix="/api/bills", tags=["bills"])
app.include_router(
    bill_payments.router, prefix="/api/bill-payments", tags=["bill-payments"]
)

# Vendors router (Supplier/Vendor Master Data)
app.include_router(vendors.router, prefix="/api/vendors", tags=["vendors"])

# Customers router (Customer Master Data)
app.include_router(customers.router, prefix="/api/customers", tags=["customers"])

# Tax Codes router (Tax Master Data)
app.include_router(tax_codes.router, prefix="/api/tax-codes", tags=["tax-codes"])

# Chart of Accounts router (CoA CRUD)
app.include_router(accounts.router, prefix="/api/accounts", tags=["accounts"])

# Sales Invoices router (Faktur Penjualan)
app.include_router(
    sales_invoices.router, prefix="/api/sales-invoices", tags=["sales-invoices"]
)

# Credit Notes router (Nota Kredit / Sales Returns)
app.include_router(
    credit_notes.router, prefix="/api/credit-notes", tags=["credit-notes"]
)

# Vendor Credits router (Kredit Vendor / Purchase Returns)
app.include_router(
    vendor_credits.router, prefix="/api/vendor-credits", tags=["vendor-credits"]
)

# Storage Locations router (Lokasi Penyimpanan)
app.include_router(
    storage_locations.router,
    prefix="/api/storage-locations",
    tags=["storage-locations"],
)

# Price Lists router (Daftar Harga)
app.include_router(price_lists.router, prefix="/api/price-lists", tags=["price-lists"])

# SAK EMKM Financial Reports router
app.include_router(reports.router, prefix="/api/reports", tags=["reports"])
app.include_router(accounting_settings.router, prefix="/api", tags=["settings"])

# Dashboard Summary router (aggregated KPIs)
app.include_router(dashboard.router, prefix="/api/dashboard", tags=["dashboard"])

# QR Login System (Phase: QR Auth)
app.include_router(qr_auth.router, tags=["qr-auth"])
app.include_router(device.router, tags=["devices"])

# MFA (Multi-Factor Authentication) - ISO 27001:2022 A.8.5
app.include_router(mfa.router, tags=["mfa"])

# Opening Balance (Saldo Awal)
app.include_router(opening_balance.router, prefix="/api", tags=["opening-balance"])

# Bank Accounts router (Rekening Bank)
app.include_router(
    bank_accounts.router, prefix="/api/bank-accounts", tags=["bank-accounts"]
)

# Bank Transfers router (Transfer Antar Bank)
app.include_router(
    bank_transfers.router, prefix="/api/bank-transfers", tags=["bank-transfers"]
)

# Stock Adjustments router (Penyesuaian Stok)
app.include_router(
    stock_adjustments.router,
    prefix="/api/stock-adjustments",
    tags=["stock-adjustments"],
)

# Customer Deposits router (Uang Muka Pelanggan)
app.include_router(
    customer_deposits.router,
    prefix="/api/customer-deposits",
    tags=["customer-deposits"],
)

# Receive Payments router (Penerimaan Pembayaran)
app.include_router(
    receive_payments.router, prefix="/api/receive-payments", tags=["receive-payments"]
)

# Purchase Orders router (Pesanan Pembelian)
app.include_router(
    purchase_orders.router, prefix="/api/purchase-orders", tags=["purchase-orders"]
)

# Quotes router (Penawaran Harga)
app.include_router(quotes.router, prefix="/api/quotes", tags=["quotes"])

# Sales Orders router (Pesanan Penjualan)
app.include_router(
    sales_orders.router, prefix="/api/sales-orders", tags=["sales-orders"]
)

# Currencies router (Multi-currency Management)
app.include_router(currencies.router, prefix="/api/currencies", tags=["currencies"])

# Bank Reconciliation router (Rekonsiliasi Bank)
app.include_router(
    bank_reconciliation.router,
    prefix="/api/bank-reconciliation",
    tags=["bank-reconciliation"],
)

# ===========================================
# P4 CORE COMPLETION - 7 NEW MODULES
# ===========================================

# Warehouses router (Gudang & Lokasi)
app.include_router(warehouses.router, prefix="/api/warehouses", tags=["warehouses"])

# Stock Transfers router (Transfer Stok Antar Gudang)
app.include_router(
    stock_transfers.router, prefix="/api/stock-transfers", tags=["stock-transfers"]
)

# Sales Receipts router (Bukti Penjualan / POS)
app.include_router(
    sales_receipts.router, prefix="/api/sales-receipts", tags=["sales-receipts"]
)

# Recurring Invoices router (Faktur Berulang)
app.include_router(
    recurring_invoices.router,
    prefix="/api/recurring-invoices",
    tags=["recurring-invoices"],
)

# Item Batches router (Nomor Lot & Kedaluwarsa - FEFO)
app.include_router(
    item_batches.router, prefix="/api/item-batches", tags=["item-batches"]
)

# Item Serials router (Nomor Seri)
app.include_router(
    item_serials.router, prefix="/api/item-serials", tags=["item-serials"]
)

# Documents router (Lampiran / Attachments - S3/MinIO)
app.include_router(documents.router, prefix="/api/documents", tags=["documents"])

# ===========================================
# P5 TIER 1 PROFESSIONAL - 6 NEW MODULES
# ===========================================

# Fixed Assets router (Aset Tetap - with depreciation)
app.include_router(
    fixed_assets.router, prefix="/api/fixed-assets", tags=["fixed-assets"]
)

# Budgets router (Anggaran - NO journal, planning only)
app.include_router(budgets.router, prefix="/api/budgets", tags=["budgets"])

# Cost Centers router (Pusat Biaya - NO journal, dimension)
app.include_router(
    cost_centers.router, prefix="/api/cost-centers", tags=["cost-centers"]
)

# Recurring Bills router (Tagihan Berulang)
app.include_router(
    recurring_bills.router, prefix="/api/recurring-bills", tags=["recurring-bills"]
)

# Vendor Deposits router (Uang Muka Vendor)
app.include_router(
    vendor_deposits.router, prefix="/api/vendor-deposits", tags=["vendor-deposits"]
)

# ===========================================
# P6 TIER 2 ENTERPRISE - 4 NEW MODULES
# ===========================================

# Audit Trail router (Jejak Audit - NO journal)
app.include_router(audit.router, prefix="/api", tags=["audit"])

# Approval Workflows router (Alur Persetujuan - NO journal)
app.include_router(approvals.router, prefix="/api", tags=["approvals"])

# Cheques/Giro router (Manajemen Giro - HAS journal)
app.include_router(cheques.router, prefix="/api/cheques", tags=["cheques"])

# Financial Ratios router (Rasio Keuangan - NO journal)
app.include_router(
    financial_ratios.router, prefix="/api/financial-ratios", tags=["financial-ratios"]
)

# ===========================================
# P7 TIER 3 CORPORATE - 3 NEW MODULES
# ===========================================

# Consolidation router (Konsolidasi Laporan Multi-Entitas)
app.include_router(
    consolidation.router, prefix="/api/consolidation", tags=["consolidation"]
)

# Intercompany router (Transaksi Antar Perusahaan - HAS journal)
app.include_router(
    intercompany.router, prefix="/api/intercompany", tags=["intercompany"]
)

# Branches router (Multi-Cabang - HAS journal for transfers)
app.include_router(branches.router, prefix="/api/branches", tags=["branches"])

# ===========================================
# P8 MANUFACTURING - 3 NEW MODULES
# ===========================================

# BOM router (Bill of Materials - NO journal, master data)
app.include_router(bom.router, prefix="/api/bom", tags=["bom"])

# Production router (Production Orders - HAS journal for COGS)
app.include_router(production.router, prefix="/api/production", tags=["production"])

# Production Costing router (Kalkulasi Harga Produksi - NO journal)
app.include_router(
    production_costing.router,
    prefix="/api/production-costing",
    tags=["production-costing"],
)

# ===========================================
# P9 F&B - 3 NEW MODULES
# ===========================================

# Recipes router (Manajemen Resep - NO journal, master data)
app.include_router(recipes.router, prefix="/api/recipes", tags=["recipes"])

# KDS router (Kitchen Display System - NO journal, operational)
app.include_router(kds.router, prefix="/api/kds", tags=["kds"])

# Tables router (Manajemen Meja Restoran - NO journal, operational)
app.include_router(tables.router, prefix="/api/tables", tags=["tables"])

# ===========================================
# ACCOUNTING KERNEL (LAYER 0) - 4 CORE MODULES
# ===========================================

# Journals router (Jurnal Umum - Manual Journal Entries)
app.include_router(journals.router, prefix="/api/journals", tags=["journals"])

# Ledger router (Buku Besar - Read-only Ledger Views)
app.include_router(ledger.router, prefix="/api/ledger", tags=["ledger"])

# Fiscal Years router (Tahun Fiskal)
app.include_router(
    fiscal_years.router, prefix="/api/fiscal-years", tags=["fiscal-years"]
)

# Periods router (Periode Akuntansi)
app.include_router(periods.router, prefix="/api/periods", tags=["periods"])
app.include_router(user.router, prefix="/api", tags=["user"])

# Expenses router (Biaya & Pengeluaran)
app.include_router(expenses.router, prefix="/api/expenses", tags=["expenses"])
app.include_router(kasbank.router, prefix="/api/kasbank", tags=["kasbank"])
app.include_router(expense_extended.router, prefix="/api", tags=["expense-extended"])


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


# ===========================================
# ALIAS ENDPOINTS FOR AUDIT COMPATIBILITY
# ===========================================

import asyncpg
from fastapi import HTTPException, Request, Query
from typing import Optional
from datetime import date


async def _get_pool() -> asyncpg.Pool:
    """Get database connection pool."""
    db_config = settings.get_db_config()
    return await asyncpg.create_pool(
        **db_config, min_size=2, max_size=10, command_timeout=60
    )


def _get_user_context(request: Request) -> dict:
    """Extract user context from request."""
    if not hasattr(request.state, "user") or not request.state.user:
        raise HTTPException(status_code=401, detail="Authentication required")
    user = request.state.user
    tenant_id = user.get("tenant_id")
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Invalid user context")
    return {"tenant_id": tenant_id, "user_id": user.get("user_id")}


# Note: /api/journals is now handled by journals.router (Accounting Kernel Layer 0)


@app.get("/api/payments", tags=["payments"])
async def list_payments(
    request: Request,
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    payment_type: Optional[str] = Query(default=None),
    start_date: Optional[date] = Query(default=None),
    end_date: Optional[date] = Query(default=None),
):
    """List all payments (sales invoice payments + bill payments) - alias endpoint."""
    try:
        ctx = _get_user_context(request)
        pool = await _get_pool()

        async with pool.acquire() as conn:
            # Build unified payments query from both tables
            params = [ctx["tenant_id"]]
            param_idx = 2

            date_filter = ""
            if start_date:
                date_filter += f" AND payment_date >= ${param_idx}"
                params.append(start_date)
                param_idx += 1
            if end_date:
                date_filter += f" AND payment_date <= ${param_idx}"
                params.append(end_date)
                param_idx += 1

            type_filter_received = "" if payment_type != "made" else "AND 1=0"
            type_filter_made = "" if payment_type != "received" else "AND 1=0"

            # Unified query combining sales invoice payments and bill payments
            query = f"""
                WITH unified_payments AS (
                    SELECT
                        sip.id,
                        sip.payment_date,
                        sip.amount,
                        sip.payment_method,
                        si.invoice_number as reference_number,
                        'received' as payment_type,
                        sip.created_at
                    FROM sales_invoice_payments sip
                    JOIN sales_invoices si ON si.id = sip.invoice_id
                    WHERE si.tenant_id = $1 {date_filter} {type_filter_received}

                    UNION ALL

                    SELECT
                        bp.id,
                        bp.payment_date,
                        bp.amount,
                        bp.payment_method,
                        b.invoice_number as reference_number,
                        'made' as payment_type,
                        bp.created_at
                    FROM bill_payments bp
                    JOIN bills b ON b.id = bp.bill_id
                    WHERE b.tenant_id = $1 {date_filter} {type_filter_made}
                )
                SELECT * FROM unified_payments
                ORDER BY created_at DESC
                LIMIT ${param_idx} OFFSET ${param_idx + 1}
            """
            params.extend([per_page, (page - 1) * per_page])

            rows = await conn.fetch(query, *params)

            # Count total
            count_query = f"""
                SELECT (
                    SELECT COUNT(*) FROM sales_invoice_payments sip
                    JOIN sales_invoices si ON si.id = sip.invoice_id
                    WHERE si.tenant_id = $1 {date_filter} {type_filter_received}
                ) + (
                    SELECT COUNT(*) FROM bill_payments bp
                    JOIN bills b ON b.id = bp.bill_id
                    WHERE b.tenant_id = $1 {date_filter} {type_filter_made}
                )
            """
            total = await conn.fetchval(
                count_query, *params[:-2]
            )  # Exclude pagination params

            return {
                "success": True,
                "data": [
                    {
                        "id": str(row["id"]),
                        "payment_date": row["payment_date"].isoformat()
                        if row["payment_date"]
                        else None,
                        "amount": row["amount"],
                        "payment_method": row["payment_method"],
                        "reference_number": row["reference_number"],
                        "payment_type": row["payment_type"],
                        "created_at": row["created_at"].isoformat()
                        if row["created_at"]
                        else None,
                    }
                    for row in rows
                ],
                "pagination": {
                    "page": page,
                    "per_page": per_page,
                    "total": total,
                    "total_pages": (total + per_page - 1) // per_page if total else 0,
                },
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing payments: {e}")
        raise HTTPException(status_code=500, detail="Failed to list payments")


# Note: /api/production/orders is handled by the production.py router
# Added /orders alias endpoint in production.py to avoid route conflict with /{order_id}
