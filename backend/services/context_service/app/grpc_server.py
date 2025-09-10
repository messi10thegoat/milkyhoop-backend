import asyncio
import logging
import signal
import os
import grpc
from grpc import aio
from grpc_health.v1 import health, health_pb2, health_pb2_grpc
from app.config import settings
from app import context_service_pb2_grpc as pb_grpc
from app import context_service_pb2 as pb
import json
import redis.asyncio as redis
from datetime import datetime

# ✅ Logging config
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(settings.SERVICE_NAME)

class Context_serviceServicer(pb_grpc.ContextServiceServicer):
    
    def __init__(self):
        self.redis_client = None
        
    async def _get_redis_client(self):
        """Initialize Redis client if needed"""
        if self.redis_client is None:
            try:
                self.redis_client = redis.from_url("redis://redis:6379")
                await self.redis_client.ping()
                logger.info("✅ Redis connected")
            except Exception as e:
                logger.error("❌ Redis connection failed: %s", str(e))
                self.redis_client = None
        return self.redis_client
    
    async def _get_conversation_context(self, session_id: str, tenant_id: str):
        """Get conversation context from Memory Service"""
        try:
            redis_client = await self._get_redis_client()
            if not redis_client:
                return {}
                
            # Try multiple context keys
            context_keys = [
                f"conversation:{tenant_id}:{session_id}:context",
                f"session:{session_id}:context",
                f"tenant:{tenant_id}:last_context"
            ]
            
            for key in context_keys:
                context_data = await redis_client.get(key)
                if context_data:
                    logger.info("📦 Found context in key: %s", key)
                    return json.loads(context_data)
            
            logger.info("📭 No conversation context found")
            return {}
            
        except Exception as e:
            logger.error("❌ Error getting context: %s", str(e))
            return {}
    
    async def _extract_last_entity_from_session(self, session_id: str, tenant_id: str):
        """Extract last mentioned entity from session"""
        try:
            redis_client = await self._get_redis_client()
            if not redis_client:
                return None
                
            # Check session data for last entity
            session_keys = [
                f"session:{session_id}:last_entity",
                f"conversation:{tenant_id}:{session_id}:last_entity",
                f"tenant:{tenant_id}:sessions:{session_id}:entity"
            ]
            
            for key in session_keys:
                entity_data = await redis_client.get(key)
                if entity_data:
                    logger.info("🎯 Found last entity: %s", entity_data)
                    return entity_data.decode() if isinstance(entity_data, bytes) else entity_data
            
            return None
            
        except Exception as e:
            logger.error("❌ Error extracting entity: %s", str(e))
            return None
    
    def ResolveReference(self, request, context):
        """Resolve references in user message - UNIVERSAL LOGIC"""
        logger.info("📥 ResolveReference request received")
        logger.info("🔍 Session: %s | Tenant: %s", request.session_id, request.tenant_id)
        logger.info("💬 Message: '%s'", request.message)
        
        # Check for different types of references
        message_lower = request.message.lower()
        detected_references = []
        resolved_message = request.message
        
        # 1. Temporal References (yang tadi, kemarin, sebelumnya)
        temporal_refs = ["yang tadi", "tadi", "sebelumnya", "yang barusan", "yang kemarin"]
        for ref in temporal_refs:
            if ref in message_lower:
                logger.info("⏰ Temporal reference detected: '%s'", ref)
                detected_references.append({
                    "type": "temporal",
                    "reference": ref,
                    "needs_context": True
                })
        
        # 2. Demonstrative References (itu, ini, yang itu)  
        demonstrative_refs = ["yang itu", "itu", "ini", "yang ini", "tersebut"]
        for ref in demonstrative_refs:
            if ref in message_lower:
                logger.info("👉 Demonstrative reference detected: '%s'", ref)
                detected_references.append({
                    "type": "demonstrative", 
                    "reference": ref,
                    "needs_context": True
                })
        
        # 3. Topic References (yang kita bahas, topik tersebut)
        topic_refs = ["yang kita bahas", "topik tersebut", "yang disebutkan", "pembahasan tadi"]
        for ref in topic_refs:
            if ref in message_lower:
                logger.info("💭 Topic reference detected: '%s'", ref)
                detected_references.append({
                    "type": "topic",
                    "reference": ref, 
                    "needs_context": True
                })
        
        # Process detected references
        if detected_references:
            logger.info("🎯 Total references detected: %d", len(detected_references))
            
            resolved_references = []
            
            for ref_data in detected_references:
                placeholder_entity = self._generate_placeholder_resolution(
                    ref_data, request.tenant_id, request.session_id
                )
                
                if placeholder_entity:
                    resolved_references.append(placeholder_entity)
                    
                    # Replace reference in message
                    resolved_message = resolved_message.replace(
                        ref_data["reference"], 
                        placeholder_entity.entity_value
                    )
            
            # ✅ ADD: Context-aware intent suggestion
            suggested_intent = self._suggest_context_aware_intent(request.message, resolved_message)
            if suggested_intent:
                logger.info("💡 Suggested intent override: %s", suggested_intent)
            
            logger.info("✅ References resolved: %d → Message: '%s'", len(resolved_references), resolved_message)
            
            return pb.ReferenceResponse(
                resolved_message=resolved_message,
                references=resolved_references,
                confidence=0.8,
                success=True,
                suggested_intent=suggested_intent or ""
            )
        
        # No references detected
        logger.info("📝 No references detected in message")
        return pb.ReferenceResponse(
            resolved_message=request.message,
            references=[],
            confidence=1.0,
            success=True,
            suggested_intent=""
        )
        
        # No references detected
        logger.info("📝 No references detected in message")
        return pb.ReferenceResponse(
            resolved_message=request.message,
            references=[],
            confidence=1.0,
            success=True
        )
    
    def _generate_placeholder_resolution(self, ref_data, tenant_id, session_id):
        """Generate temporal-aware resolution with PRESERVATION"""
        
        # CRITICAL: Pure temporal words should NEVER be replaced
        PURE_TEMPORAL_WORDS = {
            'sebelumnya', 'kemarin', 'minggu lalu', 'bulan lalu', 'tahun lalu',
            'nanti', 'besok', 'minggu depan', 'bulan depan', 'tahun depan', 
            'dulu', 'lampau'
        }
        
        reference_text = ref_data["reference"]
        
        # Step 1: Check if this is a pure temporal word - PRESERVE IT
        for temporal_word in PURE_TEMPORAL_WORDS:
            if temporal_word in reference_text.lower():
                logger.info("⏰ Temporal word PRESERVED: '%s' → NO REPLACEMENT", reference_text)
                # Return original reference - NO REPLACEMENT
                return pb.ResolvedReference(
                    reference=reference_text,
                    entity_type="temporal_preserved",
                    entity_id=0,
                    entity_value=reference_text,  # Keep original
                    confidence=1.0
                )
        
        # Step 2: Only replace contextual references like "yang tadi"
        CONTEXTUAL_REFERENCES = ['yang tadi', 'yang kemarin', 'yang barusan', 'tadi', 'itu']
        
        if any(ctx_ref in reference_text.lower() for ctx_ref in CONTEXTUAL_REFERENCES):
            # Business context replacement
            business_contexts = {
                "konsultan": "harga konsultasi",
                "psikolog": "harga konsultasi", 
                "warung": "harga menu",
                "bookstore": "harga buku",
            }
            
            detected_context = "harga"
            tenant_lower = tenant_id.lower()
            
            for business_type, context in business_contexts.items():
                if business_type in tenant_lower:
                    detected_context = context
                    logger.info("🏢 Business context: %s → %s", business_type, context)
                    break
            
            logger.info("🎯 Contextual resolution: '%s' → '%s'", reference_text, detected_context)
            
            return pb.ResolvedReference(
                reference=reference_text,
                entity_type="contextual_reference",
                entity_id=0,
                entity_value=detected_context,
                confidence=0.8
            )
        # Step 3: Default - no replacement needed
        logger.info("📝 No replacement needed for: '%s'", reference_text)
        return None

    
    def _suggest_context_aware_intent(self, original_message, resolved_message):
        """Suggest intent based on context awareness"""
        
        message_lower = original_message.lower()
        
        # Pattern: Reference + Price change = Update
        price_change_words = ["kemahalan", "turunin", "ganti jadi", "ubah jadi", "update jadi", "naik jadi"]
        reference_words = ["yang tadi", "tadi", "yang itu", "itu", "yang kemarin"]
        
        has_reference = any(ref in message_lower for ref in reference_words)
        has_price_change = any(word in message_lower for word in price_change_words)
        
        if has_reference and has_price_change:
            logger.info("🎯 Context-aware intent suggestion: faq_update")
            return "faq_update"
        
        # Pattern: Reference + Question = Query
        question_words = ["berapa", "apa", "gimana", "bagaimana", "kapan", "dimana"]
        has_question = any(word in message_lower for word in question_words)
        
        if has_reference and has_question:
            logger.info("🎯 Context-aware intent suggestion: faq_query")
            return "faq_query"
        
        return None  # No suggestion, let original intent parser handle



async def serve() -> None:
    logger.info("🚀 Starting Universal Context Service...")
    logger.info("🌍 Supporting all business types: Restaurant, Clinic, School, Shop, etc.")
    
    server = aio.server()
    pb_grpc.add_ContextServiceServicer_to_server(Context_serviceServicer(), server)
    
    # ✅ Health check
    health_servicer = health.HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)
    health_servicer.set('', health_pb2.HealthCheckResponse.SERVING)
    
    listen_addr = f"[::]:{settings.GRPC_PORT}"
    server.add_insecure_port(listen_addr)
    logger.info(f"🚀 {settings.SERVICE_NAME} listening on port {settings.GRPC_PORT}")
    
    stop_event = asyncio.Event()
    
    def handle_shutdown(*_):
        logger.info("🛑 Shutdown signal received. Cleaning up...")
        stop_event.set()
    
    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)
    
    try:
        await server.start()
        await stop_event.wait()
    finally:
        logger.info("🧹 Shutting down gRPC server...")
        await server.stop(5)
        logger.info("✅ gRPC server shut down cleanly.")

if __name__ == "__main__":
    asyncio.run(serve())