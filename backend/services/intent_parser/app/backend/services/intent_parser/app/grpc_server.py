#!/usr/bin/env python3
"""
Intent Parser gRPC Server - Context-Aware Version
Handles intent classification with conversation context support
"""

import os
import sys
import json
import logging
import asyncio
from typing import Dict, Any

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
    Intent Parser gRPC Service Implementation
    
    Features:
    - Context-aware LLM classification (GPT-4o)
    - Rule-based fallback for reliability
    - Proper intent validation and mapping
    - Confidence scoring
    """
    
    def __init__(self):
        """Initialize service with intent definitions"""
        
        # SETUP MODE INTENTS (Business Owner Actions)
        self.setup_mode_intents = {
            "business_setup",      # Business information gathering
            "confirm_setup",       # User confirmation (yes/ok/proceed)
            "faq_create",          # Create new FAQ
            "faq_read",            # Read/query FAQ
            "faq_update",          # Update existing FAQ
            "faq_delete",          # Delete FAQ
            "faq_query",           # Search FAQ
            "general_chat",        # General conversation
            "others"               # Unclear/unknown intent
        }
        
        # CUSTOMER MODE INTENTS (Public Customer Service)
        self.customer_mode_intents = {
            "customer_inquiry",    # Product/service questions
            "price_inquiry",       # Pricing questions
            "order_status",        # Order tracking
            "complaint",           # Customer complaints
            "feedback"             # Customer feedback
        }
        
        logger.info(f"Intent Parser initialized | LLM: {LLM_AVAILABLE}")
        logger.info(f"Setup intents: {self.setup_mode_intents}")
    
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
            logger.info(f"ClassifyIntent called | message_length={len(request.message)}")
            
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
            
            # PRIMARY: LLM-based classification with context awareness
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
                        entities_json=json.dumps(raw_entities)
                    )
                    
                except Exception as e:
                    logger.error(f"LLM classification failed: {e}")
                    logger.info("Falling back to rule-based classification")
                    # Fall through to rule-based fallback
            
            # FALLBACK: Rule-based classification
            logger.info("Using rule-based fallback classification")
            
            message = request.message.lower().strip()
            confidence = 0.70  # Lower confidence for rule-based
            intent = "general_chat"
            entities = {"entities": {}}
            
            # PRIORITY 1: Confirmation patterns
            if message in ["ya", "yes", "ok", "oke", "okay", "lanjut", "proceed", "iya"]:
                intent = "confirm_setup"
                confidence = 0.85
            
            # PRIORITY 2: Business setup patterns
            elif any(word in message for word in ["buka", "bikin", "setup", "daftar", "register"]):
                intent = "business_setup"
                confidence = 0.75
            
            # PRIORITY 3: FAQ create patterns
            elif any(word in message for word in ["tambah faq", "buat faq", "create faq"]):
                intent = "faq_create"
                confidence = 0.80
            
            # PRIORITY 4: FAQ read patterns
            elif any(word in message for word in ["baca faq", "lihat faq", "read faq"]):
                intent = "faq_read"
                confidence = 0.80
            
            # PRIORITY 5: FAQ update patterns
            elif any(word in message for word in ["update faq", "ubah faq", "edit faq"]):
                intent = "faq_update"
                confidence = 0.80
            
            # PRIORITY 6: FAQ delete patterns
            elif any(word in message for word in ["hapus", "delete", "buang"]) and "faq" in message:
                intent = "faq_delete"
                confidence = 0.80
            
            # PRIORITY 7: FAQ query patterns
            elif any(word in message for word in ["cek faq", "lihat faq", "tampilkan faq"]):
                intent = "faq_query"
                confidence = 0.80
            
            # PRIORITY 8: Default fallback
            else:
                intent = "general_chat"
                confidence = 0.65
            
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
    
    def _validate_and_map_intent(self, raw_intent: str, message: str) -> str:
        """
        Validate LLM intent and map to setup mode schema
        
        Args:
            raw_intent: Intent from LLM
            message: Original user message
            
        Returns:
            Valid setup mode intent
        """
        # Normalize intent name
        raw_intent = raw_intent.lower().strip()
        
        # INTENT NAME MAPPING (Handle LLM variations)
        intent_mapping = {
            "confirmation": "confirm_setup",
            "setup": "business_setup",
            "business": "business_setup",
            "create_faq": "faq_create",
            "update_faq": "faq_update",
            "delete_faq": "faq_delete",
            "query_faq": "faq_query",
            "read_faq": "faq_query",
            "chat": "general_chat",
            "greeting": "general_chat"
        }
        
        # Apply mapping if needed
        mapped_intent = intent_mapping.get(raw_intent, raw_intent)
        
        # VALIDATION: Check if intent is valid for setup mode
        if mapped_intent in self.setup_mode_intents:
            # Additional validation for confirm_setup
            if mapped_intent == "confirm_setup" and len(message) > 20:
                logger.info(f"Downgrading confirm_setup to general_chat (message too long: {len(message)} chars)")
                return "general_chat"
            
            return mapped_intent
        
        # CUSTOMER MODE INTENT DETECTION
        if mapped_intent in self.customer_mode_intents:
            logger.info(f"Customer mode intent detected: {mapped_intent} -> general_chat")
            return "general_chat"
        
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
            # Higher confidence if business entities extracted
            if entities and "Business" in entities.get("entities", {}):
                business_data = entities["entities"]["Business"]
                if business_data.get("business_name") and business_data.get("business_type"):
                    return 0.95
                elif business_data.get("business_name") or business_data.get("business_type"):
                    return 0.90
                else:
                    return 0.85
            return 0.80
        
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