"""
DEPRECATED - Legacy POS Invoice Router
=======================================
Status: DEPRECATED as of 2026-02-13 (Pure Ledger migration)
Reason: Reads entirely from `transaksi_harian` (legacy POS table)

This router was used for the old POS purchase invoice listing.
The modern equivalent is:
  - /api/bills (purchase invoices / faktur pembelian) -> bills.py
  - /api/sales-invoices (sales invoices / faktur penjualan) -> sales_invoices.py

Frontend usage: NONE (only an example comment in api/client/index.ts)
The /api/invoices/purchase endpoint is not called from any frontend code.

Action: Router registration kept in main.py for backward compat but all
endpoints return 410 Gone. Will be fully removed in a future cleanup.
"""
from fastapi import APIRouter, HTTPException
import logging

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/purchase")
async def get_purchase_invoices_deprecated():
    """
    DEPRECATED: Use GET /api/bills instead.
    This endpoint read from transaksi_harian which is a legacy POS table.
    """
    raise HTTPException(
        status_code=410,
        detail={
            "message": "This endpoint is deprecated. Use GET /api/bills for purchase invoices.",
            "migration": "transaksi_harian -> bills (Pure Ledger)",
            "replacement": "/api/bills",
        },
    )


@router.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "deprecated", "service": "invoices_router", "note": "Use /api/bills instead"}
