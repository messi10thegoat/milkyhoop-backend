"""
tenant_orchestrator/app/grpc_server.py

Tenant Orchestrator - THIN ROUTING LAYER
Coordinates tenant business operations by delegating to specialized handlers

Architecture Pattern: Same as setup_orchestrator
- Minimal logic in main servicer
- All business logic in handlers/
- Clean separation of concerns

Author: MilkyHoop Team
Version: 2.0.0 (Modular)
"""

import asyncio
import signal
import logging
import json
import uuid
from datetime import datetime
from typing import Dict, Any

import grpc
from grpc import aio
from grpc_health.v1 import health, health_pb2, health_pb2_grpc
from google.protobuf import empty_pb2, timestamp_pb2

# Proto imports
from app import tenant_orchestrator_pb2 as pb
from app import tenant_orchestrator_pb2_grpc as pb_grpc
import conversation_manager_pb2
import conversation_manager_pb2_grpc


# Config
from app.config import settings

# Import handlers
from app.handlers import (
    FinancialHandler,
    InventoryHandler,
    TransactionHandler,
    AccountingHandler
)

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(settings.SERVICE_NAME)


# ============================================
# SERVICE CLIENT MANAGER
# ============================================

class ServiceClientManager:
    """Manages gRPC clients to downstream services"""
    
    def __init__(self):
        self.channels: Dict[str, grpc.aio.Channel] = {}
        self.stubs: Dict[str, Any] = {}
        
    async def initialize(self):
        """Initialize all gRPC clients"""
        logger.info("Initializing service clients...")
        
        try:
            # Business Parser (intent classification for tenant queries)
            await self._create_channel('business_parser', settings.business_parser_address)
            
            # Transaction Service (financial transactions, analytics)
            await self._create_channel('transaction', settings.transaction_address)

            # Conversation Manager (context and history for multi-turn)
            await self._create_channel('conversation_manager', settings.conversation_manager_address)
            
            # Reporting Service (SAK EMKM reports)
            await self._create_channel('reporting', settings.reporting_address)
            
            # Inventory Service (stock management)
            await self._create_channel('inventory', settings.inventory_address)
            
            # Accounting Service (journal entries)
            await self._create_channel('accounting', settings.accounting_address)
            
            logger.info("✅ All service clients initialized")
            
        except Exception as e:
            logger.error(f"Failed to initialize service clients: {e}")
            raise
    
    async def _create_channel(self, service_name: str, address: str):
        """Create gRPC channel and stub for a service"""
        try:
            channel = grpc.aio.insecure_channel(address)
            self.channels[service_name] = channel
            
            # Create appropriate stub based on service
            if service_name == 'business_parser':
                from app import business_parser_pb2_grpc
                self.stubs[service_name] = business_parser_pb2_grpc.BusinessParserStub(channel)

            
            elif service_name == 'conversation_manager':
                import conversation_manager_pb2_grpc
                self.stubs[service_name] = conversation_manager_pb2_grpc.ConversationManagerStub(channel)
                
            elif service_name == 'transaction':
                from app import transaction_service_pb2_grpc
                self.stubs[service_name] = transaction_service_pb2_grpc.TransactionServiceStub(channel)
                
            elif service_name == 'reporting':
                from app import reporting_service_pb2_grpc
                self.stubs[service_name] = reporting_service_pb2_grpc.ReportingServiceStub(channel)
                
            elif service_name == 'inventory':
                from app import inventory_service_pb2_grpc
                self.stubs[service_name] = inventory_service_pb2_grpc.InventoryServiceStub(channel)
                
            elif service_name == 'accounting':
                from app import accounting_service_pb2_grpc
                self.stubs[service_name] = accounting_service_pb2_grpc.AccountingServiceStub(channel)
            
            logger.info(f"✅ Connected to {service_name} at {address}")
            
        except Exception as e:
            logger.error(f"Failed to create channel for {service_name}: {e}")
            raise
    
    async def close_all(self):
        """Close all gRPC channels"""
        for service_name, channel in self.channels.items():
            await channel.close()
            logger.info(f"Closed channel: {service_name}")


# ============================================
# TENANT ORCHESTRATOR SERVICER (THIN LAYER)
# ============================================

class TenantOrchestratorServicer(pb_grpc.TenantOrchestratorServicer):
    """
    Tenant Orchestrator Service - ROUTING ONLY
    All business logic delegated to handlers
    """
    
    def __init__(self):
        self.client_manager = ServiceClientManager()
        logger.info("TenantOrchestratorServicer initialized")
    
    async def initialize_clients(self):
        """Initialize all gRPC clients"""
        await self.client_manager.initialize()
        logger.info("All clients initialized and ready")
    
    async def ProcessTenantQuery(
        self, 
        request: pb.ProcessTenantQueryRequest, 
        context
    ) -> pb.ProcessTenantQueryResponse:
        """
        Main tenant query processor - ROUTING ONLY
        
        Flow:
        1. Validate request
        2. Call business_parser for intent classification
        3. Route to appropriate handler
        4. Format response
        """
        
        # Generate trace ID for request tracking
        trace_id = str(uuid.uuid4())
        start_time = datetime.now()
        service_calls = []
        
        logger.info(
            f"[{trace_id}] ProcessTenantQuery | "
            f"tenant={request.tenant_id} | "
            f"message={request.message[:50]}..."
        )
        
        try:
            # ============================================
            # STEP 1: Get Conversation Context
            # ============================================
            
            logger.info(f"[{trace_id}] Step 1: Getting conversation context")
            ctx_start = datetime.now()
            
            # Use session_id from request or generate from tenant+user
            session_id = getattr(request, 'session_id', f"{request.tenant_id}_session")
            
            ctx_request = conversation_manager_pb2.GetContextRequest(
                session_id=session_id
            )
            
            ctx_response = await self.client_manager.stubs['conversation_manager'].GetContext(
                ctx_request
            )
            
            progress = getattr(ctx_response, "progress_percentage", 0)
            
            ctx_duration = (datetime.now() - ctx_start).total_seconds() * 1000
            service_calls.append({
                "service_name": "conversation_manager",
                "method": "GetContext",
                "duration_ms": int(ctx_duration),
                "status": "success"
            })
            
            logger.info(
                f"[{trace_id}] Context retrieved | "
                f"duration={ctx_duration:.0f}ms"
            )
            
            # ============================================
            # STEP 2: Input Validation
            # ============================================
            
            if not request.tenant_id:
                logger.error(f"[{trace_id}] Missing tenant_id")
                return pb.ProcessTenantQueryResponse(
                    status="error",
                    milky_response="Error: tenant_id diperlukan.",
                    error_code="MISSING_TENANT_ID",
                    error_message="tenant_id is required",
                    trace_id=trace_id
                )
            
            if not request.message:
                logger.error(f"[{trace_id}] Missing message")
                return pb.ProcessTenantQueryResponse(
                    status="error",
                    milky_response="Error: message diperlukan.",
                    error_code="MISSING_MESSAGE",
                    error_message="message is required",
                    trace_id=trace_id
                )
            
            # ============================================
            # STEP 2: Intent Classification via business_parser
            # ============================================
            
            logger.info(f"[{trace_id}] Step 3: Calling business_parser for intent classification")
            
            try:
                parser_start = datetime.now()
                
                from app import business_parser_pb2
                
                parser_request = business_parser_pb2.ClassifyIntentRequest(
                    tenant_id=request.tenant_id,
                    message=request.message,
                    context=request.conversation_context or ""
                )
                
                intent_response = await self.client_manager.stubs['business_parser'].ClassifyIntent(
                    parser_request
                )
                
                parser_duration = (datetime.now() - parser_start).total_seconds() * 1000
                service_calls.append({
                    "service_name": "business_parser",
                    "method": "ClassifyIntent",
                    "duration_ms": int(parser_duration),
                    "status": "success"
                })
                
                intent = intent_response.intent
                entities_json = intent_response.entities_json
                logger.info(f"[{trace_id}] DEBUG - entities_json from parser: {entities_json}")
                # Parse entities for handler use
                entities = {}
                if entities_json:
                    try:
                        entities = json.loads(entities_json)
                    except:
                        entities = {}
                
                logger.info(f"[{trace_id}] Intent classified: {intent}")
                
            except Exception as e:
                logger.error(f"[{trace_id}] business_parser call failed: {e}")
                service_calls.append({
                    "service_name": "business_parser",
                    "method": "ClassifyIntent",
                    "duration_ms": 0,
                    "status": "error"
                })
                
                return pb.ProcessTenantQueryResponse(
                    status="error",
                    milky_response="Maaf, terjadi error saat memproses query kamu. Coba lagi ya!",
                    error_code="PARSER_ERROR",
                    error_message=str(e),
                    trace_id=trace_id,
                    service_calls=[self._convert_service_call(sc) for sc in service_calls]
                )
            
            # ============================================
            # STEP 3: ROUTING LOGIC - Delegate to handlers
            # ============================================
            
            logger.info(f"[{trace_id}] Step 2: Routing to handler for intent: {intent}")
            
            milky_response = ""
            
            try:
                # Financial intents
                if intent == "financial_report":
                    milky_response = await FinancialHandler.handle_financial_report(
                        request, ctx_response, intent_response, 
                        trace_id, service_calls, progress, self.client_manager
                    )
                
                elif intent == "top_products":
                    milky_response = await FinancialHandler.handle_top_products_query(
                        request, ctx_response, intent_response, 
                        trace_id, service_calls, progress, self.client_manager
                    )
                
                elif intent == "low_sell_products":
                    milky_response = await FinancialHandler.handle_low_sell_products_query(
                        request, ctx_response, intent_response, 
                        trace_id, service_calls, progress, self.client_manager
                    )
                
                # Inventory intents
                elif intent == "inventory_query":
                    milky_response = await InventoryHandler.handle_inventory_query(
                        request, ctx_response, intent_response, 
                        trace_id, service_calls, progress, self.client_manager
                    )
                
                elif intent == "inventory_update":
                    milky_response = await InventoryHandler.handle_inventory_update(
                        request, ctx_response, intent_response, 
                        trace_id, service_calls, progress, self.client_manager
                    )
                
                elif intent == "transaction_record":
                    milky_response = await TransactionHandler.handle_transaction_record(
                        request, ctx_response, intent_response, 
                        trace_id, service_calls, progress, self.client_manager
                    )
                
                elif intent == "accounting_query":
                    milky_response = await AccountingHandler.handle_accounting_query(
                        request, ctx_response, intent_response, 
                        trace_id, service_calls, progress, self.client_manager
                    )
                
                # General fallback
                else:
                    milky_response = "Maaf, aku belum paham. Coba tanya tentang laporan, stok, atau transaksi?"
                
            except Exception as e:
                logger.error(f"[{trace_id}] Handler error for intent {intent}: {e}")
                milky_response = "Maaf, terjadi error saat memproses query kamu. Tim kami sudah diberitahu."
            
            # ============================================
            # STEP 4: Build final response
            # ============================================
            
            total_duration = (datetime.now() - start_time).total_seconds() * 1000
            
            response = pb.ProcessTenantQueryResponse(
                status="success",
                milky_response=milky_response,
                intent=intent,
                entities_json=entities_json,
                service_calls=[self._convert_service_call(sc) for sc in service_calls],
                total_duration_ms=int(total_duration),
                trace_id=trace_id,
                timestamp=timestamp_pb2.Timestamp(seconds=int(datetime.now().timestamp()))
            )
            
            logger.info(f"[{trace_id}] Request completed in {int(total_duration)}ms")
            
            return response
            
        except Exception as e:
            logger.error(f"[{trace_id}] Unexpected error: {e}", exc_info=True)
            
            return pb.ProcessTenantQueryResponse(
                status="error",
                milky_response="Maaf, terjadi error sistem. Coba lagi dalam beberapa saat ya!",
                error_code="INTERNAL_ERROR",
                error_message=str(e),
                trace_id=trace_id,
                service_calls=[self._convert_service_call(sc) for sc in service_calls]
            )
    
    def _convert_service_call(self, call_dict: Dict) -> pb.ServiceCall:
        """Convert dict to ServiceCall proto message"""
        return pb.ServiceCall(
            service_name=call_dict.get("service_name", ""),
            method=call_dict.get("method", ""),
            duration_ms=call_dict.get("duration_ms", 0),
            status=call_dict.get("status", "unknown")
        )
    
    async def HealthCheck(self, request: empty_pb2.Empty, context) -> empty_pb2.Empty:
        """Health check endpoint"""
        return empty_pb2.Empty()


# ============================================
# SERVER STARTUP
# ============================================

async def serve() -> None:
    """Start gRPC server"""
    
    logger.info("Starting TenantOrchestrator gRPC server...")
    
    # Create server
    server = aio.server()
    
    # Add servicer
    servicer = TenantOrchestratorServicer()
    await servicer.initialize_clients()
    
    pb_grpc.add_TenantOrchestratorServicer_to_server(servicer, server)
    
    # Add health check
    health_servicer = health.HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)
    health_servicer.set("", health_pb2.HealthCheckResponse.SERVING)
    
    # Listen on port
    listen_addr = f"0.0.0.0:{settings.grpc_port}"
    server.add_insecure_port(listen_addr)
    
    logger.info(f"🚀 TenantOrchestrator gRPC server listening on port {settings.grpc_port}")
    logger.info(f"📊 Connected services: business_parser, transaction, reporting, inventory, accounting")
    logger.info(f"🎯 Handlers: Financial, Inventory, Transaction, Accounting, General")
    
    # Start server
    await server.start()
    
    # Graceful shutdown handler
    stop_event = asyncio.Event()
    
    def handle_shutdown(*_):
        logger.info("🛑 Shutdown signal received. Cleaning up...")
        stop_event.set()
    
    signal.signal(signal.SIGTERM, handle_shutdown)
    signal.signal(signal.SIGINT, handle_shutdown)
    
    # Wait for termination
    try:
        await stop_event.wait()
    finally:
        logger.info("Stopping server...")
        await server.stop(grace=5)
        
        logger.info("Closing service clients...")
        await servicer.client_manager.close_all()
        
        logger.info("✅ Shutdown complete")


if __name__ == "__main__":
    asyncio.run(serve())