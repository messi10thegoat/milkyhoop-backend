"""
Setup Orchestrator gRPC Server - THIN ROUTING LAYER
Coordinates multi-service setup workflow by delegating to specialized handlers
"""

import asyncio
import grpc
import logging
import json
import uuid
import hashlib
from datetime import datetime
from concurrent import futures

# Health check imports
from google.protobuf import empty_pb2
try:
    from google import health_pb2
    from google import health_pb2_grpc
    HEALTH_AVAILABLE = True
except ImportError:
    HEALTH_AVAILABLE = False
    logger.warning("Health proto not available, healthcheck will not work")

# Proto imports
import setup_orchestrator_pb2
import setup_orchestrator_pb2_grpc
from google.protobuf import empty_pb2

# Configuration
from config import settings

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

import sys
import os

# ============================================
# CRITICAL: Prioritize local directory for stub imports
# ============================================
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

# ============================================
# Import service modules
# ============================================
from services import SessionManager, AdaptiveResponseGenerator, ProgressCalculator, DataCleaner, QualityChecker

# ============================================
# Import handlers (modular)
# ============================================
from handlers.inventory_handler import InventoryHandler
from handlers.accounting_handler import AccountingHandler
from handlers.business_handler import BusinessHandler
from handlers.transaction_handler import TransactionHandler
from handlers.financial_handler import FinancialHandler
from handlers.general_handler import GeneralHandler


class GrpcClientManager:
    """Manages gRPC client connections to all dependent services"""
    
    def __init__(self):
        self.channels = {}
        self.stubs = {}
        
    async def initialize(self):
        """Initialize all gRPC client connections"""
        try:
            # Intent Parser (Port 7009)
            await self._create_channel('intent_parser', settings.intent_parser_address)
            
            # Business Extractor (Port 7015)
            await self._create_channel('business_extractor', settings.business_extractor_address)
            
            # Conversation Manager (Port 7016)
            await self._create_channel('conversation_manager', settings.conversation_manager_address)
            
            # RAG CRUD (Port 7001)
            await self._create_channel('ragcrud', settings.ragcrud_address)
            
            # RAG LLM (Port 7011)
            await self._create_channel('ragllm', settings.ragllm_address)

            # Transaction Service (Port 7020)                          
            await self._create_channel('transaction', settings.transaction_address)

            # Reporting Service (Port 7030)
            await self._create_channel('reporting', settings.reporting_address)

            # Inventory Service (Port 7040)
            await self._create_channel('inventory', settings.inventory_address)

            # Accounting Service (Port 7050)
            await self._create_channel('accounting', settings.accounting_address)

            logger.info("All gRPC clients initialized successfully")
            
        except Exception as e:
            logger.error(f"Failed to initialize gRPC clients: {e}")
            raise
    
    async def _create_channel(self, service_name: str, address: str):
        """Create gRPC channel and stub for a service"""
        try:
            channel = grpc.aio.insecure_channel(address)
            self.channels[service_name] = channel
            
            # Import and create appropriate stub based on service
            if service_name == 'intent_parser':
                from intent_parser_pb2_grpc import IntentParserServiceStub
                self.stubs[service_name] = IntentParserServiceStub(channel)
            elif service_name == 'business_extractor':
                from business_extractor_pb2_grpc import BusinessExtractorStub
                self.stubs[service_name] = BusinessExtractorStub(channel)
            elif service_name == 'conversation_manager':
                from conversation_manager_pb2_grpc import ConversationManagerStub
                self.stubs[service_name] = ConversationManagerStub(channel)
            elif service_name == 'ragcrud':
                from ragcrud_service_pb2_grpc import RagCrudServiceStub
                self.stubs[service_name] = RagCrudServiceStub(channel)
            elif service_name == 'ragllm':
                from ragllm_service_pb2_grpc import RagLlmServiceStub
                self.stubs[service_name] = RagLlmServiceStub(channel)
            elif service_name == 'transaction':                                      
                from transaction_service_pb2_grpc import TransactionServiceStub  
                self.stubs[service_name] = TransactionServiceStub(channel)   

            elif service_name == 'reporting':
                from reporting_service_pb2_grpc import ReportingServiceStub
                self.stubs[service_name] = ReportingServiceStub(channel)

            elif service_name == 'inventory':
                from inventory_service_pb2_grpc import InventoryServiceStub
                self.stubs[service_name] = InventoryServiceStub(channel)
            elif service_name == 'accounting':
                from accounting_service_pb2_grpc import AccountingServiceStub
                self.stubs[service_name] = AccountingServiceStub(channel)
                
            logger.info(f"Connected to {service_name} at {address}")
            
        except Exception as e:
            logger.error(f"Failed to connect to {service_name}: {e}")
            raise
    
    async def close_all(self):
        """Close all gRPC channels"""
        for service_name, channel in self.channels.items():
            await channel.close()
            logger.info(f"Closed connection to {service_name}")

class HealthServicer:
    """
    Implements grpc.health.v1.Health service for container healthchecks
    """
    
    def __init__(self):
        """Initialize with service status tracking"""
        self._status = {}
        # Register our main service as SERVING
        self._status[""] = health_pb2.HealthCheckResponse.SERVING
        self._status["setup_orchestrator.SetupOrchestrator"] = health_pb2.HealthCheckResponse.SERVING
    
    async def Check(self, request, context):
        """Health check endpoint - returns SERVING if service is healthy"""
        if not HEALTH_AVAILABLE:
            context.set_code(grpc.StatusCode.UNIMPLEMENTED)
            context.set_details("Health service not available")
            return health_pb2.HealthCheckResponse()
        
        service = request.service if hasattr(request, 'service') else ""
        status = self._status.get(service, health_pb2.HealthCheckResponse.UNKNOWN)
        
        return health_pb2.HealthCheckResponse(status=status)
    
    async def Watch(self, request, context):
        """Streaming health check"""
        if not HEALTH_AVAILABLE:
            context.set_code(grpc.StatusCode.UNIMPLEMENTED)
            context.set_details("Watch not implemented")
            return
        
        service = request.service if hasattr(request, 'service') else ""
        status = self._status.get(service, health_pb2.HealthCheckResponse.UNKNOWN)
        yield health_pb2.HealthCheckResponse(status=status)
    
    async def List(self, request, context):
        """List all registered services"""
        if not HEALTH_AVAILABLE:
            context.set_code(grpc.StatusCode.UNIMPLEMENTED)
            context.set_details("List not implemented")
            return health_pb2.ListResponse()
        
        return health_pb2.ListResponse(
            services=[
                health_pb2.ServiceStatus(service=svc)
                for svc in self._status.keys()
            ]
        )

class SetupOrchestratorServicer(setup_orchestrator_pb2_grpc.SetupOrchestratorServicer):
    """Setup Orchestrator Service Implementation - THIN ROUTING LAYER ONLY"""
    
    def __init__(self):
        self.client_manager = GrpcClientManager()
        self.session_manager = SessionManager()
        logger.info("SetupOrchestratorServicer initialized")
    
    async def initialize_clients(self):
        """Initialize all gRPC clients and session manager"""
        await self.client_manager.initialize()
        await self.session_manager.initialize()
        logger.info("All clients and session manager initialized")
    
    async def ProcessSetupChat(
        self, 
        request: setup_orchestrator_pb2.ProcessSetupChatRequest,
        context: grpc.aio.ServicerContext
    ) -> setup_orchestrator_pb2.ProcessSetupChatResponse:
        """
        Main orchestration logic - ROUTING ONLY
        Delegates all business logic to specialized handlers
        """
        start_time = datetime.now()
        trace_id = str(uuid.uuid4())[:8]
        service_calls = []
        
        logger.info(
            f"[{trace_id}] ProcessSetupChat started | "
            f"user={request.user_id} | tenant={request.tenant_id} | "
            f"session={request.session_id}"
        )
        
        # Generate lock key to prevent duplicate processing
        message_hash = hashlib.md5(request.message.encode()).hexdigest()[:8]
        lock_key = f"request_lock:{request.session_id}:{message_hash}"
        
        # Try to acquire lock
        lock_acquired = await self.session_manager.acquire_lock(lock_key, ttl=60)
        
        if not lock_acquired:
            logger.warning(
                f"[{trace_id}] ⚠️ Duplicate request detected | "
                f"session={request.session_id} | message_hash={message_hash}"
            )
            return setup_orchestrator_pb2.ProcessSetupChatResponse(
                status="processing",
                milky_response="Permintaan sedang diproses, mohon tunggu...",
                current_state="processing",
                session_id=request.session_id,
                progress_percentage=0,
                next_action="wait"
            )
        
        logger.info(f"[{trace_id}] 🔒 Lock acquired: {lock_key}")
        
        try:
            # Step 1: Get conversation context
            logger.info(f"[{trace_id}] Step 1: Getting conversation context")
            ctx_start = datetime.now()
            
            from conversation_manager_pb2 import GetContextRequest
            ctx_request = GetContextRequest(session_id=request.session_id)
            
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
                f"[{trace_id}] Context retrieved | state={ctx_response.current_state} | "
                f"duration={ctx_duration:.0f}ms"
            )
            
            # Step 2: Classify intent
            logger.info(f"[{trace_id}] Step 2: Classifying intent")
            intent_start = datetime.now()
            
            from intent_parser_pb2 import ClassifyIntentRequest
            intent_request = ClassifyIntentRequest(
                message=request.message,
                context=ctx_response.extracted_data_json
            )
            
            intent_response = await self.client_manager.stubs['intent_parser'].ClassifyIntent(
                intent_request
            )
            
            intent_duration = (datetime.now() - intent_start).total_seconds() * 1000
            service_calls.append({
                "service_name": "intent_parser",
                "method": "ClassifyIntent",
                "duration_ms": int(intent_duration),
                "status": "success"
            })
            
            logger.info(
                f"[{trace_id}] Intent classified | intent={intent_response.intent} | "
                f"confidence={intent_response.confidence:.2f} | "
                f"duration={intent_duration:.0f}ms"
            )
            
            # ============================================
            # ROUTING LOGIC - Delegate to handlers
            # ============================================
            
            # Welcome trigger
            if request.message == "__WELCOME__":
                response = await GeneralHandler.handle_welcome(
                    request, ctx_response, trace_id, service_calls, progress, self.client_manager
                )
            
            # Business setup intents
            elif intent_response.intent == "business_setup":
                response = await BusinessHandler.handle_business_setup(
                    request, ctx_response, intent_response, 
                    trace_id, service_calls, progress, self.session_manager, self.client_manager
                )
            elif intent_response.intent == "faq_create":
                response = await BusinessHandler.handle_faq_create(
                    request, ctx_response, intent_response,
                    trace_id, service_calls, progress, self.client_manager
                )
            elif intent_response.intent == "confirm_setup":
                response = await BusinessHandler.handle_confirm_setup(
                    request, ctx_response, intent_response,
                    trace_id, service_calls, progress, self.client_manager
                )
            
            # Financial management intents
            elif intent_response.intent == "transaction_record":
                response = await TransactionHandler.handle_transaction_record(
                    request, ctx_response, intent_response,
                    trace_id, service_calls, progress, message_hash, self.client_manager
                )
            elif intent_response.intent == "financial_report":
                response = await FinancialHandler.handle_financial_report(
                    request, ctx_response, intent_response,
                    trace_id, service_calls, progress, self.client_manager
                )
            
            # Financial analytics intents (Phase 2)
            elif intent_response.intent == "top_products":
                response = await FinancialHandler.handle_top_products_query(
                    request, ctx_response, intent_response,
                    trace_id, service_calls, progress, self.client_manager
                )
            elif intent_response.intent == "low_sell_products":
                response = await FinancialHandler.handle_low_sell_products_query(
                    request, ctx_response, intent_response,
                    trace_id, service_calls, progress, self.client_manager
                )
            
            # Inventory intents
            elif intent_response.intent == "inventory_query":
                response = await InventoryHandler.handle_inventory_query(
                    request, ctx_response, intent_response,
                    trace_id, service_calls, progress, self.client_manager
                )
            elif intent_response.intent == "inventory_update":
                response = await InventoryHandler.handle_inventory_update(
                    request, ctx_response, intent_response,
                    trace_id, service_calls, progress, self.client_manager
                )
            
            # Accounting intents
            elif intent_response.intent == "accounting_query":
                response = await AccountingHandler.handle_accounting_query(
                    request, ctx_response, intent_response,
                    trace_id, service_calls, progress, self.client_manager
                )
            
            # General fallback
            else:
                response = await GeneralHandler.handle_general(
                    request, ctx_response, intent_response,
                    trace_id, service_calls, progress, self.client_manager
                )
            
            # Add metadata
            processing_time = (datetime.now() - start_time).total_seconds() * 1000
            
            metadata = setup_orchestrator_pb2.ResponseMetadata(
                trace_id=trace_id,
                processing_time_ms=int(processing_time),
                timestamp=datetime.now().isoformat(),
                service_version=settings.service_version,
                service_calls=[
                    setup_orchestrator_pb2.ServiceCall(**call) 
                    for call in service_calls
                ]
            )
            response.metadata.CopyFrom(metadata)
            
            logger.info(
                f"[{trace_id}] ProcessSetupChat completed | "
                f"status={response.status} | "
                f"total_time={processing_time:.0f}ms"
            )
            return response       
                 
        except Exception as e:
            logger.error(f"[{trace_id}] ProcessSetupChat failed: {e}", exc_info=True)
            
            error = setup_orchestrator_pb2.ErrorDetails(
                code="ORCHESTRATION_ERROR",
                message=str(e),
                service="setup_orchestrator",
                details="See logs for full stack trace"
            )
            
            return setup_orchestrator_pb2.ProcessSetupChatResponse(
                status="error",
                milky_response="Maaf, ada masalah teknis. Coba lagi ya!",
                session_id=request.session_id,
                progress_percentage=0,
                error=error
            )
        
        finally:
            # Always release lock after processing
            await self.session_manager.release_lock(lock_key)
            logger.info(f"[{trace_id}] 🔓 Lock released: {lock_key}")
    
    async def HealthCheck(
        self,
        request: empty_pb2.Empty,
        context: grpc.aio.ServicerContext
    ) -> empty_pb2.Empty:
        """Health check endpoint"""
        return empty_pb2.Empty()

async def serve():
    """Start gRPC server"""
    server = grpc.aio.server(futures.ThreadPoolExecutor(max_workers=10))
    
    servicer = SetupOrchestratorServicer()
    await servicer.initialize_clients()
    
    setup_orchestrator_pb2_grpc.add_SetupOrchestratorServicer_to_server(
        servicer, server
    )
    
    # Register health service for healthcheck
    if HEALTH_AVAILABLE:
        health_servicer = HealthServicer()
        health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)
        logger.info("Health service registered for healthcheck")
    else:
        logger.warning("Health service not available - healthcheck will fail")

    listen_addr = f'0.0.0.0:{settings.grpc_port}'
    server.add_insecure_port(listen_addr)
    
    logger.info(f"Starting Setup Orchestrator gRPC server on {listen_addr}")
    await server.start()
    
    try:
        await server.wait_for_termination()
    except KeyboardInterrupt:
        logger.info("Shutting down gracefully...")
        await servicer.client_manager.close_all()
        await server.stop(grace=5)

if __name__ == '__main__':
    asyncio.run(serve())