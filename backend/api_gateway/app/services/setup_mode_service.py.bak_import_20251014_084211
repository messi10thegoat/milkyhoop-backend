# backend/api_gateway/app/services/setup_mode_service.py

from typing import List, Dict, Optional
import uuid
import logging
from datetime import datetime
from pydantic import BaseModel

# Import existing service clients
from backend.api_gateway.app.services.intent_client import IntentParserClient
from backend.api_gateway.app.services.ragllm_client import RagLLMClient
from backend.api_gateway.app.services.ragcrud_client import RagCrudClient
from backend.api_gateway.app.services.memory_client import MemoryClient
from backend.api_gateway.app.services.chatbot_client import ChatbotClient
from backend.api_gateway.libs.milkyhoop_prisma import Prisma


logger = logging.getLogger(__name__)

# Request/Response schemas
class SetupModeRequest(BaseModel):
    user_id: str
    tenant_id: str
    message: str
    session_id: Optional[str] = None

class SetupModeResponse(BaseModel):
    status: str
    tenant_profile: Dict
    milky_response: str
    next_action: str


class SetupModeService:
    """Service for handling setup mode conversations between business owners and Milky assistant"""
    
    def __init__(self):
        self.prisma = Prisma()
        self.memory_client = MemoryClient()
        self.conversation_memory = {}  # In-memory storage for MVP
    
    def generate_trace_id(self) -> str:
        """Generate unique trace ID for request tracking"""
        return str(uuid.uuid4())[:8]
    
    def get_conversation_history(self, session_id: str) -> List[Dict]:
        """Get conversation history for session"""
        return self.conversation_memory.get(session_id, [])
    
    def save_conversation_turn(self, session_id: str, user_message: str, bot_response: str, 
                             intent: str, entities: Dict, trace_id: str):
        """Save conversation turn to memory"""
        if session_id not in self.conversation_memory:
            self.conversation_memory[session_id] = []
        
        self.conversation_memory[session_id].append({
            "timestamp": datetime.now().isoformat(),
            "user_message": user_message,
            "bot_response": bot_response,
            "intent": intent,
            "entities": entities,
            "trace_id": trace_id
        })
        
        # Keep only last 10 turns to prevent memory bloat
        if len(self.conversation_memory[session_id]) > 10:
            self.conversation_memory[session_id] = self.conversation_memory[session_id][-10:]
    
    async def call_chatbot_service(self, user_id: str, tenant_id: str, message: str, trace_id: str) -> Dict:
        """Call Chatbot Service via gRPC"""
        try:
            chatbot_client = ChatbotClient()
            result = await chatbot_client.send_message(
                user_id=user_id,
                session_id=trace_id,
                message=message,
                tenant_id=tenant_id
            )
            return result
        except Exception as e:
            logger.error(f"[{trace_id}] Chatbot service error: {e}")
            return {"status": "error", "message": str(e)}
    
    async def call_flow_executor(self, intent: str, entities: Dict, user_id: str, 
                               tenant_id: str, trace_id: str) -> Dict:
        """Call Flow Executor for business automation"""
        try:
            # Flow mapping for different intents
            flow_mapping = {
                "business_setup": "business-setup-handler.json",
                "faq_create": "faq-create-handler.json", 
                "faq_read": "faq-read-handler.json",
                "faq_update": "faq-update-handler.json",
                "faq_delete": "faq-delete-handler.json",
                "faq_query": "faq-query-handler.json",
            }
            
            flow_file = flow_mapping.get(intent, "default-handler.json")
            
            # Execute flow (simplified for now)
            return {
                "flow_executed": flow_file,
                "intent": intent,
                "entities": entities,
                "status": "completed"
            }
        except Exception as e:
            logger.error(f"[{trace_id}] Flow executor error: {e}")
            return {"status": "error", "message": str(e)}
    
    async def send_notification(self, user_id: str, notification_type: str, 
                              details: Dict, trace_id: str):
        """Send notification for business events"""
        try:
            # Simplified notification logic
            logger.info(f"[{trace_id}] Notification sent: {notification_type} for {user_id}")
            return {"status": "sent", "type": notification_type}
        except Exception as e:
            logger.error(f"[{trace_id}] Notification error: {e}")
            return {"status": "error", "message": str(e)}
    
    async def handle_setup_conversation(self, request: SetupModeRequest) -> SetupModeResponse:
        """
        Main handler for setup mode conversations
        Extracted from onboarding.py conversational_setup function
        """
        trace_id = self.generate_trace_id()
        session_id = request.session_id or str(uuid.uuid4())
        
        logger.info(f"[{trace_id}] Setup mode conversation: user={request.user_id}, tenant={request.tenant_id}")
        
        try:
            # STEP 1: Call Chatbot Service for session management
            logger.info(f"[{trace_id}] Step 1: Calling Chatbot Service")
            chatbot_response = await self.call_chatbot_service(
                request.user_id, request.tenant_id, request.message, trace_id
            )
            
            # STEP 2: Intent parsing
            logger.info(f"[{trace_id}] Step 2: Intent parsing")
            intent_client = IntentParserClient()
            parsed = await intent_client.parse(user_id=request.user_id, message=request.message)
            
            intent = parsed.get("intent", "unknown")
            entities = parsed.get("entities", {})
            
            # STEP 3: Flow execution
            logger.info(f"[{trace_id}] Step 3: Flow execution")
            flow_result = await self.call_flow_executor(
                intent, entities, request.user_id, request.tenant_id, trace_id
            )
            
            # STEP 4: RAG response generation
            logger.info(f"[{trace_id}] Step 4: RAG response generation")
            rag_client = RagLLMClient()
            
            # Context for RAG
            context = {
                "user_id": request.user_id,
                "tenant_id": request.tenant_id,
                "intent": intent,
                "entities": entities,
                "flow_result": flow_result,
                "conversation_history": self.get_conversation_history(session_id)
            }
            
            natural_response = await rag_client.generate_answer(
                user_id=request.user_id,
                session_id=session_id,
                tenant_id=request.tenant_id,
                message=request.message
            )
            
            # STEP 5: Memory storage
            logger.info(f"[{trace_id}] Step 5: Memory storage")
            memory_data = {
                "user_id": request.user_id,
                "tenant_id": request.tenant_id,
                "intent_history": [intent],
                "last_interaction": datetime.now().isoformat(),
                "conversation_turns": len(self.get_conversation_history(session_id)) + 1,
                "trace_id": trace_id
            }
            
            await self.memory_client.store_memory(
                user_id=request.user_id,
                tenant_id=request.tenant_id,
                key=f"setup_session_{session_id}",
                value=memory_data
            )
            
            # STEP 6: Determine next action
            next_action_mapping = {
                "business_setup": "suggest_document_upload",
                "faq_create": "suggest_document_upload", 
                "faq_read": "show_existing_faq",
                "faq_update": "suggest_faq_edit",
                "product_inquiry": "provide_recommendation",
                "unknown": "collect_more_info"
            }
            next_action = next_action_mapping.get(intent, "collect_more_info")
            
            # STEP 7: Save conversation turn
            self.save_conversation_turn(
                session_id, request.message, natural_response, intent, entities, trace_id
            )
            
            # STEP 8: Send notification for business events
            if intent in ["business_setup", "faq_create", "faq_update"]:
                await self.send_notification(
                    request.user_id, 
                    f"{intent}_completed",
                    {"tenant_id": request.tenant_id, "intent": intent},
                    trace_id
                )
            
            # Build tenant profile
            tenant_profile = {
                "session_id": session_id,
                "intent_history": [intent],
                "last_interaction": datetime.now().isoformat(),
                "conversation_turns": len(self.get_conversation_history(session_id)),
                "trace_id": trace_id
            }
            
            logger.info(f"[{trace_id}] 8-service pipeline completed successfully")
            
            return SetupModeResponse(
                status="success",
                tenant_profile=tenant_profile,
                milky_response=natural_response,
                next_action=next_action
            )
            
        except Exception as e:
            logger.error(f"[{trace_id}] Setup mode pipeline error: {str(e)}")
            
            # Graceful fallback with Milky personality
            fallback_profile = {
                "session_id": session_id,
                "error": str(e),
                "trace_id": trace_id,
                "fallback": True
            }
            
            return SetupModeResponse(
                status="error",
                tenant_profile=fallback_profile,
                milky_response="Hai! Aku Milky. Aku bisa bantu kamu bikin asisten AI untuk bisnis kamu! Cerita dong apa tantangannya?",
                next_action="collect_more_info"
            )
