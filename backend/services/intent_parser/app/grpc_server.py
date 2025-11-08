#!/usr/bin/env python3
"""
Intent Parser gRPC Server - Dual Domain Version
Handles intent classification for:
1. Chatbot Setup (FAQ management, business config)
2. Financial Management (transactions, reports, inventory)

Field mapping: 100% aligned with transaction_service.proto, 
               inventory_service.proto, reporting_service.proto
"""

import os
import sys
import json
import logging
import asyncio
import re
from typing import Dict, Any, Optional

import grpc
from grpc import aio
from google.protobuf import empty_pb2
from grpc_health.v1 import health_pb2, health_pb2_grpc

# Import proto definitions
import intent_parser_pb2 as pb
import intent_parser_pb2_grpc as pb_grpc

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Try to import LLM parser
try:
    from services.llm_parser import parse_intent_entities
    LLM_AVAILABLE = True
    logger.info("LLM parser loaded successfully")
except ImportError as e:
    logger.warning(f"LLM parser not available, using fallback: {e}")
    LLM_AVAILABLE = False


class IntentParserService(pb_grpc.IntentParserServiceServicer):
    """
    Intent Parser gRPC Service Implementation - Dual Domain
    
    Features:
    - Context-aware LLM classification (GPT-4o)
    - Dual domain: Chatbot Setup + Financial Management
    - Field mapping aligned with SAK EMKM proto schemas
    - Rule-based fallback for reliability
    - Confidence scoring per domain
    """
    
    def __init__(self):
        """Initialize service with dual-domain intent definitions"""
        
        # DOMAIN 1: CHATBOT SETUP INTENTS
        self.chatbot_setup_intents = {
            "business_setup",      # Business information gathering
            "confirm_setup",       # User confirmation (yes/ok/proceed)
            "faq_create",          # Create new FAQ
            "faq_read",            # Read/query FAQ
            "faq_update",          # Update existing FAQ
            "faq_delete",          # Delete FAQ
            "faq_query",           # Search FAQ
        }
        
        # DOMAIN 2: FINANCIAL MANAGEMENT INTENTS
        self.financial_intents = {
            "transaction_record",  # Record financial transaction
            "financial_report",    # Request financial reports
            "inventory_query",     # Check stock levels
            "inventory_update",    # Manual stock adjustment
            "accounting_query",    # Check journal entries
            "top_products",        # Top selling products analytics
            "low_sell_products",   # Low-sell products analytics
        }
        
        # GENERAL INTENTS
        self.general_intents = {
            "general_chat",        # General conversation
            "others"               # Unclear/unknown intent
        }
        
        # Combined valid intents
        self.valid_intents = (
            self.chatbot_setup_intents | 
            self.financial_intents | 
            self.general_intents
        )
        
        logger.info(f"Intent Parser initialized | LLM: {LLM_AVAILABLE}")
        logger.info(f"Chatbot intents: {self.chatbot_setup_intents}")
        logger.info(f"Financial intents: {self.financial_intents}")
    
    async def ClassifyIntent(self, request, context):
        """
        Classify user intent from message with conversation context
        
        Args:
            request: ClassifyIntentRequest with message and optional context
            context: gRPC context
            
        Returns:
            ClassifyIntentResponse with intent, confidence, and entities
        """
        try:
            logger.info(f"ClassifyIntent called | message='{request.message[:100]}...'")
            
            # SPECIAL CASE 1: Welcome trigger
            if request.message == "__WELCOME__":
                logger.info("Welcome trigger detected")
                return pb.ClassifyIntentResponse(
                    status="success",
                    intent="general_chat",
                    confidence=1.0,
                    entities_json=json.dumps({
                        "query_type": "welcome",
                        "entities": {"trigger": "system_welcome"}
                    })
                )
            
            # SPECIAL CASE 2: Bulk article (FAQ creation)
            if len(request.message) > 1000:
                logger.info(f"Bulk article detected | length={len(request.message)}")
                return pb.ClassifyIntentResponse(
                    status="success",
                    intent="faq_create",
                    confidence=0.95,
                    entities_json=json.dumps({
                        "entities": {
                            "FAQ": {
                                "article_type": "bulk",
                                "word_count": len(request.message.split()),
                                "content": request.message[:200] + "..."
                            }
                        }
                    })
                )
            
            # PRIMARY: LLM-based classification with dual-domain awareness
            if LLM_AVAILABLE:
                try:
                    logger.info("Using LLM classification (GPT-4o)")
                    
                    # Extract context if provided
                    context_str = None
                    if request.context and request.context.strip():
                        context_str = request.context.strip()
                        logger.info(f"Context provided | length={len(context_str)}")
                    
                    # Call LLM parser with context parameter
                    llm_result = parse_intent_entities(request.message, context=context_str)
                    
                    raw_intent = llm_result.get("intent", "general_chat")
                    raw_entities = llm_result.get("entities", {})
                    
                    # Validate and map intent
                    intent = self._validate_and_map_intent(raw_intent, request.message)
                    
                    # Pattern-based override for missed financial intents
                    if intent == "general_chat":
                        detected_intent = self._detect_intent_patterns(request.message)
                        if detected_intent and detected_intent != "general_chat":
                            logger.info(f"PATTERN OVERRIDE: '{request.message[:50]}...' -> {detected_intent}")
                            intent = detected_intent
                    
                    # Calculate confidence based on intent type
                    confidence = self._calculate_confidence(intent, request.message, raw_entities)
                    
                    logger.info(
                        f"LLM classification | raw_intent={raw_intent} | "
                        f"mapped_intent={intent} | confidence={confidence:.2f}"
                    )
                    
                    return pb.ClassifyIntentResponse(
                        status="success",
                        intent=intent,
                        confidence=confidence,
                        entities_json=json.dumps({"entities": raw_entities})
                    )
                    
                except Exception as e:
                    logger.error(f"LLM classification failed: {e}")
                    logger.info("Falling back to rule-based classification")
                    # Fall through to rule-based fallback
            
            # FALLBACK: Rule-based classification with dual-domain support
            logger.info("Using rule-based fallback classification")
            
            message = request.message.lower().strip()
            
            # Detect intent using patterns
            intent = self._detect_intent_patterns(message)
            
            # Calculate confidence (lower for rule-based)
            if intent == "general_chat":
                confidence = 0.65
            elif intent in self.financial_intents:
                confidence = 0.75
            elif intent in self.chatbot_setup_intents:
                confidence = 0.75
            elif intent == "confirm_setup":
                confidence = 0.85
            else:
                confidence = 0.70
            
            # Extract basic entities (limited in fallback mode)
            entities = {"entities": {}}
            
            logger.info(f"Rule-based classification | intent={intent} | confidence={confidence:.2f}")
            
            return pb.ClassifyIntentResponse(
                status="success",
                intent=intent,
                confidence=confidence,
                entities_json=json.dumps(entities)
            )
            
        except Exception as e:
            logger.error(f"ClassifyIntent error: {e}")
            import traceback
            traceback.print_exc()
            
            # Return error response
            return pb.ClassifyIntentResponse(
                status="error",
                intent="general_chat",
                confidence=0.0,
                entities_json=json.dumps({"error": str(e)})
            )
    
    def _detect_intent_patterns(self, message: str) -> str:
        """
        Detect intent using rule-based patterns (fallback + override)
        
        Args:
            message: User message (already lowercased)
            
        Returns:
            Detected intent string
        """
        message = message.lower().strip()
        
        # PRIORITY 1: Confirmation patterns
        if message in ["ya", "yes", "ok", "oke", "okay", "lanjut", "proceed", "iya", "siap"]:
            return "confirm_setup"
        
        # PRIORITY 2: Financial transaction patterns (HIGH PRIORITY)
        # Pattern: jual/beli/bayar + nominal + optional pihak
        transaction_keywords = ["jual", "beli", "bayar", "terima", "transfer", "keluar", "masuk"]
        if any(kw in message for kw in transaction_keywords):
            # Check for nominal indicators
            if re.search(r'\d+', message) or 'rb' in message or 'ribu' in message or 'juta' in message:
                return "transaction_record"
        
        # PRIORITY 3: Financial report patterns
        report_keywords = ["untung", "rugi", "laba", "neraca", "kas", "aset", "laporan"]
        if any(kw in message for kw in report_keywords):
            return "financial_report"
        
        # PRIORITY 4: Inventory query patterns
        inventory_query_keywords = ["stok", "stock", "cek stok", "berapa stok", "persediaan"]
        if any(kw in message for kw in inventory_query_keywords):
            if "tambah" in message or "kurang" in message or "set" in message or "update" in message:
                return "inventory_update"
            else:
                return "inventory_query"
        
        # PRIORITY 5: Accounting query patterns
        accounting_keywords = ["jurnal", "bagan akun", "chart of account", "debit", "kredit"]
        if any(kw in message for kw in accounting_keywords):
            return "accounting_query"
        
        # PRIORITY 6: Business setup patterns
        business_keywords = ["cafe", "toko", "warung", "resto", "salon", "usaha", "bisnis"]
        action_keywords = ["buka", "operasional", "harga", "mulai"]
        possessive_keywords = ["saya", "gue", "aku", "punya"]
        
        has_business = any(kw in message for kw in business_keywords)
        has_action = any(kw in message for kw in action_keywords)
        has_possessive = any(kw in message for kw in possessive_keywords)
        
        if has_business and (has_action or has_possessive):
            return "business_setup"
        
        if any(word in message for word in ["bikin", "setup", "daftar", "register"]):
            return "business_setup"
        
        # PRIORITY 7: FAQ patterns
        if "faq" in message:
            if any(word in message for word in ["tambah", "buat", "create"]):
                return "faq_create"
            elif any(word in message for word in ["update", "ubah", "edit"]):
                return "faq_update"
            elif any(word in message for word in ["hapus", "delete", "buang"]):
                return "faq_delete"
            elif any(word in message for word in ["cek", "lihat", "tampilkan", "baca"]):
                return "faq_query"
        
        # PRIORITY 8: Default fallback
        return "general_chat"
    
    def _validate_and_map_intent(self, raw_intent: str, message: str) -> str:
        """
        Validate LLM intent and map to dual-domain schema
        
        Args:
            raw_intent: Intent from LLM
            message: Original user message
            
        Returns:
            Valid intent from dual-domain schema
        """
        # Normalize intent name
        raw_intent = raw_intent.lower().strip()
        
        # INTENT NAME MAPPING (Handle LLM variations)
        intent_mapping = {
            # Chatbot setup
            "confirmation": "confirm_setup",
            "setup": "business_setup",
            "business": "business_setup",
            "create_faq": "faq_create",
            "update_faq": "faq_update",
            "delete_faq": "faq_delete",
            "query_faq": "faq_query",
            "read_faq": "faq_query",
            
            # Financial
            "transaction": "transaction_record",
            "record_transaction": "transaction_record",
            "financial_transaction": "transaction_record",
            "report": "financial_report",
            "get_report": "financial_report",
            "inventory": "inventory_query",
            "check_stock": "inventory_query",
            "stock_query": "inventory_query",
            "update_stock": "inventory_update",
            "adjust_stock": "inventory_update",
            "journal": "accounting_query",
            "accounting": "accounting_query",
            
            # General
            "chat": "general_chat",
            "greeting": "general_chat"
        }
        
        # Apply mapping if needed
        mapped_intent = intent_mapping.get(raw_intent, raw_intent)
        
        # VALIDATION: Check if intent is valid
        if mapped_intent in self.valid_intents:
            # Additional validation for confirm_setup
            if mapped_intent == "confirm_setup" and len(message) > 20:
                logger.info(f"Downgrading confirm_setup to general_chat (message too long: {len(message)} chars)")
                return "general_chat"
            
            return mapped_intent
        
        # UNKNOWN INTENT
        logger.warning(f"Unknown intent: {raw_intent} -> general_chat")
        return "general_chat"
    
    def _calculate_confidence(self, intent: str, message: str, entities: dict) -> float:
        """
        Calculate confidence score based on intent and context
        
        Args:
            intent: Classified intent
            message: Original message
            entities: Extracted entities
            
        Returns:
            Confidence score (0.0 to 1.0)
        """
        base_confidence = 0.90  # Base for LLM classification
        
        message_lower = message.lower()
        
        # INTENT-SPECIFIC CONFIDENCE SCORING
        
        if intent == "confirm_setup":
            # High confidence for short confirmations
            if len(message) <= 10:
                return 0.92
            elif len(message) <= 20:
                return 0.85
            else:
                return 0.70
        
        elif intent == "business_setup":
            # Check if pattern-detected business setup
            business_keywords = ["cafe", "toko", "warung", "resto", "salon", "usaha", "bisnis"]
            action_keywords = ["buka", "jual", "operasional", "harga", "mulai"]
            possessive_keywords = ["saya", "gue", "aku", "punya"]
            
            has_business = any(kw in message_lower for kw in business_keywords)
            has_action = any(kw in message_lower for kw in action_keywords)
            has_possessive = any(kw in message_lower for kw in possessive_keywords)
            
            if has_business and (has_action or has_possessive):
                return 0.90
            
            # Higher confidence if business entities extracted
            if entities and "Business" in entities.get("entities", {}):
                business_data = entities["entities"]["Business"]
                if business_data.get("business_name") and business_data.get("business_type"):
                    return 0.95
                elif business_data.get("business_name") or business_data.get("business_type"):
                    return 0.90
            return 0.80
        
        elif intent == "transaction_record":
            # High confidence if transaction entities extracted
            if entities and "entities" in entities:
                entity_data = entities.get("entities", {})
                # Check for financial fields (aligned with transaction_service.proto)
                has_jenis = "jenis_transaksi" in entity_data
                has_nominal = "total_nominal" in entity_data
                has_pihak = "nama_pihak" in entity_data
                has_metode = "metode_pembayaran" in entity_data
                
                entity_count = sum([has_jenis, has_nominal, has_pihak, has_metode])
                
                if entity_count >= 3:
                    return 0.95
                elif entity_count >= 2:
                    return 0.90
                elif entity_count >= 1:
                    return 0.85
            
            # Pattern-based confidence (has transaction keyword + nominal)
            has_transaction_kw = any(kw in message_lower for kw in ["jual", "beli", "bayar", "terima"])
            has_nominal = bool(re.search(r'\d+', message))
            
            if has_transaction_kw and has_nominal:
                return 0.88
            elif has_transaction_kw or has_nominal:
                return 0.80
            
            return 0.75
        
        elif intent == "financial_report":
            # High confidence if report type extracted
            if entities and "entities" in entities:
                entity_data = entities.get("entities", {})
                has_report_type = "report_type" in entity_data
                has_periode = "periode_pelaporan" in entity_data
                
                if has_report_type and has_periode:
                    return 0.95
                elif has_report_type or has_periode:
                    return 0.90
            
            # Pattern-based (has report keyword)
            report_keywords = ["untung", "rugi", "laba", "neraca", "kas", "aset"]
            if any(kw in message_lower for kw in report_keywords):
                return 0.85
            
            return 0.75
        
        elif intent == "inventory_query":
            # High confidence if inventory entities extracted
            if entities and "entities" in entities:
                entity_data = entities.get("entities", {})
                has_produk = "produk_id" in entity_data or "product_name" in entity_data
                has_gudang = "lokasi_gudang" in entity_data
                
                if has_produk and has_gudang:
                    return 0.95
                elif has_produk or has_gudang:
                    return 0.88
            
            # Pattern-based
            if "stok" in message_lower or "stock" in message_lower:
                return 0.85
            
            return 0.75
        
        elif intent == "inventory_update":
            # High confidence if update entities extracted
            if entities and "entities" in entities:
                entity_data = entities.get("entities", {})
                has_produk = "produk_id" in entity_data or "product_name" in entity_data
                has_quantity = "new_quantity" in entity_data or "jumlah_movement" in entity_data
                has_reason = "reason" in entity_data
                
                entity_count = sum([has_produk, has_quantity, has_reason])
                
                if entity_count >= 2:
                    return 0.92
                elif entity_count >= 1:
                    return 0.85
            
            return 0.78
        
        elif intent == "accounting_query":
            # Medium confidence for accounting queries
            if entities and "entities" in entities:
                entity_data = entities.get("entities", {})
                if "query_type" in entity_data:
                    return 0.90
            
            if "jurnal" in message_lower or "bagan akun" in message_lower:
                return 0.85
            
            return 0.75
        
        elif intent in ["faq_create", "faq_update", "faq_delete", "faq_query"]:
            if entities and "FAQ" in entities.get("entities", {}):
                return 0.88
            return 0.82
        
        elif intent == "general_chat":
            if len(message) < 10:
                return 0.85
            return 0.75
        
        elif intent == "others":
            return 0.60
        
        return base_confidence
    
    async def HealthCheck(self, request, context):
        """Health check endpoint"""
        logger.info("HealthCheck called")
        return empty_pb2.Empty()


class AsyncHealthServicer(health_pb2_grpc.HealthServicer):
    """gRPC Health Check Implementation for async server"""
    
    def __init__(self):
        super().__init__()
        self._status = health_pb2.HealthCheckResponse.SERVING
    
    async def Check(self, request, context):
        """Health check endpoint"""
        return health_pb2.HealthCheckResponse(status=self._status)
    
    async def Watch(self, request, context):
        """Streaming health check"""
        while True:
            yield health_pb2.HealthCheckResponse(status=self._status)
            await asyncio.sleep(1)


async def serve():
    """Start gRPC server"""
    port = os.getenv("INTENT_PARSER_GRPC_PORT", "5009")
    
    server = aio.server(
        options=[
            ('grpc.max_send_message_length', 50 * 1024 * 1024),
            ('grpc.max_receive_message_length', 50 * 1024 * 1024),
        ]
    )
    
    pb_grpc.add_IntentParserServiceServicer_to_server(
        IntentParserService(), server
    )

    # Register health check service
    health_pb2_grpc.add_HealthServicer_to_server(AsyncHealthServicer(), server)

    server.add_insecure_port(f'[::]:{port}')
    
    logger.info(f"Intent Parser gRPC server starting on port {port}")
    logger.info(f"LLM Classification: {'ENABLED' if LLM_AVAILABLE else 'DISABLED (using fallback)'}")
    logger.info(f"Dual Domain: Chatbot Setup + Financial Management")
    
    await server.start()
    await server.wait_for_termination()


if __name__ == '__main__':
    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        logger.info("Server stopped by user")
    except Exception as e:
        logger.error(f"Server error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)