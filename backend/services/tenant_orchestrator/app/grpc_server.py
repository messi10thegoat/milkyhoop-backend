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
import conversation_service_pb2
import conversation_service_pb2_grpc
import inventory_service_pb2


# Config
from app.config import settings

# Import handlers
from app.handlers import (
    TransactionHandler,
    FinancialHandler,
    InventoryHandler,
    AccountingHandler
)
from app.handlers.correction_handler import CorrectionHandler
from app.handlers.clarification_handler import ClarificationHandler
from app.handlers.clarification_response_handler import ClarificationResponseHandler

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

            # Rule Engine (deterministic rule evaluation)
            await self._create_channel('rule_engine', settings.rule_engine_address)

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
            
            # Conversation Service (chat persistence)
            await self._create_channel('conversation', settings.conversation_address)
            
            logger.info("✅ All service clients initialized")
            
        except Exception as e:
            logger.error(f"Failed to initialize service clients: {e}")
            raise
    
    async def _create_channel(self, service_name: str, address: str):
        """Create gRPC channel and stub for a service"""
        try:
            # gRPC keepalive configuration to prevent "too_many_pings" error
            options = [
                ('grpc.keepalive_time_ms', 30000),
                ('grpc.keepalive_timeout_ms', 10000),
                ('grpc.keepalive_permit_without_calls', 1),
                ('grpc.http2.max_pings_without_data', 0),
                ('grpc.http2.min_time_between_pings_ms', 30000),
                ('grpc.http2.min_ping_interval_without_data_ms', 30000),
            ]
            channel = grpc.aio.insecure_channel(address, options=options)
            self.channels[service_name] = channel
            
            # Create appropriate stub based on service
            if service_name == 'business_parser':
                from app import business_parser_pb2_grpc
                self.stubs[service_name] = business_parser_pb2_grpc.BusinessParserStub(channel)

            elif service_name == 'rule_engine':
                from app import rule_engine_pb2_grpc
                self.stubs[service_name] = rule_engine_pb2_grpc.RuleEngineStub(channel)

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
                
            elif service_name == 'conversation':
                import conversation_service_pb2_grpc
                self.stubs[service_name] = conversation_service_pb2_grpc.ConversationServiceStub(channel)
            
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
            # STEP 1 & 2: INPUT VALIDATION (EARLY EXIT)
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
            # FORM MODE: Skip LLM parser entirely
            # Form data is structured, no need for LLM classification
            # Expected: 3-4 second saving per request
            # ============================================
            is_form_mode = request.message.startswith("[FORM]")

            # ============================================
            # PHASE 1.3: PARALLEL EXECUTION
            # Run context retrieval + intent classification concurrently
            # Expected: 500-800ms saving per request
            # ============================================

            logger.info(f"[{trace_id}] 🚀 PARALLEL: Launching context + intent classification")
            parallel_start = datetime.now()

            # Use session_id from request or generate from tenant+user
            # CRITICAL FIX: Handle empty string case - generate consistent session_id for multi-turn
            request_session_id = getattr(request, 'session_id', '')
            if request_session_id and request_session_id.strip():
                session_id = request_session_id
            else:
                # Generate consistent session_id from tenant_id + user_id for multi-turn continuity
                # This ensures same session_id across multiple requests in same conversation
                user_id = getattr(request, 'user_id', '')
                if user_id:
                    session_id = f"{request.tenant_id}_{user_id}"
                else:
                    # Fallback: use tenant_id only (less ideal but better than empty)
                    session_id = f"{request.tenant_id}_session"

            # Prepare requests
            ctx_request = conversation_manager_pb2.GetContextRequest(
                session_id=session_id
            )

            from app import business_parser_pb2

            # Execute calls - skip business_parser for FORM mode
            ctx_start = datetime.now()

            try:
                if is_form_mode:
                    # ============================================
                    # FORM MODE: Skip LLM parser, use hardcoded intent
                    # ============================================
                    logger.info(f"[{trace_id}] 📝 FORM MODE: Skipping business_parser call")

                    # Only call conversation_manager (needed for context tracking)
                    ctx_response = await self.client_manager.stubs['conversation_manager'].GetContext(ctx_request)

                    ctx_duration = (datetime.now() - ctx_start).total_seconds() * 1000
                    parallel_duration = ctx_duration

                    service_calls.append({
                        "service_name": "conversation_manager",
                        "method": "GetContext",
                        "duration_ms": int(ctx_duration),
                        "status": "success"
                    })

                    # Create a mock intent_response for FORM mode
                    # The actual entities will be extracted in transaction_handler from conversation_context
                    class MockIntentResponse:
                        def __init__(self):
                            self.intent = "transaction_record"
                            self.entities_json = "{}"  # Empty - will be extracted from form_data
                            self.confidence = 1.0

                    intent_response = MockIntentResponse()
                    progress = getattr(ctx_response, "progress_percentage", 0)
                    intent = "transaction_record"
                    entities_json = "{}"

                    logger.info(
                        f"[{trace_id}] 📝 FORM MODE COMPLETE | "
                        f"total={parallel_duration:.0f}ms | "
                        f"ctx={ctx_duration:.0f}ms | "
                        f"parser=SKIPPED | "
                        f"intent={intent}"
                    )

                else:
                    # ============================================
                    # NORMAL MODE: Run both calls in parallel
                    # ============================================
                    parser_request = business_parser_pb2.ClassifyIntentRequest(
                        tenant_id=request.tenant_id,
                        message=request.message,
                        context=request.conversation_context or ""
                    )

                    parser_start = datetime.now()

                    # asyncio.gather runs tasks in parallel
                    ctx_response, intent_response = await asyncio.gather(
                        self.client_manager.stubs['conversation_manager'].GetContext(ctx_request),
                        self.client_manager.stubs['business_parser'].ClassifyIntent(parser_request)
                    )

                    # Calculate individual durations (both finished at the same time, but track separately)
                    ctx_duration = (datetime.now() - ctx_start).total_seconds() * 1000
                    parser_duration = (datetime.now() - parser_start).total_seconds() * 1000
                    parallel_duration = (datetime.now() - parallel_start).total_seconds() * 1000

                    # Track both service calls
                    service_calls.append({
                        "service_name": "conversation_manager",
                        "method": "GetContext",
                        "duration_ms": int(ctx_duration),
                        "status": "success"
                    })

                    service_calls.append({
                        "service_name": "business_parser",
                        "method": "ClassifyIntent",
                        "duration_ms": int(parser_duration),
                        "status": "success"
                    })

                    progress = getattr(ctx_response, "progress_percentage", 0)
                    intent = intent_response.intent
                    entities_json = intent_response.entities_json

                    logger.info(
                        f"[{trace_id}] ⚡ PARALLEL COMPLETE | "
                        f"total={parallel_duration:.0f}ms | "
                        f"ctx={ctx_duration:.0f}ms | "
                        f"parser={parser_duration:.0f}ms | "
                        f"intent={intent}"
                    )

                # Note: intent might be updated by clarification_response_handler later
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
            # STEP 2.5: Check if this is a clarification response
            # ============================================
            
            milky_response = ""  # Initialize early
            
            # Only handle clarification response for transaction_record intents (not koreksi)
            if intent != "koreksi":
                clarification_result = await ClarificationResponseHandler.handle_clarification_response(
                    request, ctx_response, intent_response,
                    trace_id, service_calls, progress, self.client_manager
                )
                
                if clarification_result:
                    # Still need more clarification
                    milky_response = clarification_result
                elif clarification_result is None:
                    # Clarification response processed, entities updated
                    # Check if intent_response.intent was updated by clarification handler
                    if intent_response.intent == "transaction_record":
                        # Intent already updated by clarification handler
                        intent = "transaction_record"
                        logger.info(f"[{trace_id}] ✅ Intent updated to transaction_record by clarification handler")
                    else:
                        # Check if entities_json was modified (merged)
                        try:
                            if intent_response.entities_json:
                                merged_entities = json.loads(intent_response.entities_json)
                                if merged_entities.get("_merged_from_clarification"):
                                    logger.info(f"[{trace_id}] ✅ Clarification response merged, proceeding with transaction")
                                    # Remove merge flag
                                    merged_entities.pop("_merged_from_clarification", None)
                                    intent_response.entities_json = json.dumps(merged_entities, ensure_ascii=False)
                                    # Force intent to transaction_record
                                    intent = "transaction_record"
                                    intent_response.intent = "transaction_record"  # Also update intent_response
                                    logger.info(f"[{trace_id}] 🔄 Updated intent to transaction_record after merge")
                        except Exception as e:
                            logger.warning(f"[{trace_id}] Failed to process merged entities: {e}")

            # ============================================
            # PHASE 1.5: MULTI-TURN DRAFT MANAGEMENT
            # Check for existing draft before processing transaction
            # ============================================

            if not milky_response:  # Only if clarification handler didn't already set response
                # Check if there's an existing draft for this session
                # CRITICAL FIX: Use consistent session_id (not trace_id) for multi-turn continuity
                # session_id is same across requests in same conversation
                draft_check_start = datetime.now()

                try:
                    draft_request = conversation_manager_pb2.GetDraftRequest(
                        tenant_id=request.tenant_id,
                        session_id=session_id  # Use consistent session_id for multi-turn
                    )

                    draft_response = await self.client_manager.stubs['conversation_manager'].GetDraft(draft_request)

                    draft_check_duration = (datetime.now() - draft_check_start).total_seconds() * 1000
                    service_calls.append({
                        "service_name": "conversation_manager",
                        "method": "GetDraft",
                        "duration_ms": int(draft_check_duration),
                        "status": "success"
                    })

                    if draft_response.exists and draft_response.draft_json:
                        logger.info(f"[{trace_id}] 📋 PHASE1.5: Found existing draft, checking if continuation or new transaction")
                        
                        # CRITICAL: Check if user message is a complete new transaction
                        # If intent is transaction_record with complete entities, treat as new transaction
                        # (user might have provided complete info in one message)
                        if intent == "transaction_record" and entities_json:
                            try:
                                entities = json.loads(entities_json) if entities_json else {}
                                # Quick check: if we have jumlah AND harga_satuan AND metode_pembayaran, it's likely complete
                                items = entities.get("items", [])
                                if items and len(items) > 0:
                                    first_item = items[0]
                                    has_jumlah = first_item.get("jumlah") and first_item.get("jumlah") != 0
                                    has_harga = first_item.get("harga_satuan") and first_item.get("harga_satuan") != 0
                                    has_metode = entities.get("metode_pembayaran")
                                    
                                    if has_jumlah and has_harga and has_metode:
                                        logger.info(f"[{trace_id}] ✅ Complete transaction detected - clearing draft and proceeding")
                                        # Clear draft and proceed with normal routing
                                        delete_request = conversation_manager_pb2.DeleteDraftRequest(
                                            tenant_id=request.tenant_id,
                                            session_id=session_id
                                        )
                                        await self.client_manager.stubs['conversation_manager'].DeleteDraft(delete_request)
                                        # Continue to normal routing (don't process as continuation)
                            except Exception as e:
                                logger.warning(f"[{trace_id}] Error checking transaction completeness: {e}")
                                # Fall through to draft continuation

                        # Handle draft continuation (confirm/cancel/edit)
                        # CRITICAL: Pass consistent session_id for draft operations
                        continuation_result = await self._handle_draft_continuation(
                            request, draft_response, trace_id, service_calls, session_id
                        )

                        if continuation_result:
                            milky_response = continuation_result
                            # Skip routing logic - we're done

                except Exception as draft_error:
                    logger.error(f"[{trace_id}] Draft check failed: {draft_error}")
                    # Non-blocking: continue with normal flow if draft check fails

            # ============================================
            # STEP 3: ROUTING LOGIC - Delegate to handlers
            # Skip if milky_response already set by draft continuation
            # ============================================

            if not milky_response:
                logger.info(f"[{trace_id}] Step 2: Routing to handler for intent: {intent}")

            try:
                if milky_response:
                    # Draft continuation already handled the response, skip routing
                    logger.info(f"[{trace_id}] Skipping routing - response already set by draft continuation")
                    pass
                else:
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
                    
                    elif intent == "inventory_history":
                        milky_response = await InventoryHandler.handle_inventory_history(
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
                    
                    elif intent == "query_transaksi":
                        milky_response = await TransactionHandler.handle_query_transaksi(
                            request, ctx_response, intent_response, 
                            trace_id, service_calls, progress, self.client_manager
                        )
                    
                    elif intent in ["retur_penjualan", "retur_pembelian", "pembayaran_hutang"]:
                        # Route return and debt payment intents to transaction handler
                        milky_response = await TransactionHandler.handle_transaction_record(
                            request, ctx_response, intent_response, 
                            trace_id, service_calls, progress, self.client_manager
                        )
                    
                    elif intent == "accounting_query":
                        milky_response = await AccountingHandler.handle_accounting_query(
                            request, ctx_response, intent_response, 
                            trace_id, service_calls, progress, self.client_manager
                        )
                    
                    elif intent == "koreksi":
                        # Multi-turn conversation: correction handler
                        milky_response = await CorrectionHandler.handle_correction(
                            request, ctx_response, intent_response, 
                            trace_id, service_calls, progress, self.client_manager
                        )
                    
                    # Check for salary payment queries (even if classified as general_inquiry)
                    elif intent == "general_inquiry":
                        message_lower = request.message.lower()
                        salary_keywords = ["sudah bayar gaji", "bayar gaji siapa", "gaji siapa saja", "belum bayar gaji", "yang belum dibayar", "gaji bulan"]
                        if any(kw in message_lower for kw in salary_keywords):
                            logger.info(f"[{trace_id}] Detected salary payment query in general_inquiry, routing to financial handler")
                            milky_response = await FinancialHandler.handle_salary_payment_query(
                                request, ctx_response, intent_response,
                                trace_id, service_calls, progress, self.client_manager
                            )
                        else:
                            milky_response = "Maaf, aku belum paham. Coba tanya tentang laporan, stok, atau transaksi?"
                    
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
            
            # ============================================
            # STEP 5: Save message to conversation_service
            # ============================================
            try:
                # Build metadata with tenant context for multi-turn conversation
                metadata = {
                    "trace_id": trace_id,
                    "total_duration_ms": int(total_duration),
                    "service_calls": [sc for sc in service_calls]
                }
                
                # Store partial transaction data if clarification was asked
                # Check if last response was a clarification question
                if milky_response and any(k in milky_response.lower() for k in ["maaf bisa dibantu", "bisa tolong sebutkan"]):
                    try:
                        # Get entities from intent_response
                        clarification_entities = json.loads(intent_response.entities_json) if intent_response.entities_json else {}
                        from app.handlers.clarification_handler import detect_missing_fields
                        missing_fields = detect_missing_fields(clarification_entities, clarification_entities.get("jenis_transaksi", ""))
                        
                        if missing_fields:
                            partial_data = {
                                "partial_entities": clarification_entities,
                                "missing_fields": missing_fields,
                                "jenis_transaksi": clarification_entities.get("jenis_transaksi", "")
                            }
                            metadata["partial_transaction_data"] = partial_data
                            
                            # Also store in in-memory cache as fallback
                            from app.handlers.clarification_response_handler import store_partial_data_in_cache
                            user_id = request.user_id if hasattr(request, 'user_id') else request.tenant_id
                            session_id = getattr(request, 'session_id', f"{request.tenant_id}_session")
                            store_partial_data_in_cache(request.tenant_id, user_id, session_id, partial_data)
                            
                            logger.info(f"[{trace_id}] 💾 Stored partial transaction data for clarification (metadata + cache)")
                    except Exception as e:
                        logger.warning(f"[{trace_id}] Failed to store partial data: {e}")
                
                # Add tenant context for transaction intents (for koreksi support)
                # Support both CREATE and UPDATE (koreksi) intents
                if intent in ["transaction_record", "retur_penjualan", "retur_pembelian", "pembayaran_hutang", "koreksi"]:
                    # Try to extract transaction_id from response
                    # Response format: 
                    # - CREATE: "✅ Transaksi dicatat! ... ID: tx_xxx..."
                    # - UPDATE: "✅ Koreksi berhasil! ... Transaksi tx_xxx... sudah diupdate ... ID: tx_xxx..."
                    import re
                    tx_id_match = re.search(r'(?:Transaksi|ID:)\s*([a-zA-Z0-9_-]+)', milky_response)
                    if tx_id_match:
                        # Extract full transaction ID (might be truncated in response)
                        tx_id_short = tx_id_match.group(1)
                        # Try to get full ID from service_calls if available
                        full_tx_id = None
                        for sc in service_calls:
                            if sc.get("service_name") == "transaction" and sc.get("transaction_id"):
                                full_tx_id = sc.get("transaction_id")
                                break
                        
                        metadata["last_transaction_id"] = full_tx_id or tx_id_short
                        metadata["last_action"] = intent
                        
                        # For koreksi, preserve previous context (jenis_transaksi, etc)
                        if intent == "koreksi":
                            # Try to get previous context from last message
                            try:
                                history_request = conversation_service_pb2.GetChatHistoryRequest(
                                    user_id=request.user_id if hasattr(request, 'user_id') else request.tenant_id,
                                    tenant_id=request.tenant_id,
                                    limit=1,
                                    offset=0
                                )
                                history_response = await self.client_manager.stubs['conversation'].GetChatHistory(
                                    history_request
                                )
                                if history_response.messages and len(history_response.messages) > 0:
                                    last_msg = history_response.messages[0]
                                    if last_msg.metadata_json:
                                        prev_metadata = json.loads(last_msg.metadata_json)
                                        # Preserve jenis_transaksi and other context from previous message
                                        metadata["last_jenis_transaksi"] = prev_metadata.get("last_jenis_transaksi", "")
                                        metadata["last_total_nominal"] = prev_metadata.get("last_total_nominal", 0)
                            except Exception as e:
                                logger.warning(f"[{trace_id}] Failed to get previous context: {e}")
                        else:
                            # For CREATE, extract from entities
                            try:
                                entities = json.loads(entities_json) if entities_json else {}
                                if isinstance(entities, dict):
                                    metadata["last_jenis_transaksi"] = entities.get("jenis_transaksi", "")
                                    metadata["last_total_nominal"] = entities.get("total_nominal", 0)
                            except:
                                pass
                        
                        logger.info(f"[{trace_id}] 💾 Saved tenant context: transaction_id={metadata.get('last_transaction_id')}, action={intent}")
                
                save_msg_request = conversation_service_pb2.SaveMessageRequest(
                    user_id=request.user_id if hasattr(request, 'user_id') else request.tenant_id,
                    tenant_id=request.tenant_id,
                    message=request.message,
                    response=milky_response,
                    intent=intent,
                    metadata_json=json.dumps(metadata)
                )
                
                save_response = await self.client_manager.stubs['conversation'].SaveMessage(
                    save_msg_request
                )
                
                logger.info(f"[{trace_id}] Message saved: id={save_response.message_id}")
                
            except Exception as save_error:
                logger.error(f"[{trace_id}] Failed to save message: {save_error}")
                # Non-blocking: don't fail the request if save fails
            
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

    # ============================================
    # PHASE 1.5: MULTI-TURN DRAFT HELPERS
    # ============================================

    async def _handle_draft_continuation(
        self,
        request,
        draft_response,
        trace_id: str,
        service_calls: list,
        session_id: str
    ) -> str:
        """
        Handle user response to draft confirmation
        Returns: response message or None to continue processing
        """
        try:
            draft_data = json.loads(draft_response.draft_json)
            user_message = request.message.lower().strip()
            # Preserve original message for product name extraction
            original_user_message = request.message.strip()

            logger.info(f"[{trace_id}] 📋 Draft continuation | message='{user_message}'")
            
            # CRITICAL: Check if user message is a NEW transaction intent
            # If user provides transaction keywords, treat as new transaction (not field answer)
            # Patterns:
            # - Complete: "jual 10 kopi @15000 tunai" → clear draft, process as new
            # - Incomplete: "jual kopi", "beli kain" → clear draft, start new multi-turn
            import re

            # Pattern 1: Complete transaction with price
            complete_tx_pattern = re.search(
                r'(?:jual|beli|bayar)\s+(\d+)\s+([a-zA-Z][a-zA-Z0-9\s]*?)\s+@\s*(?:rp\s*)?(\d+)\s*(rb|ribu|k|jt|juta)?\s*(tunai|transfer|kas|tempo|bank)?',
                user_message,
                re.IGNORECASE
            )

            # Pattern 2: New transaction intent (incomplete)
            # "jual kopi", "beli kain", "bayar gaji"
            new_tx_intent = re.search(
                r'^(?:jual|beli|bayar|catat)\s+(?:\d+\s+)?([a-zA-Z][a-zA-Z0-9\s]{2,})',
                user_message.strip(),
                re.IGNORECASE
            )

            if complete_tx_pattern or new_tx_intent:
                logger.info(f"[{trace_id}] ✅ NEW transaction detected - clearing old draft and proceeding")
                # Clear old draft and proceed with normal routing
                delete_request = conversation_manager_pb2.DeleteDraftRequest(
                    tenant_id=request.tenant_id,
                    session_id=session_id
                )
                await self.client_manager.stubs['conversation_manager'].DeleteDraft(delete_request)
                # Return None to proceed with normal routing (will create new draft or post transaction)
                return None

            # ============================================
            # SPRINT 2.1: PRODUCT SELECTION HANDLING
            # ============================================
            # CRITICAL: Check awaiting state FIRST before draft confirmation
            # This prevents "ya" from being treated as transaction confirmation
            # when it's actually ambiguous product confirmation
            awaiting_state = draft_data.get("awaiting")

            # CANCELLATION: "batal", "tidak jadi", "cancel"
            # Check this first so user can always cancel
            if any(kw in user_message for kw in ["batal", "tidak jadi", "cancel", "gak jadi"]):
                logger.info(f"[{trace_id}] ❌ User canceled draft")

                # Delete draft
                # CRITICAL FIX: Use consistent session_id for draft operations
                delete_request = conversation_manager_pb2.DeleteDraftRequest(
                    tenant_id=request.tenant_id,
                    session_id=session_id  # Use consistent session_id for multi-turn
                )
                await self.client_manager.stubs['conversation_manager'].DeleteDraft(delete_request)

                return "Oke kak, transaksi dibatalkan. Ada yang bisa dibantu lagi?"

            if awaiting_state == "product_selection":
                logger.info(f"[{trace_id}] 🔍 SPRINT 2.1: Processing product selection")

                # Get product resolution from draft
                product_resolution = draft_data.get("product_resolution", {})
                matches = product_resolution.get("matches", [])

                selected_product = None

                # Try to extract selection by NUMBER first (e.g., "1", "pilih 2", "yang kedua")
                selection_match = re.search(r'(\d+)', user_message)

                if selection_match:
                    selection_idx = int(selection_match.group(1)) - 1  # Convert to 0-based index

                    if 0 <= selection_idx < len(matches):
                        selected_product = matches[selection_idx]

                        logger.info(
                            f"[{trace_id}] ✅ SPRINT 2.1: User selected by number {selection_idx + 1} - "
                            f"{selected_product['nama_produk']}"
                        )

                # If no number match, try to match by PRODUCT NAME
                # User might say "gaming" or "macbook" instead of "1" or "2"
                if not selected_product:
                    user_lower = user_message.lower().strip()

                    # Try to find product name that matches user input
                    for match in matches:
                        product_name = match['nama_produk'].lower()
                        # Check if user message contains the product name (or significant part of it)
                        # Example: user says "gaming" → matches "laptop gaming"
                        # Example: user says "macbook" → matches "laptop MacBook"
                        if user_lower in product_name or product_name in user_lower:
                            selected_product = match
                            logger.info(
                                f"[{trace_id}] ✅ SPRINT 2.1: User selected by name '{user_message}' → "
                                f"{selected_product['nama_produk']}"
                            )
                            break

                        # Also check individual words
                        # Example: "laptop macbook" contains both "laptop" and "macbook"
                        user_words = set(user_lower.split())
                        product_words = set(product_name.split())
                        # If user mentions 2+ unique words that are in product name, likely a match
                        common_words = user_words & product_words
                        if len(common_words) >= 2:
                            selected_product = match
                            logger.info(
                                f"[{trace_id}] ✅ SPRINT 2.1: User selected by words {common_words} → "
                                f"{selected_product['nama_produk']}"
                            )
                            break

                if selected_product:
                    # Product successfully selected
                    logger.info(
                        f"[{trace_id}] ✅ SPRINT 2.1: Final selection - "
                        f"{selected_product['nama_produk']}"
                    )

                    # Update draft entities with selected product
                    entities = draft_data.get("entities", {})
                    entities['produk_id'] = selected_product['produk_id']
                    entities['nama_produk'] = selected_product['nama_produk']
                    if not entities.get('satuan'):
                        entities['satuan'] = selected_product['satuan']

                    # Remove product resolution state
                    draft_data.pop("product_resolution", None)
                    draft_data.pop("awaiting", None)

                    # Check if transaction is now complete
                    from backend.services.tenant_orchestrator.app.services.field_validator import field_validator

                    jenis_transaksi = draft_data.get("jenis_transaksi", "")
                    missing_fields = field_validator.detect_missing_fields(jenis_transaksi, entities)

                    if missing_fields:
                        # Still missing fields, continue multi-turn
                        logger.info(f"[{trace_id}] 🔄 SPRINT 2.1: Product selected, but still missing: {missing_fields}")

                        draft_data["entities"] = entities
                        draft_data["missing_fields"] = missing_fields
                        draft_data["asking_for_field"] = missing_fields[0]

                        # Save updated draft
                        save_request = conversation_manager_pb2.SaveDraftRequest(
                            tenant_id=request.tenant_id,
                            session_id=session_id,
                            draft_json=json.dumps(draft_data, ensure_ascii=False)
                        )
                        await self.client_manager.stubs['conversation_manager'].SaveDraft(save_request)

                        # Ask next question
                        question = field_validator.generate_question(jenis_transaksi, missing_fields[0])
                        return question
                    else:
                        # Transaction complete, show confirmation
                        logger.info(f"[{trace_id}] ✅ SPRINT 2.1: Product selected, transaction complete!")

                        draft_data["entities"] = entities
                        draft_data["missing_fields"] = []
                        draft_data.pop("asking_for_field", None)

                        # Save updated draft
                        save_request = conversation_manager_pb2.SaveDraftRequest(
                            tenant_id=request.tenant_id,
                            session_id=session_id,
                            draft_json=json.dumps(draft_data, ensure_ascii=False)
                        )
                        await self.client_manager.stubs['conversation_manager'].SaveDraft(save_request)

                        return self._show_confirmation(draft_data)
                else:
                    # No product selected - couldn't parse user input
                    logger.warning(f"[{trace_id}] ⚠️ SPRINT 2.1: Could not understand selection: '{user_message}'")
                    return f"Maaf kak, belum paham pilihannya. Ketik angka (1, 2, ...) atau nama produknya ya"

            # ============================================
            # SPRINT 2.1: NEW PRODUCT UNIT HANDLING
            # ============================================
            # Handle user providing unit for new product (e.g., "pcs", "kg")
            elif awaiting_state == "new_product_unit":
                logger.info(f"[{trace_id}] 🆕 SPRINT 2.1: Processing new product unit")

                # SPECIAL CASE: Check if there's a closest_match (60-69% similarity)
                # User might be confirming "ya/sama" or rejecting "beda/tidak"
                product_resolution = draft_data.get("product_resolution", {})
                closest_match = product_resolution.get("closest_match")

                if closest_match:
                    # This is an ambiguous confirmation case
                    logger.info(f"[{trace_id}] 🔍 SPRINT 2.1: Ambiguous product with closest match - checking user response")

                    # Check if user confirms it's the same product
                    confirmation_keywords = ['ya', 'iya', 'yes', 'sama', 'betul', 'benar', 'oke', 'ok']
                    rejection_keywords = ['beda', 'tidak', 'bukan', 'no', 'nggak', 'gak']

                    user_lower = user_message.lower().strip()

                    # Also check for explicit product name mention (e.g., "ya, keyboard gaming")
                    mentioned_product_name = None
                    similar_name = closest_match.get('nama_produk', '')
                    if similar_name.lower() in user_lower:
                        mentioned_product_name = similar_name
                        logger.info(f"[{trace_id}] ✅ User mentioned product name: '{similar_name}'")

                    # CRITICAL FIX: Use word boundary matching to avoid false positives
                    # Example: "macbook" should NOT match "ok", "laptop" should NOT match "ya"
                    is_confirmation = any(re.search(rf'\b{re.escape(kw)}\b', user_lower) for kw in confirmation_keywords) or mentioned_product_name
                    is_rejection = any(re.search(rf'\b{re.escape(kw)}\b', user_lower) for kw in rejection_keywords)

                    # CRITICAL FIX: If user provides text that looks like a product name (not a unit, not just confirmation),
                    # and it doesn't match the suggested product, treat as implicit rejection
                    # Example: Bot suggests "laptop gaming", user says "laptop MacBook" → implicit rejection
                    unit_pattern = r'\b(pcs|kg|kilogram|gram|g|liter|l|ml|unit|buah|box|karton|dus|pack|lusin)\b'
                    looks_like_unit = bool(re.search(unit_pattern, user_lower, re.IGNORECASE))

                    # If user message has multiple words and doesn't look like confirmation/unit, likely a product name
                    is_implicit_rejection = (
                        not is_confirmation and
                        not is_rejection and
                        not looks_like_unit and
                        len(user_lower.split()) >= 2  # At least 2 words
                    )

                    if is_implicit_rejection:
                        logger.info(f"[{trace_id}] 🔍 SPRINT 2.1: Detected implicit rejection - user provided '{original_user_message}'")

                        # Extract the new product name from user message (use original casing)
                        new_product_name = original_user_message

                        # Update entities with new product name
                        entities = draft_data.get("entities", {})
                        old_name = entities.get('nama_produk', '')
                        entities['nama_produk'] = new_product_name
                        draft_data["entities"] = entities

                        # Clear product resolution state
                        draft_data.pop("product_resolution", None)

                        # Save updated draft
                        save_request = conversation_manager_pb2.SaveDraftRequest(
                            tenant_id=request.tenant_id,
                            session_id=session_id,
                            draft_json=json.dumps(draft_data, ensure_ascii=False)
                        )
                        await self.client_manager.stubs['conversation_manager'].SaveDraft(save_request)

                        logger.info(f"[{trace_id}] 📝 Product name updated: '{old_name}' → '{new_product_name}'")

                        # Still in awaiting=new_product_unit state, ask for unit
                        return f"Oke, produk '{new_product_name}' ya. Berapa satuannya kak? (contoh: pcs, kg, liter)"

                    if is_confirmation and not is_rejection:
                        # User confirms it's the same product - use existing product
                        logger.info(f"[{trace_id}] ✅ SPRINT 2.1: User confirmed same product - using '{closest_match['nama_produk']}'")

                        # Update draft entities with existing product
                        entities = draft_data.get("entities", {})
                        entities['produk_id'] = closest_match['produk_id']
                        entities['nama_produk'] = closest_match['nama_produk']
                        if not entities.get('satuan'):
                            entities['satuan'] = closest_match['satuan']

                        # Remove product resolution state
                        draft_data.pop("product_resolution", None)
                        draft_data.pop("awaiting", None)

                        # Check if transaction is now complete
                        from backend.services.tenant_orchestrator.app.services.field_validator import field_validator

                        jenis_transaksi = draft_data.get("jenis_transaksi", "")
                        missing_fields = field_validator.detect_missing_fields(jenis_transaksi, entities)

                        if missing_fields:
                            # Still missing fields, continue multi-turn
                            logger.info(f"[{trace_id}] 🔄 SPRINT 2.1: Product confirmed, but still missing: {missing_fields}")

                            draft_data["entities"] = entities
                            draft_data["missing_fields"] = missing_fields
                            draft_data["asking_for_field"] = missing_fields[0]

                            # Save updated draft
                            save_request = conversation_manager_pb2.SaveDraftRequest(
                                tenant_id=request.tenant_id,
                                session_id=session_id,
                                draft_json=json.dumps(draft_data, ensure_ascii=False)
                            )
                            await self.client_manager.stubs['conversation_manager'].SaveDraft(save_request)

                            # Ask next question
                            question = field_validator.generate_question(jenis_transaksi, missing_fields[0])
                            return question
                        else:
                            # Transaction complete, show confirmation
                            logger.info(f"[{trace_id}] ✅ SPRINT 2.1: Product confirmed, transaction complete!")

                            draft_data["entities"] = entities
                            draft_data["missing_fields"] = []
                            draft_data.pop("asking_for_field", None)

                            # Save updated draft
                            save_request = conversation_manager_pb2.SaveDraftRequest(
                                tenant_id=request.tenant_id,
                                session_id=session_id,
                                draft_json=json.dumps(draft_data, ensure_ascii=False)
                            )
                            await self.client_manager.stubs['conversation_manager'].SaveDraft(save_request)

                            return self._show_confirmation(draft_data)

                # If not confirmation/rejection, or if user rejected, proceed with unit extraction for new product

                # SPRINT 2.1: Handle rejection - user says "beda" and provides corrected product name
                # Example: "beda sih, ini laptop MacBook M2"
                # Only process rejection if closest_match was presented (is_rejection is defined)
                if closest_match and locals().get('is_rejection', False) and is_rejection:
                    logger.info(f"[{trace_id}] ❌ SPRINT 2.1: User rejected similar product - extracting new product name")

                    # Try to extract the corrected product name from the message
                    # Remove rejection keywords and common filler words
                    cleaned_message = user_message.lower()
                    for kw in rejection_keywords + ['sih', 'ini', 'adalah', 'itu', ',']:
                        cleaned_message = cleaned_message.replace(kw, ' ')

                    # Extract potential product name (words after cleaning)
                    cleaned_message = ' '.join(cleaned_message.split()).strip()

                    if cleaned_message and len(cleaned_message) > 2:
                        # User provided a new product name
                        new_product_name = cleaned_message
                        logger.info(f"[{trace_id}] ✅ SPRINT 2.1: Extracted corrected product name: '{new_product_name}'")

                        # Update entities with new product name
                        entities = draft_data.get("entities", {})
                        old_name = entities.get('nama_produk', '')
                        entities['nama_produk'] = new_product_name
                        draft_data["entities"] = entities

                        # Clear product resolution state
                        draft_data.pop("product_resolution", None)

                        # Save updated draft
                        save_request = conversation_manager_pb2.SaveDraftRequest(
                            tenant_id=request.tenant_id,
                            session_id=session_id,
                            draft_json=json.dumps(draft_data, ensure_ascii=False)
                        )
                        await self.client_manager.stubs['conversation_manager'].SaveDraft(save_request)

                        logger.info(f"[{trace_id}] 📝 Product name updated: '{old_name}' → '{new_product_name}'")

                        # Still in awaiting=new_product_unit state, keep asking for unit
                        return f"Oke, produk '{new_product_name}' ya. Berapa satuannya kak? (contoh: pcs, kg, liter)"

                # Extract unit from user message (pcs, kg, liter, etc.)
                unit_pattern = r'\b(pcs|kg|kilogram|gram|g|liter|l|ml|unit|buah|box|karton|dus|pack|lusin)\b'
                unit_match = re.search(unit_pattern, user_message, re.IGNORECASE)

                if unit_match:
                    satuan = unit_match.group(1).lower()

                    # Normalize common units
                    unit_map = {
                        'kilogram': 'kg',
                        'liter': 'l',
                        'buah': 'pcs',
                        'unit': 'pcs'
                    }
                    satuan = unit_map.get(satuan, satuan)

                    logger.info(f"[{trace_id}] ✅ SPRINT 2.1: Unit extracted - '{satuan}'")

                    # Update entities with new product unit
                    entities = draft_data.get("entities", {})
                    entities['satuan'] = satuan

                    # SPRINT 2.1 FIX: Create product in Products table FIRST, then use returned UUID
                    # This eliminates the "dangling UUID" problem
                    nama_produk = entities.get('nama_produk', '')
                    if nama_produk and not entities.get('produk_id'):
                        logger.info(f"[{trace_id}] 🆕 SPRINT 2.1: Creating product '{nama_produk}' in Products table")

                        try:
                            # Call inventory_service.CreateProduct
                            create_product_request = inventory_service_pb2.CreateProductRequest(
                                tenant_id=request.tenant_id,
                                nama_produk=nama_produk,
                                satuan=satuan,
                                kategori=""  # Optional - not collected yet
                            )

                            create_product_response = await self.client_manager.stubs['inventory'].CreateProduct(
                                create_product_request
                            )

                            if create_product_response.success:
                                product_id = create_product_response.product_id
                                entities['produk_id'] = product_id
                                logger.info(f"[{trace_id}] ✅ SPRINT 2.1: Product created - '{nama_produk}' -> {product_id}")
                            else:
                                # Fallback: use product name as ID (legacy system)
                                entities['produk_id'] = nama_produk
                                logger.warning(
                                    f"[{trace_id}] ⚠️ SPRINT 2.1: CreateProduct failed ({create_product_response.message}), "
                                    f"using product name as fallback"
                                )

                        except Exception as e:
                            # Fallback: use product name as ID (legacy system)
                            entities['produk_id'] = nama_produk
                            logger.error(
                                f"[{trace_id}] ❌ SPRINT 2.1: CreateProduct error ({str(e)}), "
                                f"using product name as fallback",
                                exc_info=True
                            )

                    # Remove new product state
                    draft_data.pop("product_resolution", None)
                    draft_data.pop("awaiting", None)

                    # Check if transaction is now complete
                    from backend.services.tenant_orchestrator.app.services.field_validator import field_validator

                    jenis_transaksi = draft_data.get("jenis_transaksi", "")
                    missing_fields = field_validator.detect_missing_fields(jenis_transaksi, entities)

                    if missing_fields:
                        # Still missing fields, continue multi-turn
                        logger.info(f"[{trace_id}] 🔄 SPRINT 2.1: Unit set, but still missing: {missing_fields}")

                        draft_data["entities"] = entities
                        draft_data["missing_fields"] = missing_fields
                        draft_data["asking_for_field"] = missing_fields[0]

                        # Save updated draft
                        save_request = conversation_manager_pb2.SaveDraftRequest(
                            tenant_id=request.tenant_id,
                            session_id=session_id,
                            draft_json=json.dumps(draft_data, ensure_ascii=False)
                        )
                        await self.client_manager.stubs['conversation_manager'].SaveDraft(save_request)

                        # Ask next question
                        question = field_validator.generate_question(jenis_transaksi, missing_fields[0])
                        return question
                    else:
                        # Transaction complete, show confirmation
                        logger.info(f"[{trace_id}] ✅ SPRINT 2.1: Unit set, transaction complete!")

                        draft_data["entities"] = entities
                        draft_data["missing_fields"] = []
                        draft_data.pop("asking_for_field", None)

                        # Save updated draft
                        save_request = conversation_manager_pb2.SaveDraftRequest(
                            tenant_id=request.tenant_id,
                            session_id=session_id,
                            draft_json=json.dumps(draft_data, ensure_ascii=False)
                        )
                        await self.client_manager.stubs['conversation_manager'].SaveDraft(save_request)

                        return self._show_confirmation(draft_data)
                else:
                    logger.warning(f"[{trace_id}] ⚠️ SPRINT 2.1: Could not parse unit")
                    return "Maaf kak, belum paham satuannya. Coba ketik pcs, kg, liter, atau satuan lainnya ya"

            # CONFIRMATION: "ya", "oke", "lanjut", "confirm"
            # Only check this if NOT in awaiting_state (to avoid conflicts with ambiguous product confirmation)
            # IMPORTANT: Use word boundaries to avoid matching substrings (e.g., "harganya" contains "ya")
            elif not awaiting_state:
                confirmation_pattern = r'\b(ya|oke|lanjut|confirm|betul|benar|iya|ok|yes)\b'
                if re.search(confirmation_pattern, user_message, re.IGNORECASE):
                    logger.info(f"[{trace_id}] ✅ User confirmed draft, posting transaction")

                    # Post transaction from draft
                    result = await self._post_transaction_from_draft(
                        request, draft_data, trace_id, service_calls, session_id
                    )
                    return result

            # FIELD ANSWER PROCESSING
            # Process when:
            # 1. awaiting_state exists (product selection, new product unit) - handled above
            # 2. OR no awaiting_state but has asking_for_field (normal field clarification)
            if not awaiting_state and draft_data.get("asking_for_field"):
                logger.info(f"[{trace_id}] 🔄 Processing field answer from user")

                # Check if draft has asking_for_field (valid draft)
                # If not, this might be a new transaction - clear invalid draft and proceed
                if not draft_data.get("asking_for_field"):
                    logger.warning(f"[{trace_id}] ⚠️ Draft exists but no asking_for_field - clearing invalid draft")
                    # Clear invalid draft
                    delete_request = conversation_manager_pb2.DeleteDraftRequest(
                        tenant_id=request.tenant_id,
                        session_id=session_id
                    )
                    await self.client_manager.stubs['conversation_manager'].DeleteDraft(delete_request)
                    # Return None to proceed with normal routing
                    return None

                # Update draft with new field value
                updated_draft = await self._process_field_answer(
                    request, draft_data, user_message, trace_id
                )

                if updated_draft:
                    # Check if complete now
                    jenis_transaksi = updated_draft.get("jenis_transaksi", "")

                    # Import field validator
                    from backend.services.tenant_orchestrator.app.services.field_validator import field_validator

                    extracted_data = updated_draft.get("entities", {})

                    if field_validator.is_complete(jenis_transaksi, extracted_data):
                        logger.info(f"[{trace_id}] ✅ Draft now complete, showing confirmation")
                        # Update draft before showing confirmation
                        updated_draft.pop("asking_for_field", None)
                        updated_draft["missing_fields"] = []

                        # Save updated draft
                        # CRITICAL FIX: Use consistent session_id for draft operations
                        save_request = conversation_manager_pb2.SaveDraftRequest(
                            tenant_id=request.tenant_id,
                            session_id=session_id,  # Use consistent session_id for multi-turn
                            draft_json=json.dumps(updated_draft, ensure_ascii=False)
                        )
                        await self.client_manager.stubs['conversation_manager'].SaveDraft(save_request)

                        return self._show_confirmation(updated_draft)
                    else:
                        # Still missing fields, ask next question
                        missing = field_validator.detect_missing_fields(jenis_transaksi, extracted_data)
                        next_field = missing[0] if missing else None

                        if next_field:
                            # Update draft with next asking field BEFORE saving
                            updated_draft["asking_for_field"] = next_field
                            updated_draft["missing_fields"] = missing

                            # Save updated draft with new asking_for_field
                            # CRITICAL FIX: Use consistent session_id for draft operations
                            save_request = conversation_manager_pb2.SaveDraftRequest(
                                tenant_id=request.tenant_id,
                                session_id=session_id,  # Use consistent session_id for multi-turn
                                draft_json=json.dumps(updated_draft, ensure_ascii=False)
                            )
                            await self.client_manager.stubs['conversation_manager'].SaveDraft(save_request)

                            question = field_validator.generate_question(jenis_transaksi, next_field)
                            return question
                        else:
                            # Shouldn't happen, but show confirmation anyway
                            return self._show_confirmation(updated_draft)
                else:
                    # Couldn't process answer
                    return "Maaf kak, aku belum paham jawabannya. Bisa coba lagi?"

        except Exception as e:
            logger.error(f"[{trace_id}] Error handling draft continuation: {e}")
            return "Maaf kak, terjadi error. Coba lagi ya!"

    async def _process_field_answer(
        self,
        request,
        draft_data: Dict,
        user_message: str,
        trace_id: str
    ) -> Dict:
        """
        Process user's answer to clarification question using simple field mapping
        Phase 1.5 - GENERIC approach for all UMKM types
        Returns: updated draft data
        """
        try:
            # Get missing field from draft
            missing_field = draft_data.get("asking_for_field")

            if not missing_field:
                logger.warning(f"[{trace_id}] No asking_for_field in draft")
                return None

            # Simple field mapping - extract value based on field type
            import re
            answer = user_message.strip()
            new_entities = {}

            if missing_field == "nama_produk":
                # Direct mapping: "kopi" → nama_produk="kopi"
                new_entities["nama_produk"] = answer
                logger.info(f"[{trace_id}] 📝 Mapped nama_produk: {answer}")

            elif missing_field == "jumlah":
                # Extract number and unit: "5 pcs" → jumlah=5, satuan="pcs"
                # ALSO handle complete format: "25 @Rp 100.000" → jumlah=25, harga_satuan=100000
                number_match = re.search(r'(\d+(?:[.,]\d+)?)', answer)
                if number_match:
                    jumlah = float(number_match.group(1).replace(',', '.'))
                    new_entities["jumlah"] = jumlah

                    # Extract unit (pcs, kg, liter, etc) - but NOT if it's "Rp" (price indicator)
                    unit_match = re.search(r'(?:\d+(?:[.,]\d+)?)\s*([a-zA-Z]+)', answer)
                    if unit_match and unit_match.group(1).lower() not in ['rp', 'at']:
                        new_entities["satuan"] = unit_match.group(1).lower()
                    else:
                        new_entities["satuan"] = "pcs"  # Default

                    # Check if answer also contains price (format: "25 @Rp 100.000")
                    if '@' in answer or 'rp' in answer.lower():
                        # Must have @ or rp followed by number (not optional) to avoid matching the quantity again
                        price_match = re.search(r'(?:@|rp)\s*(\d+(?:[.,]\d+)?)\s*(rb|ribu|k|jt|juta)?', answer.lower(), re.IGNORECASE)
                        if price_match:  # Already guaranteed to be after quantity since @ or rp is required
                            price_str = price_match.group(1).replace('.', '').replace(',', '')
                            price_num = float(price_str)
                            unit = (price_match.group(2) or "").lower()

                            if 'jt' in unit or 'juta' in unit:
                                harga_satuan = int(price_num * 1000000)
                            elif 'rb' in unit or 'ribu' in unit or 'k' in unit:
                                harga_satuan = int(price_num * 1000)
                            else:
                                harga_satuan = int(price_num)

                            new_entities["harga_satuan"] = harga_satuan
                            logger.info(f"[{trace_id}] 📝 BONUS: Also extracted harga_satuan={harga_satuan} from jumlah answer")

                    logger.info(f"[{trace_id}] 📝 Mapped jumlah={jumlah}, satuan={new_entities['satuan']}")
                else:
                    logger.warning(f"[{trace_id}] Could not extract number from: {answer}")
                    return None

            elif missing_field == "satuan":
                # Extract unit: "pcs" → satuan="pcs"
                new_entities["satuan"] = answer.lower()
                logger.info(f"[{trace_id}] 📝 Mapped satuan: {answer}")

            elif missing_field == "harga_satuan":
                # Extract price: "15000" or "15rb" or "Rp 25 ribu" → harga_satuan=15000
                # Handle formats: 15000, 15.000, 15rb, 15ribu, Rp 25 ribu, Rp25rb
                answer_clean = answer.lower().replace('.', '').replace(',', '').replace(' ', '').replace('rp', '')

                if 'rb' in answer_clean or 'ribu' in answer_clean:
                    # Extract number before 'rb'/'ribu' and multiply by 1000
                    number_match = re.search(r'(\d+(?:[.,]\d+)?)', answer_clean)
                    if number_match:
                        harga = float(number_match.group(1)) * 1000
                    else:
                        return None
                elif 'jt' in answer_clean or 'juta' in answer_clean:
                    # Extract number before 'jt'/'juta' and multiply by 1000000
                    number_match = re.search(r'(\d+(?:[.,]\d+)?)', answer_clean)
                    if number_match:
                        harga = float(number_match.group(1)) * 1000000
                    else:
                        return None
                else:
                    # Direct number
                    number_match = re.search(r'(\d+)', answer_clean)
                    if number_match:
                        harga = float(number_match.group(1))
                    else:
                        return None

                new_entities["harga_satuan"] = int(harga)

                # BUG FIX: Calculate total_harga when harga_satuan is set
                existing_entities = draft_data.get("entities", {})
                jumlah = existing_entities.get("jumlah", 0)
                if jumlah > 0:
                    total_harga = int(jumlah * harga)
                    new_entities["total_harga"] = total_harga
                    new_entities["total_nominal"] = total_harga  # Also set total_nominal
                    logger.info(f"[{trace_id}] 📝 Mapped harga_satuan: {harga}, calculated total: {total_harga}")
                else:
                    logger.info(f"[{trace_id}] 📝 Mapped harga_satuan: {harga}")

            elif missing_field == "total_nominal":
                # Same as harga_satuan parsing
                answer_clean = answer.lower().replace('.', '').replace(',', '').replace(' ', '')

                if 'rb' in answer_clean or 'ribu' in answer_clean:
                    number_match = re.search(r'(\d+(?:[.,]\d+)?)', answer_clean)
                    if number_match:
                        nominal = float(number_match.group(1)) * 1000
                    else:
                        return None
                elif 'jt' in answer_clean or 'juta' in answer_clean:
                    number_match = re.search(r'(\d+(?:[.,]\d+)?)', answer_clean)
                    if number_match:
                        nominal = float(number_match.group(1)) * 1000000
                    else:
                        return None
                else:
                    number_match = re.search(r'(\d+)', answer_clean)
                    if number_match:
                        nominal = float(number_match.group(1))
                    else:
                        return None

                new_entities["total_nominal"] = int(nominal)
                logger.info(f"[{trace_id}] 📝 Mapped total_nominal: {nominal}")

            elif missing_field == "metode_pembayaran":
                # Normalize payment method: tunai, transfer, tempo
                if any(kw in answer.lower() for kw in ["tunai", "cash", "uang"]):
                    new_entities["metode_pembayaran"] = "tunai"
                elif any(kw in answer.lower() for kw in ["transfer", "tf", "bank"]):
                    new_entities["metode_pembayaran"] = "transfer"
                elif any(kw in answer.lower() for kw in ["tempo", "hutang", "credit"]):
                    new_entities["metode_pembayaran"] = "tempo"
                else:
                    # Default: assume tunai
                    new_entities["metode_pembayaran"] = "tunai"

                logger.info(f"[{trace_id}] 📝 Mapped metode_pembayaran: {new_entities['metode_pembayaran']}")

            elif missing_field == "kategori_beban":
                # Direct mapping for expense category
                new_entities["kategori_beban"] = answer
                logger.info(f"[{trace_id}] 📝 Mapped kategori_beban: {answer}")

            elif missing_field == "keterangan":
                # Direct mapping for description
                new_entities["keterangan"] = answer
                logger.info(f"[{trace_id}] 📝 Mapped keterangan: {answer}")

            else:
                # Unknown field - log warning but try direct mapping
                logger.warning(f"[{trace_id}] Unknown field type: {missing_field}, using direct mapping")
                new_entities[missing_field] = answer

            # Merge with existing entities
            existing_entities = draft_data.get("entities", {})
            existing_entities.update(new_entities)

            # CRITICAL: Rebuild items array from flat entities if needed
            # TransactionHandler expects items array for penjualan/pembelian
            jenis_transaksi = draft_data.get("jenis_transaksi", "")
            if jenis_transaksi in ["penjualan", "pembelian"]:
                # Always rebuild items array to ensure consistency
                nama_produk = existing_entities.get("nama_produk")
                jumlah = existing_entities.get("jumlah")
                satuan = existing_entities.get("satuan", "pcs")
                harga_satuan = existing_entities.get("harga_satuan", 0)
                
                if nama_produk and jumlah and harga_satuan:
                    subtotal = int(jumlah * harga_satuan)
                    items_array = [{
                        "nama_produk": nama_produk,
                        "jumlah": int(jumlah),
                        "satuan": satuan,
                        "harga_satuan": int(harga_satuan),
                        "subtotal": subtotal
                    }]
                    existing_entities["items"] = items_array
                    # Recalculate total_nominal from items
                    existing_entities["total_nominal"] = subtotal
                    logger.info(f"[{trace_id}] 🔄 Rebuilt items array from flat entities: {items_array}")
                elif existing_entities.get("items") and len(existing_entities.get("items", [])) > 0:
                    # Items array exists, recalculate subtotal and total_nominal
                    items = existing_entities["items"]
                    total_from_items = sum(item.get("subtotal", 0) or (item.get("jumlah", 0) * item.get("harga_satuan", 0)) for item in items)
                    if total_from_items > 0:
                        existing_entities["total_nominal"] = total_from_items
                        # Update subtotal for each item if missing
                        for item in items:
                            if not item.get("subtotal"):
                                item["subtotal"] = int(item.get("jumlah", 0) * item.get("harga_satuan", 0))
                        logger.info(f"[{trace_id}] 🔄 Recalculated total_nominal from items: {total_from_items}")

            draft_data["entities"] = existing_entities
            draft_data.pop("asking_for_field", None)  # Remove asking flag

            logger.info(f"[{trace_id}] ✅ PHASE1.5: Updated draft with field mapping: {new_entities}")
            return draft_data

        except Exception as e:
            logger.error(f"[{trace_id}] Error processing field answer: {e}")
            return None

    def _show_confirmation(self, draft_data: Dict) -> str:
        """
        Format draft into confirmation message
        """
        try:
            # Import field validator
            import sys
            from backend.services.tenant_orchestrator.app.services.field_validator import field_validator

            jenis_transaksi = draft_data.get("jenis_transaksi", "")
            entities = draft_data.get("entities", {})

            return field_validator.format_confirmation_message(jenis_transaksi, entities)

        except Exception as e:
            logger.error(f"Error formatting confirmation: {e}")
            return f"Konfirmasi transaksi:\n{draft_data}\n\nLanjutkan? (ya/tidak)"

    async def _post_transaction_from_draft(
        self,
        request,
        draft_data: Dict,
        trace_id: str,
        service_calls: list,
        session_id: str
    ) -> str:
        """
        Post transaction from confirmed draft
        """
        try:
            entities = draft_data.get("entities", {})
            
            # CRITICAL: Ensure items array and total_nominal are correct before posting
            jenis_transaksi = draft_data.get("jenis_transaksi", "")
            if jenis_transaksi in ["penjualan", "pembelian"]:
                # Rebuild items array if needed
                items = entities.get("items", [])
                if not items or len(items) == 0:
                    # Build from flat fields
                    nama_produk = entities.get("nama_produk")
                    jumlah = entities.get("jumlah")
                    satuan = entities.get("satuan", "pcs")
                    harga_satuan = entities.get("harga_satuan", 0)
                    
                    if nama_produk and jumlah and harga_satuan:
                        subtotal = int(jumlah * harga_satuan)
                        items = [{
                            "nama_produk": nama_produk,
                            "jumlah": int(jumlah),
                            "satuan": satuan,
                            "harga_satuan": int(harga_satuan),
                            "subtotal": subtotal
                        }]
                        entities["items"] = items
                        entities["total_nominal"] = subtotal
                        logger.info(f"[{trace_id}] 🔄 Rebuilt items array before posting: {items}")
                else:
                    # Ensure subtotal and total_nominal are correct
                    total_from_items = sum(item.get("subtotal", 0) or (item.get("jumlah", 0) * item.get("harga_satuan", 0)) for item in items)
                    if total_from_items > 0:
                        entities["total_nominal"] = total_from_items
                        # Update subtotal for each item if missing
                        for item in items:
                            if not item.get("subtotal"):
                                item["subtotal"] = int(item.get("jumlah", 0) * item.get("harga_satuan", 0))
                        logger.info(f"[{trace_id}] 🔄 Recalculated total_nominal before posting: {total_from_items}")

            # Create a mock intent_response with complete entities
            class MockIntentResponse:
                def __init__(self, entities_dict):
                    self.entities_json = json.dumps(entities_dict, ensure_ascii=False)
                    self.intent = "transaction_record"

            intent_response = MockIntentResponse(entities)

            # Create mock ctx_response
            class MockCtxResponse:
                def __init__(self):
                    pass

            ctx_response = MockCtxResponse()

            # Call transaction handler to process the complete transaction
            from app.handlers import TransactionHandler

            result = await TransactionHandler.handle_transaction_record(
                request,
                ctx_response,
                intent_response,
                trace_id,
                service_calls,
                0,  # progress
                self.client_manager
            )

            # Delete draft after successful posting
            # CRITICAL FIX: Use consistent session_id for draft operations
            delete_request = conversation_manager_pb2.DeleteDraftRequest(
                tenant_id=request.tenant_id,
                session_id=session_id  # Use consistent session_id for multi-turn
            )
            await self.client_manager.stubs['conversation_manager'].DeleteDraft(delete_request)

            logger.info(f"[{trace_id}] ✅ Transaction posted from draft successfully")

            return result

        except Exception as e:
            logger.error(f"[{trace_id}] Error posting transaction from draft: {e}")
            return "Maaf kak, terjadi error saat menyimpan transaksi. Coba lagi ya!"

    async def HealthCheck(self, request: empty_pb2.Empty, context) -> empty_pb2.Empty:
        """Health check endpoint"""
        return empty_pb2.Empty()


# ============================================
# SERVER STARTUP
# ============================================

async def serve() -> None:
    """Start gRPC server"""

    logger.info("Starting TenantOrchestrator gRPC server...")

    # Create server with keepalive configuration
    server_options = [
        ('grpc.keepalive_time_ms', 30000),
        ('grpc.keepalive_timeout_ms', 10000),
        ('grpc.max_connection_idle_ms', 300000),         # 5min idle timeout
        ('grpc.max_connection_age_ms', 600000),          # 10min max connection age
        ('grpc.max_connection_age_grace_ms', 30000),     # 30s grace period
        ('grpc.http2.min_time_between_pings_ms', 30000), # Accept pings every 30s
        ('grpc.http2.max_ping_strikes', 3),              # Allow 3 bad pings before disconnect
    ]
    server = aio.server(options=server_options)
    
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