"""
Accounting Kernel Bridge for Outbox Worker
===========================================

Bridges outbox events to the new Accounting Kernel.
This replaces the direct gRPC call to accounting_service with
the new double-entry bookkeeping engine.

Author: MilkyHoop Team
Version: 1.0.0
"""

import asyncio
import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Dict, Any, Optional
from uuid import UUID
import os

import asyncpg

# Import accounting kernel components
import sys
sys.path.insert(0, '/app/backend/services/accounting_kernel')

from integration.transaction_handler import TransactionEventHandler
from integration.facade import AccountingFacade

logger = logging.getLogger(__name__)


class AccountingKernelBridge:
    """
    Bridge between outbox events and the Accounting Kernel.

    Handles:
    - accounting.create events → Journal entries with double-entry bookkeeping
    - Transaction type mapping to proper journal entries
    - Proper CoA usage from seeded accounts
    """

    def __init__(self, database_url: Optional[str] = None):
        """
        Initialize bridge with database connection.

        Args:
            database_url: PostgreSQL connection URL
        """
        self.database_url = database_url or os.getenv('DATABASE_URL')
        self.pool: Optional[asyncpg.Pool] = None
        self.event_handler: Optional[TransactionEventHandler] = None
        self.facade: Optional[AccountingFacade] = None
        self._initialized = False

    async def initialize(self):
        """Initialize database pool and services."""
        if self._initialized:
            return

        try:
            self.pool = await asyncpg.create_pool(
                self.database_url,
                min_size=2,
                max_size=10
            )

            self.event_handler = TransactionEventHandler(self.pool)
            self.facade = AccountingFacade(self.pool)

            self._initialized = True
            logger.info("✅ AccountingKernelBridge initialized")

        except Exception as e:
            logger.error(f"❌ Failed to initialize AccountingKernelBridge: {e}")
            raise

    async def close(self):
        """Close database connections."""
        if self.pool:
            await self.pool.close()
            self._initialized = False
            logger.info("✅ AccountingKernelBridge closed")

    async def handle_accounting_create(self, payload: Dict[str, Any]) -> bool:
        """
        Handle accounting.create event from outbox.

        IDEMPOTENT: Checks if journal already exists for this source_id
        before creating a new one. This prevents duplicate entries on retry.

        Payload structure:
        {
            "transaksi_id": "uuid",
            "tenant_id": "string",
            "jenis_transaksi": "penjualan|pembelian|beban",
            "total_nominal": int64,
            "periode_pelaporan": "YYYY-MM"
        }

        Returns:
            True if successful (or already exists), False otherwise
        """
        await self.initialize()

        try:
            tenant_id = payload.get("tenant_id")
            transaksi_id = payload.get("transaksi_id")
            jenis_transaksi = payload.get("jenis_transaksi")
            total_nominal = Decimal(str(payload.get("total_nominal", 0)))
            periode_pelaporan = payload.get("periode_pelaporan")

            logger.info(f"📒 Processing accounting.create | tenant={tenant_id} | tx={transaksi_id[:12] if transaksi_id else 'N/A'}... | type={jenis_transaksi}")

            # ==========================================
            # IDEMPOTENCY CHECK: Skip if journal exists
            # ==========================================
            # transaksi_id can be:
            # 1. UUID string -> convert and query by source_id
            # 2. "tx_xxxxx" format -> query by trace_id pattern
            async with self.pool.acquire() as conn:
                existing = None

                # Try as UUID first (source_id column)
                try:
                    source_uuid = UUID(transaksi_id) if transaksi_id else None
                    if source_uuid:
                        existing = await conn.fetchval(
                            """
                            SELECT id FROM journal_entries
                            WHERE tenant_id = $1 AND source_id = $2
                            LIMIT 1
                            """,
                            tenant_id,
                            source_uuid
                        )
                except (ValueError, TypeError):
                    # Not a valid UUID, try trace_id pattern
                    if transaksi_id:
                        # trace_id format: "POS-{tx_id}" or "PUR-{tx_id}"
                        existing = await conn.fetchval(
                            """
                            SELECT id FROM journal_entries
                            WHERE tenant_id = $1
                              AND (trace_id::text LIKE $2 OR trace_id::text LIKE $3)
                            LIMIT 1
                            """,
                            tenant_id,
                            f"%{transaksi_id}%",
                            f"%{transaksi_id}%"
                        )

                if existing:
                    logger.info(f"⏭️ Journal already exists for tx={transaksi_id[:12] if transaksi_id else 'N/A'}, skipping (idempotent)")
                    return True  # Return success since journal exists

            # Map jenis_transaksi to event_type
            event_type_map = {
                "penjualan": "transaction.sale.completed",
                "pembelian": "transaction.purchase.completed",
                "beban": "expense.recorded",
                "kulakan": "transaction.purchase.completed",
            }

            event_type = event_type_map.get(jenis_transaksi, "transaction.sale.completed")

            # Build event payload
            event_payload = {
                "tenant_id": tenant_id,
                "transaction_id": transaksi_id,
                "transaction_date": date.today().isoformat(),
                "total_amount": float(total_nominal),
                "payment_method": payload.get("metode_pembayaran", "tunai"),
            }

            # Add type-specific fields
            if jenis_transaksi in ["penjualan"]:
                event_payload["customer_name"] = payload.get("nama_pihak", "Customer")
                event_payload["description"] = f"Penjualan - {transaksi_id[:12]}"
            elif jenis_transaksi in ["pembelian", "kulakan"]:
                event_payload["supplier_name"] = payload.get("nama_pihak", "Supplier")
                event_payload["description"] = f"Pembelian - {transaksi_id[:12]}"
            elif jenis_transaksi == "beban":
                event_payload["expense_id"] = transaksi_id
                event_payload["expense_account"] = payload.get("kategori_beban", "5-10100")
                event_payload["description"] = payload.get("keterangan", f"Beban - {transaksi_id[:12]}")

            # Process via TransactionEventHandler
            result = await self.event_handler.handle_event(event_type, event_payload)

            if result.get("success"):
                journal_id = result.get("result", {}).get("journal_id")
                journal_number = result.get("result", {}).get("journal_number")
                logger.info(f"✅ Journal entry created | journal_id={journal_id} | number={journal_number}")
                return True
            else:
                error = result.get("error", "Unknown error")
                logger.error(f"❌ Journal creation failed: {error}")
                return False

        except Exception as e:
            logger.error(f"❌ Error in handle_accounting_create: {e}", exc_info=True)
            return False

    async def handle_sale_completed(self, payload: Dict[str, Any]) -> bool:
        """Handle sale transaction completed event."""
        await self.initialize()

        try:
            result = await self.event_handler.handle_event(
                "transaction.sale.completed",
                payload
            )
            return result.get("success", False)

        except Exception as e:
            logger.error(f"❌ Error in handle_sale_completed: {e}", exc_info=True)
            return False

    async def handle_purchase_completed(self, payload: Dict[str, Any]) -> bool:
        """Handle purchase transaction completed event."""
        await self.initialize()

        try:
            result = await self.event_handler.handle_event(
                "transaction.purchase.completed",
                payload
            )
            return result.get("success", False)

        except Exception as e:
            logger.error(f"❌ Error in handle_purchase_completed: {e}", exc_info=True)
            return False

    async def get_dashboard_metrics(
        self,
        tenant_id: str,
        period_start: date,
        period_end: date
    ) -> Dict[str, Any]:
        """Get dashboard metrics from accounting kernel."""
        await self.initialize()

        try:
            return await self.facade.get_dashboard_metrics(
                tenant_id, period_start, period_end
            )
        except Exception as e:
            logger.error(f"❌ Error getting dashboard metrics: {e}")
            return {}


# Singleton instance
_bridge_instance: Optional[AccountingKernelBridge] = None


async def get_accounting_bridge() -> AccountingKernelBridge:
    """Get or create singleton AccountingKernelBridge instance."""
    global _bridge_instance

    if _bridge_instance is None:
        _bridge_instance = AccountingKernelBridge()
        await _bridge_instance.initialize()

    return _bridge_instance


async def close_accounting_bridge():
    """Close the singleton bridge instance."""
    global _bridge_instance

    if _bridge_instance is not None:
        await _bridge_instance.close()
        _bridge_instance = None
