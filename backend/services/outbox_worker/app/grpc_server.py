"""
outbox_worker/app/grpc_server.py

gRPC Server for OutboxWorker Service
Serves HealthCheck, ProcessOutbox, and GetWorkerStatus RPCs
Starts background worker thread for polling outbox table

Author: MilkyHoop Team
Version: 1.0.0
"""

import asyncio
import logging
import os
import signal
import time
from concurrent import futures
from datetime import datetime

import grpc
from grpc_reflection.v1alpha import reflection

# Import generated proto stubs
import outbox_worker_pb2
import outbox_worker_pb2_grpc

# Import outbox processor
from workers.outbox_processor import OutboxProcessor


# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


class OutboxWorkerServicer(outbox_worker_pb2_grpc.OutboxWorkerServicer):
    """Implementation of OutboxWorker gRPC service."""
    
    def __init__(self, processor: OutboxProcessor):
        """
        Initialize servicer with outbox processor.
        
        Args:
            processor: OutboxProcessor instance
        """
        self.processor = processor
        logger.info("OutboxWorkerServicer initialized")
    
    async def HealthCheck(self, request, context):
        """Health check endpoint."""
        metrics = self.processor.get_metrics()
        
        return outbox_worker_pb2.HealthCheckResponse(
            status="healthy" if self.processor.is_running else "unhealthy",
            service_name="OutboxWorker",
            timestamp=int(time.time()),
            metrics=outbox_worker_pb2.WorkerMetrics(
                total_processed=metrics["total_processed"],
                total_failed=metrics["total_failed"],
                pending_count=0,  # TODO: Query actual pending count
                retry_count=0,    # TODO: Query actual retry count
                avg_processing_time_ms=0.0  # TODO: Calculate actual avg
            )
        )
    
    async def ProcessOutbox(self, request, context):
        """
        Manual trigger for processing outbox events.
        Useful for testing or admin operations.
        """
        try:
            batch_size = request.batch_size or 10
            force_retry = request.force_retry
            
            logger.info(f"📥 Manual ProcessOutbox triggered | batch_size={batch_size} | force_retry={force_retry}")
            
            # TODO: Implement manual processing logic
            # For now, return mock response
            
            return outbox_worker_pb2.ProcessOutboxResponse(
                success=True,
                processed_count=0,
                failed_count=0,
                message="Manual processing not yet implemented",
                events=[]
            )
        
        except Exception as e:
            logger.error(f"❌ Error in ProcessOutbox: {e}", exc_info=True)
            return outbox_worker_pb2.ProcessOutboxResponse(
                success=False,
                processed_count=0,
                failed_count=0,
                message=f"Error: {str(e)}",
                events=[]
            )
    
    async def GetWorkerStatus(self, request, context):
        """Get current worker status and metrics."""
        metrics = self.processor.get_metrics()
        
        return outbox_worker_pb2.WorkerStatusResponse(
            is_running=metrics["is_running"],
            started_at=int(metrics["started_at"]) if metrics["started_at"] else 0,
            last_poll_at=int(metrics["last_poll_at"]) if metrics["last_poll_at"] else 0,
            poll_interval_sec=metrics["poll_interval"],
            metrics=outbox_worker_pb2.WorkerMetrics(
                total_processed=metrics["total_processed"],
                total_failed=metrics["total_failed"],
                pending_count=0,  # TODO: Query actual pending count
                retry_count=0,    # TODO: Query actual retry count
                avg_processing_time_ms=0.0  # TODO: Calculate actual avg
            )
        )


async def serve():
    """Start gRPC server and background worker."""
    # Configuration from environment
    grpc_port = int(os.getenv("GRPC_PORT", "5060"))
    poll_interval = int(os.getenv("POLL_INTERVAL", "2"))
    batch_size = int(os.getenv("BATCH_SIZE", "10"))
    max_retries = int(os.getenv("MAX_RETRIES", "3"))
    
    inventory_host = os.getenv("INVENTORY_SERVICE_HOST", "inventory_service")
    inventory_port = int(os.getenv("INVENTORY_SERVICE_PORT", "7040"))
    
    accounting_host = os.getenv("ACCOUNTING_SERVICE_HOST", "accounting_service")
    accounting_port = int(os.getenv("ACCOUNTING_SERVICE_PORT", "7050"))
    
    logger.info("═══════════════════════════════════════════════════════════")
    logger.info("🚀 STARTING OUTBOX WORKER SERVICE")
    logger.info("═══════════════════════════════════════════════════════════")
    logger.info(f"gRPC Port: {grpc_port}")
    logger.info(f"Poll Interval: {poll_interval}s")
    logger.info(f"Batch Size: {batch_size}")
    logger.info(f"Max Retries: {max_retries}")
    logger.info(f"Inventory Service: {inventory_host}:{inventory_port}")
    logger.info(f"Accounting Service: {accounting_host}:{accounting_port}")
    logger.info("═══════════════════════════════════════════════════════════")
    
    # Create outbox processor
    processor = OutboxProcessor(
        poll_interval=poll_interval,
        batch_size=batch_size,
        max_retries=max_retries,
        inventory_service_host=inventory_host,
        inventory_service_port=inventory_port,
        accounting_service_host=accounting_host,
        accounting_service_port=accounting_port
    )
    
    # Create gRPC server
    server = grpc.aio.server(
        futures.ThreadPoolExecutor(max_workers=10),
        options=[
            ('grpc.max_send_message_length', 50 * 1024 * 1024),  # 50MB
            ('grpc.max_receive_message_length', 50 * 1024 * 1024),  # 50MB
        ]
    )
    
    # Add servicer
    outbox_worker_pb2_grpc.add_OutboxWorkerServicer_to_server(
        OutboxWorkerServicer(processor), server
    )
    
    # Enable reflection for debugging
    service_names = (
        outbox_worker_pb2.DESCRIPTOR.services_by_name['OutboxWorker'].full_name,
        reflection.SERVICE_NAME,
    )
    reflection.enable_server_reflection(service_names, server)
    
    # Bind to port
    server.add_insecure_port(f'[::]:{grpc_port}')
    
    # Start gRPC server
    await server.start()
    logger.info(f"✅ gRPC server started on port {grpc_port}")
    
    # Start background worker
    worker_task = asyncio.create_task(processor.start())
    logger.info("✅ Background worker started")
    
    # Setup graceful shutdown
    async def shutdown(sig):
        logger.info(f"🛑 Received signal {sig}, initiating graceful shutdown...")
        
        # Stop worker
        await processor.stop()
        
        # Stop gRPC server
        await server.stop(grace=5)
        
        logger.info("✅ Shutdown complete")
    
    # Register signal handlers
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(shutdown(s)))
    
    logger.info("═══════════════════════════════════════════════════════════")
    logger.info("✅ OUTBOX WORKER SERVICE READY")
    logger.info("═══════════════════════════════════════════════════════════")
    
    # Wait for termination
    await server.wait_for_termination()


if __name__ == '__main__':
    asyncio.run(serve())