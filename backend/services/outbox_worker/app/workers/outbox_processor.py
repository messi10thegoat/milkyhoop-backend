"""
outbox_worker/app/workers/outbox_processor.py

Outbox Event Processor
Polls outbox table and processes events asynchronously
Handles inventory updates and accounting journal creation

Author: MilkyHoop Team
Version: 1.0.0

UPDATED: Now uses AccountingKernelBridge for double-entry bookkeeping
"""

import asyncio
import json
import time
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime
import os

import grpc
from milkyhoop_prisma import Prisma

# Import gRPC clients (stubs generated to outbox_worker/app/)
from app import inventory_service_pb2
from app import inventory_service_pb2_grpc

from app import accounting_service_pb2
from app import accounting_service_pb2_grpc

# Import Accounting Kernel Bridge
from app.workers.accounting_kernel_bridge import (
    AccountingKernelBridge,
    get_accounting_bridge,
    close_accounting_bridge
)


logger = logging.getLogger(__name__)


class OutboxProcessor:
    """
    Background worker that polls outbox table and processes events.
    
    Pattern: Transactional Outbox
    1. Poll unprocessed events from outbox table
    2. Process each event (call inventory/accounting services)
    3. Mark as processed or increment retry count
    """
    
    def __init__(
        self,
        poll_interval: int = 2,
        batch_size: int = 10,
        max_retries: int = 3,
        inventory_service_host: str = "inventory_service",
        inventory_service_port: int = 7040,
        accounting_service_host: str = "accounting_service",
        accounting_service_port: int = 7050,
        use_accounting_kernel: bool = True  # NEW: Toggle for new accounting kernel
    ):
        """
        Initialize outbox processor.

        Args:
            poll_interval: Seconds between polls (default: 2)
            batch_size: Max events per batch (default: 10)
            max_retries: Max retry attempts (default: 3)
            use_accounting_kernel: Use new AccountingKernel instead of gRPC (default: True)
        """
        self.poll_interval = poll_interval
        self.batch_size = batch_size
        self.max_retries = max_retries
        self.use_accounting_kernel = use_accounting_kernel

        # Metrics
        self.total_processed = 0
        self.total_failed = 0
        self.started_at = None
        self.last_poll_at = None
        self.is_running = False

        # gRPC channels
        self.inventory_channel = None
        self.accounting_channel = None
        self.inventory_stub = None
        self.accounting_stub = None

        # Accounting Kernel Bridge (new)
        self.accounting_bridge: Optional[AccountingKernelBridge] = None

        # Service addresses
        self.inventory_address = f"{inventory_service_host}:{inventory_service_port}"
        self.accounting_address = f"{accounting_service_host}:{accounting_service_port}"

        # Database client
        self.db = Prisma()

        logger.info(f"OutboxProcessor initialized | poll_interval={poll_interval}s | batch_size={batch_size} | accounting_kernel={use_accounting_kernel}")
    
    async def start(self):
        """Start the background worker."""
        try:
            # Connect to database
            await self.db.connect()
            logger.info("✅ Database connected")

            # Setup gRPC channels for inventory
            self.inventory_channel = grpc.aio.insecure_channel(self.inventory_address)
            self.inventory_stub = inventory_service_pb2_grpc.InventoryServiceStub(self.inventory_channel)

            # Setup accounting: either kernel bridge or gRPC
            if self.use_accounting_kernel:
                self.accounting_bridge = await get_accounting_bridge()
                logger.info("✅ AccountingKernelBridge initialized (double-entry bookkeeping)")
            else:
                self.accounting_channel = grpc.aio.insecure_channel(self.accounting_address)
                self.accounting_stub = accounting_service_pb2_grpc.AccountingServiceStub(self.accounting_channel)
                logger.info(f"✅ gRPC channel setup | accounting={self.accounting_address}")

            logger.info(f"✅ gRPC channels setup | inventory={self.inventory_address}")
            
            self.is_running = True
            self.started_at = time.time()
            
            logger.info("🚀 OutboxProcessor worker started")
            
            # Main polling loop
            while self.is_running:
                try:
                    await self._poll_and_process()
                except Exception as e:
                    logger.error(f"❌ Error in poll cycle: {e}", exc_info=True)
                
                # Wait before next poll
                await asyncio.sleep(self.poll_interval)
        
        except Exception as e:
            logger.error(f"❌ Fatal error in worker: {e}", exc_info=True)
            self.is_running = False
    
    async def stop(self):
        """Stop the background worker gracefully."""
        logger.info("🛑 Stopping OutboxProcessor...")
        self.is_running = False

        # Close gRPC channels
        if self.inventory_channel:
            await self.inventory_channel.close()
        if self.accounting_channel:
            await self.accounting_channel.close()

        # Close accounting kernel bridge
        if self.accounting_bridge:
            await close_accounting_bridge()

        # Disconnect database
        await self.db.disconnect()

        logger.info("✅ OutboxProcessor stopped")
    
    async def _poll_and_process(self):
        """Poll outbox table and process unprocessed events."""
        self.last_poll_at = time.time()
        
        # Fetch unprocessed events
        events = await self.db.outbox.find_many(
            where={
                'processed': False,
                'retryCount': {'lt': self.max_retries}
            },
            order={'createdAt': 'asc'},
            take=self.batch_size
        )
        
        if not events:
            logger.debug("No pending events in outbox")
            return
        
        logger.info(f"📥 Found {len(events)} events to process")
        
        # Process each event
        for event in events:
            success = await self._process_event(event)
            
            if success:
                self.total_processed += 1
            else:
                self.total_failed += 1
    
    async def _process_event(self, event) -> bool:
        """
        Process a single outbox event.
        
        Args:
            event: Outbox record from database
            
        Returns:
            True if successful, False otherwise
        """
        start_time = time.time()
        
        try:
            logger.info(f"⚙️  Processing event | id={event.id[:12]} | type={event.eventType}")
            
            # Parse payload
            payload = event.payload if isinstance(event.payload, dict) else json.loads(event.payload)
            
            # Route to appropriate handler
            if event.eventType == "inventory.update":
                success = await self._handle_inventory_update(payload)
            elif event.eventType == "accounting.create":
                success = await self._handle_accounting_create(payload)
            else:
                logger.warning(f"⚠️  Unknown event type: {event.eventType}")
                success = False
            
            # Update outbox record
            if success:
                await self.db.outbox.update(
                    where={'id': event.id},
                    data={
                        'processed': True,
                        'processedAt': datetime.utcnow()
                    }
                )
                
                elapsed = (time.time() - start_time) * 1000
                logger.info(f"✅ Event processed | id={event.id[:12]} | time={elapsed:.0f}ms")
            else:
                # Increment retry count
                await self.db.outbox.update(
                    where={'id': event.id},
                    data={
                        'retryCount': event.retryCount + 1,
                        'errorMessage': f"Failed at attempt {event.retryCount + 1}"
                    }
                )
                
                logger.warning(f"⚠️  Event failed | id={event.id[:12]} | retry={event.retryCount + 1}/{self.max_retries}")
            
            return success
        
        except Exception as e:
            logger.error(f"❌ Error processing event {event.id[:12]}: {e}", exc_info=True)
            
            # Update error message
            await self.db.outbox.update(
                where={'id': event.id},
                data={
                    'retryCount': event.retryCount + 1,
                    'errorMessage': str(e)[:500]
                }
            )
            
            return False
    
    async def _handle_inventory_update(self, payload: Dict[str, Any]) -> bool:
        """
        Handle inventory.update event.
        
        Payload structure:
        {
            "transaksi_id": "uuid",
            "tenant_id": "string",
            "items": [
                {
                    "produk_id": "uuid",
                    "jumlah_movement": float,
                    "stok_setelah": float,
                    "lokasi_gudang": "string"
                }
            ]
        }
        """
        try:
            transaksi_id = payload.get("transaksi_id")
            tenant_id = payload.get("tenant_id")
            items = payload.get("items", [])
            
            logger.info(f"📦 Updating inventory | transaksi={transaksi_id[:12]} | items={len(items)}")
            
            # Call inventory service for each item
            for item in items:
                # Build ItemInventory list
                item_inventories = [
                    inventory_service_pb2.ItemInventory(
                        produk_id=item["produk_id"],
                        jumlah_movement=item["jumlah_movement"],
                        stok_setelah=item.get("stok_setelah", 0)
                    ) for item in items
                ]

                # Build InventoryImpact
                inventory_impact = inventory_service_pb2.InventoryImpact(
                    is_tracked=True,
                    jenis_movement=payload.get("jenis_movement", "masuk"),
                    lokasi_gudang=items[0].get("lokasi_gudang", "") if items else "",
                    items_inventory=item_inventories
                )

                request = inventory_service_pb2.ProcessInventoryImpactRequest(
                    tenant_id=tenant_id,
                    transaksi_id=transaksi_id,
                    inventory_impact=inventory_impact
                )

                response = await self.inventory_stub.ProcessInventoryImpact(request, timeout=5)

                if not response.success:
                    logger.error(f"❌ Inventory update failed: {response.message}")
                    return False
            
            logger.info(f"✅ Inventory updated successfully | {len(items)} items")
            return True
        
        except grpc.RpcError as e:
            logger.error(f"❌ gRPC error calling inventory_service: {e.code()} - {e.details()}")
            return False
        except Exception as e:
            logger.error(f"❌ Error in inventory update: {e}", exc_info=True)
            return False
    
    async def _handle_accounting_create(self, payload: Dict[str, Any]) -> bool:
        """
        Handle accounting.create event.

        IDEMPOTENT: Checks if journal already exists for this transaction
        before creating a new one.

        Payload structure:
        {
            "transaksi_id": "uuid",
            "tenant_id": "string",
            "jenis_transaksi": "string",
            "total_nominal": int64,
            "periode_pelaporan": "YYYY-MM"
        }
        """
        try:
            transaksi_id = payload.get("transaksi_id")
            tenant_id = payload.get("tenant_id")

            logger.info(f"📒 Processing accounting.create | transaksi={transaksi_id[:12] if transaksi_id else 'N/A'}")

            # Use Accounting Kernel Bridge (new double-entry bookkeeping)
            if self.use_accounting_kernel and self.accounting_bridge:
                success = await self.accounting_bridge.handle_accounting_create(payload)

                if success:
                    logger.info(f"✅ Journal entry created via AccountingKernel | tx={transaksi_id[:12] if transaksi_id else 'N/A'}")
                else:
                    logger.error(f"❌ AccountingKernel failed to create journal")

                return success

            # Fallback: Use legacy gRPC accounting_service
            request = accounting_service_pb2.ProcessTransactionRequest(
                tenant_id=tenant_id,
                transaksi_id=transaksi_id
            )

            response = await self.accounting_stub.ProcessTransaction(request, timeout=5)

            if not response.success:
                logger.error(f"❌ Journal creation failed: {response.message}")
                return False

            logger.info(f"✅ Journal entry created (legacy) | journal_id={response.jurnal_entry_id}")
            return True

        except grpc.RpcError as e:
            logger.error(f"❌ gRPC error calling accounting_service: {e.code()} - {e.details()}")
            return False
        except Exception as e:
            logger.error(f"❌ Error in accounting create: {e}", exc_info=True)
            return False
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get current worker metrics."""
        return {
            "total_processed": self.total_processed,
            "total_failed": self.total_failed,
            "is_running": self.is_running,
            "started_at": self.started_at,
            "last_poll_at": self.last_poll_at,
            "poll_interval": self.poll_interval
        }