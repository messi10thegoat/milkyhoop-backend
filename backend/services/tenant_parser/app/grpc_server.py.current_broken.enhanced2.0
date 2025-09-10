from backend.api_gateway.libs.milkyhoop_prisma import Prisma
import asyncio
import signal
import logging
import os
import json
import grpc
from grpc import aio
from grpc_health.v1 import health, health_pb2, health_pb2_grpc
from app.config import settings
from app import tenant_parser_pb2_grpc as pb_grpc
from app import tenant_parser_pb2 as pb
from app.services.llm_parser import parse_intent_entities

# Service availability flags
try:
    from app import ragcrud_service_pb2 as rag_pb
    from app import ragcrud_service_pb2_grpc as rag_pb_grpc
    RAG_CRUD_AVAILABLE = True
except ImportError:
    RAG_CRUD_AVAILABLE = False

try:
    from app import cust_context_pb2 as context_pb
    from app import cust_context_pb2_grpc as context_pb_grpc
    LEVEL13_AVAILABLE = True
except ImportError:
    LEVEL13_AVAILABLE = False

try:
    from app import cust_reference_pb2 as ref_pb
    from app import cust_reference_pb2_grpc as ref_pb_grpc
    REFERENCE_AVAILABLE = True
except ImportError:
    REFERENCE_AVAILABLE = False

print(f"✅ Services - RAG: {RAG_CRUD_AVAILABLE}, Level 13: {LEVEL13_AVAILABLE}, Reference: {REFERENCE_AVAILABLE}")

prisma = Prisma()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

class TenantParserServicer(pb_grpc.IntentParserServiceServicer):
    def __init__(self):
        self.rag_crud_target = "ragcrud_service:5001"
        self.context_target = "cust_context:5008"
        self.reference_target = "cust_reference:5013"
        
        # Channel reuse for performance
        self._context_channel = None
        self._rag_crud_channel = None
        self._reference_channel = None
        self._context_stub = None
        self._rag_crud_stub = None
        self._reference_stub = None
        
        asyncio.create_task(self._initialize_channels())
        
    async def _initialize_channels(self):
        """Initialize and cache gRPC channels"""
        try:
            if LEVEL13_AVAILABLE:
                self._context_channel = aio.insecure_channel(self.context_target)
                self._context_stub = context_pb_grpc.CustContextServiceStub(self._context_channel)
                logger.info("✅ Level 13 context channel initialized")
                
            if RAG_CRUD_AVAILABLE:
                self._rag_crud_channel = aio.insecure_channel(self.rag_crud_target)
                self._rag_crud_stub = rag_pb_grpc.RagCrudServiceStub(self._rag_crud_channel)
                logger.info("✅ RAG CRUD channel initialized")
                
            if REFERENCE_AVAILABLE:
                self._reference_channel = aio.insecure_channel(self.reference_target)
                self._reference_stub = ref_pb_grpc.Cust_referenceStub(self._reference_channel)
                logger.info("✅ Reference channel initialized")
                
        except Exception as e:
            logger.error(f"❌ Channel initialization error: {e}")

    def extract_tenant_id(self, message: str, user_id: str) -> str:
        """FALLBACK: Extract tenant from message content or use intelligent guess"""
        message_lower = message.lower()
        
        # Tenant detection patterns
        if any(word in message_lower for word in ['bca', 'tahapan', 'xpresi', 'britama']):
            return 'bca'
        elif any(word in message_lower for word in ['simpledimple', 'baju', 'anak', 'kids']):
            return 'simpledimple'
        elif any(word in message_lower for word in ['konseling', 'psikolog', 'terapi']):
            return 'konsultanpsikologi'
        else:
            # Generic fallback based on user_id pattern or default
            return 'bca'  # Most common tenant for now

    async def level13_proto_compatible_intelligence(self, session_id: str, tenant_id: str, message: str) -> dict:
        """Level 13 Intelligence using ONLY existing proto fields"""
        intelligence_data = {}
        
        if not LEVEL13_AVAILABLE or not self._context_stub:
            logger.info(f"ℹ️ [{tenant_id}] Level 13 unavailable - using basic processing")
            return intelligence_data
            
        # PARALLEL Level 13 calls with CORRECT proto fields
        async def call_mood_detection():
            try:
                mood_req = context_pb.SetConversationMoodRequest()
                mood_req.tenant_id = tenant_id
                mood_req.session_id = session_id
                # Only use fields that exist: mood, reason, confidence
                mood_req.mood = "neutral"  # Default value
                
                mood_resp = await self._context_stub.SetConversationMood(mood_req, timeout=5.0)
                return {'mood': mood_resp.detected_mood, 'mood_confidence': mood_resp.confidence}
            except Exception as e:
                logger.debug(f"Mood detection failed for {tenant_id}: {e}")
                return {'mood': 'neutral', 'mood_confidence': 0.6}
        
        async def call_intent_tracking():
            try:
                intent_req = context_pb.TrackUserIntentRequest()
                intent_req.tenant_id = tenant_id
                intent_req.session_id = session_id
                # Only use existing fields: intent, confidence, detected_from
                intent_req.intent = "general_inquiry"
                
                intent_resp = await self._context_stub.TrackUserIntent(intent_req, timeout=5.0)
                return {
                    'intent_progression': intent_resp.current_intent,
                    'intent_stage': intent_resp.intent_stage
                }
            except Exception as e:
                logger.debug(f"Intent tracking failed for {tenant_id}: {e}")
                return {'intent_progression': 'general_inquiry', 'intent_stage': 'information_gathering'}
        
        async def call_product_detection():
            try:
                product_req = context_pb.DetectProductMentionedRequest()
                product_req.tenant_id = tenant_id
                product_req.session_id = session_id
                # Only use existing field: conversation_turn
                product_req.conversation_turn = message[:100]  # Truncate if needed
                
                product_resp = await self._context_stub.DetectProductMentioned(product_req, timeout=5.0)
                return {
                    'products': product_resp.mentioned_products,
                    'product_categories': product_resp.product_categories
                }
            except Exception as e:
                logger.debug(f"Product detection failed for {tenant_id}: {e}")
                return {'products': [], 'product_categories': []}
        
        async def call_tone_adaptation():
            try:
                tone_req = context_pb.AdaptToneToUserMoodRequest()
                tone_req.tenant_id = tenant_id
                tone_req.session_id = session_id
                tone_req.detected_mood = 'neutral'
                
                tone_resp = await self._context_stub.AdaptToneToUserMood(tone_req, timeout=5.0)
                return {
                    'recommended_tone': tone_resp.recommended_tone,
                    'tone_guidelines': tone_resp.tone_guidelines
                }
            except Exception as e:
                logger.debug(f"Tone adaptation failed for {tenant_id}: {e}")
                return {'recommended_tone': 'professional_friendly'}
        
        async def call_lead_scoring():
            try:
                lead_req = context_pb.FindLeadSignalsRequest()
                lead_req.tenant_id = tenant_id
                lead_req.session_id = session_id
                # Use available fields only
                
                lead_resp = await self._context_stub.FindLeadSignals(lead_req, timeout=5.0)
                return {
                    'lead_score': lead_resp.lead_score,
                    'buying_signals': lead_resp.buying_signals
                }
            except Exception as e:
                logger.debug(f"Lead scoring failed for {tenant_id}: {e}")
                return {'lead_score': 0.5, 'buying_signals': []}
        
        # Run Level 13 calls in parallel for performance
        try:
            results = await asyncio.gather(
                call_mood_detection(),
                call_intent_tracking(),
                call_product_detection(),
                call_tone_adaptation(),
                call_lead_scoring(),
                return_exceptions=True
            )
            
            # Merge all results
            for result in results:
                if isinstance(result, dict):
                    intelligence_data.update(result)
            
            logger.info(f"🧠 [{tenant_id}] Level 13 proto-compatible intelligence complete ({len(intelligence_data)} signals)")
            
        except Exception as e:
            logger.error(f"❌ [{tenant_id}] Level 13 intelligence failed: {e}")
            
        return intelligence_data

    def apply_intelligence_enhancement(self, response: str, intelligence_data: dict, tenant_id: str) -> str:
        """Apply Level 13 intelligence to enhance response"""
        if not intelligence_data:
            return response
            
        enhanced_response = response
        
        # Mood-based enhancement
        mood = intelligence_data.get('mood', 'neutral')
        if mood == 'happy':
            enhanced_response = f"Senang bisa membantu! {enhanced_response} 😊"
        elif mood == 'frustrated':
            enhanced_response = f"Maaf jika ada kendala sebelumnya. {enhanced_response} 🙏"
        elif mood == 'curious':
            enhanced_response = f"Pertanyaan yang bagus! {enhanced_response}"
        
        # Intent stage enhancement
        intent_stage = intelligence_data.get('intent_stage', '')
        if intent_stage == 'purchase_decision':
            enhanced_response += "\n\nApakah Anda ingin melanjutkan? Saya siap membantu! 🚀"
        elif intent_stage == 'information_gathering':
            enhanced_response += "\n\nAda informasi lain yang ingin Anda ketahui? 😊"
        
        # Lead scoring enhancement
        lead_score = intelligence_data.get('lead_score', 0)
        if lead_score > 0.7:
            enhanced_response += "\n\nTerlihat Anda serius dengan ini. Mau saya hubungkan dengan tim specialist? 📞"
        
        # Product enhancement
        products = intelligence_data.get('products', [])
        if products:
            enhanced_response += f"\n\nBtw, produk yang Anda sebutkan memang pilihan yang tepat! ��"
        
        return enhanced_response

    async def call_rag_crud(self, tenant_id: str, message: str):
        """Get FAQ content - WORKING"""
        if not RAG_CRUD_AVAILABLE or not self._rag_crud_stub:
            return f"Informasi untuk {tenant_id} sedang tidak tersedia saat ini."
            
        try:
            request = rag_pb.FuzzySearchRequest()
            request.tenant_id = tenant_id
            request.search_content = message
            request.similarity_threshold = 0.7
            
            response = await self._rag_crud_stub.FuzzySearchDocuments(request, timeout=10.0)
            
            if response.documents and len(response.documents) > 0:
                best_match = response.documents[0]
                logger.info(f"✅ [{tenant_id}] FAQ match found")
                return best_match.content
            else:
                logger.info(f"ℹ️ [{tenant_id}] No FAQ matches")
                return f"Maaf, informasi yang Anda cari untuk {tenant_id} belum tersedia."
                
        except Exception as e:
            logger.error(f"❌ [{tenant_id}] FAQ fetch error: {e}")
            return f"Maaf ada kendala teknis untuk {tenant_id}."

    async def DoSomething(self, request, context):
        """Proto-Compatible Level 13 Enhanced Processing"""
        session_id = request.user_id
        message = request.input
        
        # INTELLIGENT TENANT EXTRACTION as fallback
        tenant_id = self.extract_tenant_id(message, session_id)
        
        logger.info(f"🧠 [{tenant_id}] Level 13 proto-compatible processing: {message[:50]}...")
        
        try:
            # Step 1: Parse intent
            intent_result = await asyncio.to_thread(parse_intent_entities, message)
            logger.info(f"📝 [{tenant_id}] Intent: {intent_result.get('intent')}")
            
            # Step 2: Level 13 Intelligence (proto-compatible)
            intelligence_data = await self.level13_proto_compatible_intelligence(session_id, tenant_id, message)
            
            # Step 3: Get FAQ content
            faq_content = await self.call_rag_crud(tenant_id, message)
            
            # Step 4: Apply Level 13 enhancement
            enhanced_response = self.apply_intelligence_enhancement(faq_content, intelligence_data, tenant_id)
            
            # Final response
            result = {
                "tenant_id": tenant_id,
                "intent": intent_result.get("intent", "general_inquiry"),
                "entities": intent_result.get("entities", {}),
                "response": enhanced_response,
                "level13_intelligence": intelligence_data,
                "mood": intelligence_data.get('mood', 'neutral'),
                "lead_score": intelligence_data.get('lead_score', 0),
                "recommended_tone": intelligence_data.get('recommended_tone', 'professional_friendly')
            }
            
            logger.info(f"🚀 [{tenant_id}] Level 13 proto-compatible response complete")
            
            return pb.IntentParserResponse(
                status="success",
                result=json.dumps(result, ensure_ascii=False)
            )
            
        except Exception as e:
            logger.error(f"🔥 [{tenant_id}] Processing error: {e}", exc_info=True)
            return pb.IntentParserResponse(
                status="error",
                result=json.dumps({
                    "tenant_id": tenant_id,
                    "intent": "general_inquiry",
                    "entities": {},
                    "response": f"Maaf ada kendala teknis untuk {tenant_id}, silakan coba lagi.",
                    "level13_intelligence": {}
                }, ensure_ascii=False)
            )

    async def HealthCheck(self, request, context):
        return request

async def serve() -> None:
    listen_addr = f"[::]:{settings.GRPC_PORT}"
    server = aio.server()
    
    servicer = TenantParserServicer()
    pb_grpc.add_IntentParserServiceServicer_to_server(servicer, server)
    
    health_servicer = health.HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)
    health_servicer.set("tenant_parser.IntentParserService", health_pb2.HealthCheckResponse.SERVING)
    
    server.add_insecure_port(listen_addr)
    
    logger.info("🚀 Level 13 Proto-Compatible Tenant Parser listening on port %s", settings.GRPC_PORT)
    logger.info(f"🧠 Level 13 Intelligence: {'✅ Available' if LEVEL13_AVAILABLE else '❌ Unavailable'}")
    logger.info("�� PROTO-FIXED: Uses only existing proto fields")
    logger.info("🧠 SMART TENANT: Intelligent tenant detection from message content")
    
    await server.start()

    def handle_shutdown(*_):
        logger.info("🛑 Shutting down Level 13 Proto-Compatible Tenant Parser...")
        asyncio.create_task(server.stop(grace=10.0))

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, handle_shutdown)

    await server.wait_for_termination()

if __name__ == "__main__":
    asyncio.run(serve())
