"""
business_parser/app/grpc_server.py

Business Parser - Intent Classification for Tenant Mode Queries
Purpose: Classify financial analytics, inventory, and accounting queries
Architecture: Called by tenant_orchestrator → routes to financial services

Author: MilkyHoop Team
Version: 1.0.0
"""

import asyncio
import signal
import logging
import json
import re
from typing import Dict, Any
from datetime import datetime

import grpc
from grpc import aio
from grpc_health.v1 import health, health_pb2, health_pb2_grpc
from google.protobuf import empty_pb2, timestamp_pb2

# Import generated proto stubs
import business_parser_pb2 as pb
import business_parser_pb2_grpc as pb_grpc

# Import config
from config import settings

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

# Try to import LLM parser
try:
    from services.llm_parser import parse_tenant_intent_entities
    LLM_AVAILABLE = True
    logger.info("LLM parser loaded successfully")
except ImportError as e:
    logger.warning(f"LLM parser not available, using fallback: {e}")
    LLM_AVAILABLE = False


class BusinessParserService(pb_grpc.BusinessParserServicer):
    """
    Business Parser Service Implementation
    
    Classifies tenant mode queries into financial/analytics intents
    """
    
    def __init__(self):
        # Tenant mode intents (read-only analytics)
        self.tenant_intents = [
            "financial_report",      # SAK EMKM reports
            "top_products",          # Best sellers
            "low_sell_products",     # Slow moving
            "inventory_query",       # Stock level/movement
            "accounting_query",      # Journal/CoA
            "general_inquiry",       # Business questions
            "out_of_scope"           # Outside domain
        ]
        
        logger.info(f"Business Parser initialized | LLM: {LLM_AVAILABLE}")
        logger.info(f"Tenant intents: {self.tenant_intents}")
    
    async def ClassifyIntent(
        self, 
        request: pb.ClassifyIntentRequest, 
        context
    ) -> pb.ClassifyIntentResponse:
        """
        Classify user query into tenant mode intent
        
        Flow:
        1. Try LLM classification (GPT-4o)
        2. Fall back to rule-based if LLM fails
        3. Return intent + entities + confidence
        """
        
        try:
            logger.info(f"ClassifyIntent called | tenant={request.tenant_id} | message='{request.message[:100]}...'")
            
            # PRIMARY: LLM-based classification
            if LLM_AVAILABLE:
                try:
                    logger.info("Using LLM classification (GPT-4o)")
                    
                    # Extract context if provided
                    context_str = None
                    if request.context and request.context.strip():
                        logger.info(f"Context provided | length={len(request.context)}")
                        context_str = request.context
                    
                    # Call LLM parser
                    parsed = parse_tenant_intent_entities(
                        text=request.message,
                        context=context_str,
                        tenant_id=request.tenant_id
                    )
                    
                    intent = parsed.get("intent", "general_inquiry")
                    entities = parsed.get("entities", {})
                    confidence = parsed.get("confidence", 0.0)
                    reasoning = parsed.get("reasoning", "")
                    model_used = parsed.get("model_used", "gpt-4o")
                    
                    logger.info(
                        f"LLM classification | intent={intent} | "
                        f"confidence={confidence:.2f} | model={model_used}"
                    )
                    
                    return pb.ClassifyIntentResponse(
                        intent=intent,
                        entities_json=json.dumps(entities, ensure_ascii=False),
                        confidence=confidence,
                        reasoning=reasoning,
                        model_used=model_used,
                        processing_time_ms=0,  # TODO: track timing
                        timestamp=timestamp_pb2.Timestamp(seconds=int(datetime.now().timestamp()))
                    )
                    
                except Exception as e:
                    logger.error(f"LLM classification failed: {e}")
                    logger.info("Falling back to rule-based classification")
                    # Fall through to rule-based fallback
            
            # FALLBACK: Rule-based classification
            logger.info("Using rule-based fallback classification")
            intent, confidence, entities = self._rule_based_classify(request.message)
            
            logger.info(f"Rule-based classification | intent={intent} | confidence={confidence:.2f}")
            
            return pb.ClassifyIntentResponse(
                intent=intent,
                entities_json=json.dumps(entities, ensure_ascii=False),
                confidence=confidence,
                reasoning="rule-based fallback",
                model_used="regex",
                processing_time_ms=0,
                timestamp=timestamp_pb2.Timestamp(seconds=int(datetime.now().timestamp()))
            )
            
        except Exception as e:
            logger.error(f"ClassifyIntent error: {e}")
            import traceback
            traceback.print_exc()
            
            # Return error as general_inquiry
            return pb.ClassifyIntentResponse(
                intent="general_inquiry",
                entities_json="{}",
                confidence=0.3,
                reasoning=f"error: {str(e)}",
                model_used="error_fallback",
                processing_time_ms=0,
                timestamp=timestamp_pb2.Timestamp(seconds=int(datetime.now().timestamp()))
            )
    
    def _rule_based_classify(self, message: str) -> tuple:
        """
        Rule-based intent classification (fallback)
        
        Returns:
            (intent, confidence, entities)
        """
        text_lower = message.lower()
        
        # 1. Financial report triggers
        financial_keywords = ["untung", "rugi", "laba", "neraca", "kas", "aset", "laporan", 
                             "keuangan", "finansial", "omzet", "profit", "pendapatan"]
        if any(kw in text_lower for kw in financial_keywords):
            return ("financial_report", 0.85, {
                "report_type": "laba_rugi",
                "periode_pelaporan": datetime.now().strftime("%Y-%m")
            })
        
        # 2. Top products triggers
        top_keywords = ["terlaris", "paling laku", "best seller", "top", "ranking"]
        if any(kw in text_lower for kw in top_keywords):
            return ("top_products", 0.90, {
                "time_range": "monthly",
                "limit": 10
            })
        
        # 3. Low-sell products triggers
        low_keywords = ["kurang laku", "slow moving", "jarang laku", "menumpuk"]
        if any(kw in text_lower for kw in low_keywords):
            return ("low_sell_products", 0.88, {
                "time_range": "30_hari",
                "limit": 10
            })
        
        # 4. Inventory query triggers
        inventory_keywords = ["stok", "stock", "persediaan", "cek stok", "berapa stok"]
        if any(kw in text_lower for kw in inventory_keywords):
            # Extract product name from query
            # Simple extraction: words after keywords like "stok", "cek", "berapa"
            product_name = ""
            
            # Try to extract product name using simple pattern
            patterns = [
                r'(?:stok|stock)\s+([a-zA-Z0-9\s]+?)(?:\?|$|\.)',
                r'(?:cek|check)\s+stok\s+([a-zA-Z0-9\s]+?)(?:\?|$|\.)',
                r'berapa\s+stok\s+([a-zA-Z0-9\s]+?)(?:\?|$|\.)',
            ]
            
            for pattern in patterns:
                match = re.search(pattern, text_lower)
                if match:
                    product_name = match.group(1).strip()
                    break
            
            return ("inventory_query", 0.80, {
                "query_type": "stock_level",
                "product_name": product_name
            })
        
        # 5. Accounting query triggers
        accounting_keywords = ["jurnal", "bagan akun", "chart of account", "debit", "kredit"]
        if any(kw in text_lower for kw in accounting_keywords):
            return ("accounting_query", 0.75, {
                "query_type": "journal_entries"
            })
        
        # 6. Default: general inquiry
        return ("general_inquiry", 0.6, {})
    
    async def HealthCheck(self, request: empty_pb2.Empty, context) -> empty_pb2.Empty:
        """Health check endpoint"""
        return empty_pb2.Empty()


async def serve() -> None:
    """Start gRPC server"""
    
    logger.info("Starting Business Parser gRPC server...")
    
    # Create server
    server = aio.server()
    
    # Add servicer
    servicer = BusinessParserService()
    pb_grpc.add_BusinessParserServicer_to_server(servicer, server)
    
    # Add health check
    health_servicer = health.HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)
    health_servicer.set("", health_pb2.HealthCheckResponse.SERVING)
    health_servicer.set("business_parser.BusinessParser", health_pb2.HealthCheckResponse.SERVING)
    
    # Listen on port
    listen_addr = f"0.0.0.0:{settings.GRPC_PORT}"
    server.add_insecure_port(listen_addr)
    
    logger.info(f"🚀 Business Parser gRPC server listening on port {settings.GRPC_PORT}")
    logger.info(f"📊 Tenant intents: financial_report, top_products, inventory_query, accounting_query")
    
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
        logger.info("✅ Shutdown complete")


if __name__ == "__main__":
    asyncio.run(serve())