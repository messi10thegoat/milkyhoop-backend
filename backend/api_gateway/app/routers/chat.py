import json
import time
import re
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Dict, Optional, Any

from backend.api_gateway.app.services.ragcrud_client import RagCrudClient
from backend.api_gateway.app.services.chatbot_client import ChatbotClient
from backend.api_gateway.app.services.tenant_client import TenantParserClient
from backend.api_gateway.app.services.intent_client import IntentParserClient

# Level 13 imports - PROPER PROTO ACCESS
import grpc
import sys

sys.path.append("/app/backend/api_gateway/libs")
sys.path.append("/app/backend/api_gateway/libs/milkyhoop_protos")

# Conversation Service imports - CHAT PERSISTENCE
try:
    from conversation_service_pb2 import GetChatHistoryRequest, SaveMessageRequest
    from conversation_service_pb2_grpc import ConversationServiceStub
except ImportError as e:
    print(f"⚠️ Warning: conversation_service_pb2 import failed: {e}")
    print("   This is OK if conversation_service is not available")
    GetChatHistoryRequest = None
    ConversationServiceStub = None

router = APIRouter()


class ChatRequest(BaseModel):
    user_id: str
    session_id: str
    message: str
    tenant_id: str


class ChatResponse(BaseModel):
    reply: str


class CustomerChatRequest(BaseModel):
    session_id: str = "anonymous"
    message: str


# =====================================================
# LEVEL 13 CONTEXT CLIENT - ALL 22 METHODS
# =====================================================
class Level13ContextClient:
    """Complete Level 13 Customer Context Service Integration - All 22 Methods"""

    def __init__(self, host: str = "cust_context", port: int = 5008):
        self.host = host
        self.port = port

    # =========== TIER 1: CORE CONTEXT MANAGEMENT (6 methods) ===========

    async def create_context(
        self, session_id: str, tenant_id: str, ttl_seconds: int = 3600
    ):
        """Method 1: Create new conversation context"""
        try:
            channel = grpc.aio.insecure_channel(f"{self.host}:{self.port}")
            sys.path.append("/app/backend/api_gateway/libs/milkyhoop_protos")
            from cust_context_pb2 import CreateContextRequest
            from cust_context_pb2_grpc import CustContextServiceStub

            stub = CustContextServiceStub(channel)
            request = CreateContextRequest(
                session_id=session_id, tenant_id=tenant_id, ttl_seconds=ttl_seconds
            )
            response = await stub.CreateContext(request)
            await channel.close()
            return response.success
        except Exception as e:
            print(f"⚠️ CreateContext error: {e}")
            return False

    async def update_context(
        self, session_id: str, tenant_id: str, message: str, entities: list
    ):
        """Method 2: Update conversation context with new turn"""
        try:
            channel = grpc.aio.insecure_channel(f"{self.host}:{self.port}")
            sys.path.append("/app/backend/api_gateway/libs/milkyhoop_protos")
            from cust_context_pb2 import UpdateContextRequest
            from cust_context_pb2_grpc import CustContextServiceStub

            stub = CustContextServiceStub(channel)
            entities_json = json.dumps(entities) if entities else ""

            request = UpdateContextRequest(
                session_id=session_id,
                tenant_id=tenant_id,
                message=message,
                entities=entities_json,
            )
            response = await stub.UpdateContext(request)
            await channel.close()
            return response.success
        except Exception as e:
            print(f"⚠️ UpdateContext error: {e}")
            return False

    async def get_context(self, session_id: str, tenant_id: str):
        """Method 3: Get current context"""
        try:
            channel = grpc.aio.insecure_channel(f"{self.host}:{self.port}")
            sys.path.append("/app/backend/api_gateway/libs/milkyhoop_protos")
            from cust_context_pb2 import GetContextRequest
            from cust_context_pb2_grpc import CustContextServiceStub

            stub = CustContextServiceStub(channel)
            request = GetContextRequest(session_id=session_id, tenant_id=tenant_id)
            response = await stub.GetContext(request)
            await channel.close()
            return {"success": response.success, "context": response.context_json}
        except Exception as e:
            print(f"⚠️ GetContext error: {e}")
            return {"success": False, "context": ""}

    async def delete_context(self, session_id: str, tenant_id: str):
        """Method 4: Delete context (used for cleanup)"""
        try:
            channel = grpc.aio.insecure_channel(f"{self.host}:{self.port}")
            sys.path.append("/app/backend/api_gateway/libs/milkyhoop_protos")
            from cust_context_pb2 import DeleteContextRequest
            from cust_context_pb2_grpc import CustContextServiceStub

            stub = CustContextServiceStub(channel)
            request = DeleteContextRequest(session_id=session_id, tenant_id=tenant_id)
            response = await stub.DeleteContext(request)
            await channel.close()
            return response.success
        except Exception as e:
            print(f"⚠️ DeleteContext error: {e}")
            return False

    async def get_focused_entity(self, session_id: str, tenant_id: str):
        """Method 5: Get currently focused entity"""
        try:
            channel = grpc.aio.insecure_channel(f"{self.host}:{self.port}")
            sys.path.append("/app/backend/api_gateway/libs/milkyhoop_protos")
            from cust_context_pb2 import GetFocusedEntityRequest
            from cust_context_pb2_grpc import CustContextServiceStub

            stub = CustContextServiceStub(channel)
            request = GetFocusedEntityRequest(
                session_id=session_id, tenant_id=tenant_id
            )
            response = await stub.GetFocusedEntity(request)
            await channel.close()
            return {
                "success": response.success,
                "entity_name": response.entity_name,
                "entity_type": response.entity_type,
            }
        except Exception as e:
            print(f"⚠️ GetFocusedEntity error: {e}")
            return {"success": False, "entity_name": "", "entity_type": ""}

    async def get_session_stats(self, session_id: str, tenant_id: str):
        """Method 6: Get session statistics"""
        try:
            channel = grpc.aio.insecure_channel(f"{self.host}:{self.port}")
            sys.path.append("/app/backend/api_gateway/libs/milkyhoop_protos")
            from cust_context_pb2 import GetSessionStatsRequest
            from cust_context_pb2_grpc import CustContextServiceStub

            stub = CustContextServiceStub(channel)
            request = GetSessionStatsRequest(session_id=session_id, tenant_id=tenant_id)
            response = await stub.GetSessionStats(request)
            await channel.close()
            return {
                "success": response.success,
                "total_turns": response.total_turns,
                "entities_mentioned": response.entities_mentioned,
            }
        except Exception as e:
            print(f"⚠️ GetSessionStats error: {e}")
            return {"success": False, "total_turns": 0, "entities_mentioned": 0}

    # =========== TIER 2: EMOTIONAL & INTENT INTELLIGENCE (6 methods) ===========

    async def set_conversation_mood(
        self,
        session_id: str,
        tenant_id: str,
        mood: str,
        reason: str = "",
        confidence: float = 0.8,
    ):
        """Method 7: Set emotional context"""
        try:
            channel = grpc.aio.insecure_channel(f"{self.host}:{self.port}")
            sys.path.append("/app/backend/api_gateway/libs/milkyhoop_protos")
            from cust_context_pb2 import SetConversationMoodRequest
            from cust_context_pb2_grpc import CustContextServiceStub

            stub = CustContextServiceStub(channel)
            request = SetConversationMoodRequest(
                session_id=session_id,
                tenant_id=tenant_id,
                mood=mood,
                reason=reason,
                confidence=confidence,
            )
            response = await stub.SetConversationMood(request)
            await channel.close()
            return {
                "success": response.success,
                "detected_mood": response.detected_mood,
                "mood_confidence": response.mood_confidence,
                "previous_mood": response.previous_mood,
                "message": response.message,
            }
        except Exception as e:
            print(f"⚠️ SetConversationMood error: {e}")
            return {
                "success": False,
                "detected_mood": mood,
                "mood_confidence": 0.5,
                "previous_mood": "",
                "message": "",
            }

    async def track_user_intent(
        self,
        session_id: str,
        tenant_id: str,
        intent: str,
        confidence: float = 0.8,
        detected_from: str = "semantic_analysis",
    ):
        """Method 8: Track user intent"""
        try:
            channel = grpc.aio.insecure_channel(f"{self.host}:{self.port}")
            sys.path.append("/app/backend/api_gateway/libs/milkyhoop_protos")
            from cust_context_pb2 import TrackUserIntentRequest
            from cust_context_pb2_grpc import CustContextServiceStub

            stub = CustContextServiceStub(channel)
            request = TrackUserIntentRequest(
                session_id=session_id,
                tenant_id=tenant_id,
                intent=intent,
                confidence=confidence,
                detected_from=detected_from,
            )
            response = await stub.TrackUserIntent(request)
            await channel.close()
            return {
                "success": response.success,
                "recommended_response_style": response.recommended_response_style,
                "intent_history": response.intent_history,
            }
        except Exception as e:
            print(f"⚠️ TrackUserIntent error: {e}")
            return {
                "success": False,
                "recommended_response_style": "friendly",
                "intent_history": [],
            }

    async def get_conversation_flow(self, session_id: str, tenant_id: str):
        """Method 9: Analyze conversation flow"""
        try:
            channel = grpc.aio.insecure_channel(f"{self.host}:{self.port}")
            sys.path.append("/app/backend/api_gateway/libs/milkyhoop_protos")
            from cust_context_pb2 import GetConversationFlowRequest
            from cust_context_pb2_grpc import CustContextServiceStub

            stub = CustContextServiceStub(channel)
            request = GetConversationFlowRequest(
                session_id=session_id, tenant_id=tenant_id
            )
            response = await stub.GetConversationFlow(request)
            await channel.close()
            return {
                "success": response.success,
                "flow_stage": getattr(response, "flow_stage", "unknown"),
                "message": response.message,
            }
        except Exception as e:
            print(f"⚠️ GetConversationFlow error: {e}")
            return {"success": False, "flow_stage": "unknown", "message": ""}

    async def predict_next_user_question(self, session_id: str, tenant_id: str):
        """Method 10: Predict next user question"""
        try:
            channel = grpc.aio.insecure_channel(f"{self.host}:{self.port}")
            sys.path.append("/app/backend/api_gateway/libs/milkyhoop_protos")
            from cust_context_pb2 import PredictNextUserQuestionRequest
            from cust_context_pb2_grpc import CustContextServiceStub

            stub = CustContextServiceStub(channel)
            request = PredictNextUserQuestionRequest(
                session_id=session_id, tenant_id=tenant_id
            )
            response = await stub.PredictNextUserQuestion(request)
            await channel.close()
            return {
                "success": response.success,
                "predicted_questions": getattr(response, "predicted_questions", []),
                "message": response.message,
            }
        except Exception as e:
            print(f"⚠️ PredictNextUserQuestion error: {e}")
            return {"success": False, "predicted_questions": [], "message": ""}

    async def disambiguate_entity(
        self,
        session_id: str,
        tenant_id: str,
        ambiguous_entity: str,
        context_hint: str = "",
    ):
        """Method 11: Resolve ambiguous references"""
        try:
            channel = grpc.aio.insecure_channel(f"{self.host}:{self.port}")
            sys.path.append("/app/backend/api_gateway/libs/milkyhoop_protos")
            from cust_context_pb2 import DisambiguateEntityRequest
            from cust_context_pb2_grpc import CustContextServiceStub

            stub = CustContextServiceStub(channel)
            request = DisambiguateEntityRequest(
                session_id=session_id,
                tenant_id=tenant_id,
                ambiguous_entity=ambiguous_entity,
                context_hint=context_hint if context_hint else ambiguous_entity,
            )
            response = await stub.DisambiguateEntity(request)
            await channel.close()
            return {
                "success": response.success,
                "clarified_entity": response.clarified_entity,
                "entity_type": response.entity_type,
                "confidence": response.disambiguation_confidence,
            }
        except Exception as e:
            print(f"⚠️ DisambiguateEntity error: {e}")
            return {
                "success": False,
                "clarified_entity": "",
                "entity_type": "",
                "confidence": 0.0,
            }

    async def detect_frustration_events(self, session_id: str, tenant_id: str):
        """Method 12: Detect user frustration"""
        try:
            channel = grpc.aio.insecure_channel(f"{self.host}:{self.port}")
            sys.path.append("/app/backend/api_gateway/libs/milkyhoop_protos")
            from cust_context_pb2 import DetectFrustrationEventsRequest
            from cust_context_pb2_grpc import CustContextServiceStub

            stub = CustContextServiceStub(channel)
            request = DetectFrustrationEventsRequest(
                session_id=session_id, tenant_id=tenant_id
            )
            response = await stub.DetectFrustrationEvents(request)
            await channel.close()
            return {
                "success": response.success,
                "frustration_detected": response.frustration_detected,
                "frustration_level": response.frustration_level,
            }
        except Exception as e:
            print(f"⚠️ DetectFrustrationEvents error: {e}")
            return {
                "success": False,
                "frustration_detected": False,
                "frustration_level": 0,
            }

    # =========== TIER 3: ADVANCED CONTEXT MANAGEMENT (4 methods) ===========

    async def prioritize_important_turns(
        self, session_id: str, tenant_id: str, max_turns_to_keep: int = 10
    ):
        """Method 13: Prioritize important conversation turns"""
        try:
            channel = grpc.aio.insecure_channel(f"{self.host}:{self.port}")
            sys.path.append("/app/backend/api_gateway/libs/milkyhoop_protos")
            from cust_context_pb2 import PrioritizeImportantTurnsRequest
            from cust_context_pb2_grpc import CustContextServiceStub

            stub = CustContextServiceStub(channel)
            request = PrioritizeImportantTurnsRequest(
                session_id=session_id,
                tenant_id=tenant_id,
                max_turns_to_keep=max_turns_to_keep,
            )
            response = await stub.PrioritizeImportantTurns(request)
            await channel.close()
            return {"success": response.success}
        except Exception as e:
            print(f"⚠️ PrioritizeImportantTurns error: {e}")
            return {"success": False}

    async def summarize_context(
        self, session_id: str, tenant_id: str, summary_type: str = "brief"
    ):
        """Method 14: Summarize conversation context"""
        try:
            channel = grpc.aio.insecure_channel(f"{self.host}:{self.port}")
            sys.path.append("/app/backend/api_gateway/libs/milkyhoop_protos")
            from cust_context_pb2 import SummarizeContextRequest
            from cust_context_pb2_grpc import CustContextServiceStub

            stub = CustContextServiceStub(channel)
            request = SummarizeContextRequest(
                session_id=session_id, tenant_id=tenant_id, summary_type=summary_type
            )
            response = await stub.SummarizeContext(request)
            await channel.close()
            return {"success": response.success, "summary": response.summary}
        except Exception as e:
            print(f"⚠️ SummarizeContext error: {e}")
            return {"success": False, "summary": ""}

    async def recover_conversation_flow(
        self, session_id: str, tenant_id: str, disruption_point: str = ""
    ):
        """Method 15: Recover from conversation disruption"""
        try:
            channel = grpc.aio.insecure_channel(f"{self.host}:{self.port}")
            sys.path.append("/app/backend/api_gateway/libs/milkyhoop_protos")
            from cust_context_pb2 import RecoverConversationFlowRequest
            from cust_context_pb2_grpc import CustContextServiceStub

            stub = CustContextServiceStub(channel)
            request = RecoverConversationFlowRequest(
                session_id=session_id,
                tenant_id=tenant_id,
                disruption_point=disruption_point,
            )
            response = await stub.RecoverConversationFlow(request)
            await channel.close()
            return {"success": response.success}
        except Exception as e:
            print(f"⚠️ RecoverConversationFlow error: {e}")
            return {"success": False}

    async def adapt_tone_to_user_mood(
        self,
        session_id: str,
        tenant_id: str,
        current_mood: str,
        desired_outcome: str = "assist",
    ):
        """Method 16: Adapt response tone based on user mood"""
        try:
            channel = grpc.aio.insecure_channel(f"{self.host}:{self.port}")
            sys.path.append("/app/backend/api_gateway/libs/milkyhoop_protos")
            from cust_context_pb2 import AdaptToneToUserMoodRequest
            from cust_context_pb2_grpc import CustContextServiceStub

            stub = CustContextServiceStub(channel)
            request = AdaptToneToUserMoodRequest(
                session_id=session_id,
                tenant_id=tenant_id,
                current_mood=current_mood,
                desired_outcome=desired_outcome,
            )
            response = await stub.AdaptToneToUserMood(request)
            await channel.close()
            return {
                "success": response.success,
                "suggested_tone": response.suggested_tone,
            }
        except Exception as e:
            print(f"⚠️ AdaptToneToUserMood error: {e}")
            return {"success": False, "suggested_tone": "friendly"}

    # =========== TIER 4: PERSONA & DYNAMIC RESPONSE (3 methods) ===========

    async def trigger_response_by_persona(
        self,
        session_id: str,
        tenant_id: str,
        brand_persona: str,
        context_situation: str,
    ):
        """Method 17: Trigger response based on brand persona"""
        try:
            channel = grpc.aio.insecure_channel(f"{self.host}:{self.port}")
            sys.path.append("/app/backend/api_gateway/libs/milkyhoop_protos")
            from cust_context_pb2 import TriggerResponseByPersonaRequest
            from cust_context_pb2_grpc import CustContextServiceStub

            stub = CustContextServiceStub(channel)
            request = TriggerResponseByPersonaRequest(
                session_id=session_id,
                tenant_id=tenant_id,
                brand_persona=brand_persona,
                context_situation=context_situation,
            )
            response = await stub.TriggerResponseByPersona(request)
            await channel.close()
            return {
                "success": response.success,
                "persona_style": response.persona_style,
            }
        except Exception as e:
            print(f"⚠️ TriggerResponseByPersona error: {e}")
            return {"success": False, "persona_style": "default"}

    async def simulated_chain_of_thought(
        self, session_id: str, tenant_id: str, user_query: str
    ):
        """Method 18: Simulate chain of thought reasoning"""
        try:
            channel = grpc.aio.insecure_channel(f"{self.host}:{self.port}")
            sys.path.append("/app/backend/api_gateway/libs/milkyhoop_protos")
            from cust_context_pb2 import SimulatedChainOfThoughtRequest
            from cust_context_pb2_grpc import CustContextServiceStub

            stub = CustContextServiceStub(channel)
            request = SimulatedChainOfThoughtRequest(
                session_id=session_id, tenant_id=tenant_id, query=user_query
            )
            response = await stub.SimulatedChainOfThought(request)
            await channel.close()
            return {
                "success": response.success,
                "reasoning_steps": response.reasoning_steps,
            }
        except Exception as e:
            print(f"⚠️ SimulatedChainOfThought error: {e}")
            return {"success": False, "reasoning_steps": 0}

    async def auto_intent_correction(
        self,
        session_id: str,
        tenant_id: str,
        misunderstood_intent: str,
        correction_signal: str = "",
    ):
        """Method 19: Auto-correct misunderstood intent"""
        try:
            channel = grpc.aio.insecure_channel(f"{self.host}:{self.port}")
            sys.path.append("/app/backend/api_gateway/libs/milkyhoop_protos")
            from cust_context_pb2 import AutoIntentCorrectionRequest
            from cust_context_pb2_grpc import CustContextServiceStub

            stub = CustContextServiceStub(channel)
            request = AutoIntentCorrectionRequest(
                session_id=session_id,
                tenant_id=tenant_id,
                misunderstood_intent=misunderstood_intent,
                correction_signal=correction_signal,
            )
            response = await stub.AutoIntentCorrection(request)
            await channel.close()
            return {
                "success": response.success,
                "corrected_intent": response.corrected_intent,
            }
        except Exception as e:
            print(f"⚠️ AutoIntentCorrection error: {e}")
            return {"success": False, "corrected_intent": misunderstood_intent}

    # =========== TIER 5: FINE-GRAINED INTELLIGENCE (3 methods) ===========

    async def detect_product_mentioned(
        self, session_id: str, tenant_id: str, conversation_turn: str
    ):
        """Method 20: Detect products mentioned in conversation"""
        try:
            channel = grpc.aio.insecure_channel(f"{self.host}:{self.port}")
            sys.path.append("/app/backend/api_gateway/libs/milkyhoop_protos")
            from cust_context_pb2 import DetectProductMentionedRequest
            from cust_context_pb2_grpc import CustContextServiceStub

            stub = CustContextServiceStub(channel)
            request = DetectProductMentionedRequest(
                session_id=session_id,
                tenant_id=tenant_id,
                conversation_turn=conversation_turn,
            )
            response = await stub.DetectProductMentioned(request)
            await channel.close()
            return {
                "success": response.success,
                "products": list(response.products)
                if hasattr(response, "products")
                else [],
            }
        except Exception as e:
            print(f"⚠️ DetectProductMentioned error: {e}")
            return {"success": False, "products": []}

    async def find_lead_signals(self, session_id: str, tenant_id: str):
        """Method 21: Find sales lead signals"""
        try:
            channel = grpc.aio.insecure_channel(f"{self.host}:{self.port}")
            sys.path.append("/app/backend/api_gateway/libs/milkyhoop_protos")
            from cust_context_pb2 import FindLeadSignalsRequest
            from cust_context_pb2_grpc import CustContextServiceStub

            stub = CustContextServiceStub(channel)
            request = FindLeadSignalsRequest(session_id=session_id, tenant_id=tenant_id)
            response = await stub.FindLeadSignals(request)
            await channel.close()
            return {"success": response.success, "lead_score": response.lead_score}
        except Exception as e:
            print(f"⚠️ FindLeadSignals error: {e}")
            return {"success": False, "lead_score": 0}

    async def capture_feedback_from_conversation(self, session_id: str, tenant_id: str):
        """Method 22: Capture implicit feedback from conversation"""
        try:
            channel = grpc.aio.insecure_channel(f"{self.host}:{self.port}")
            sys.path.append("/app/backend/api_gateway/libs/milkyhoop_protos")
            from cust_context_pb2 import CaptureFeedbackFromConversationRequest
            from cust_context_pb2_grpc import CustContextServiceStub

            stub = CustContextServiceStub(channel)
            request = CaptureFeedbackFromConversationRequest(
                session_id=session_id, tenant_id=tenant_id
            )
            response = await stub.CaptureFeedbackFromConversation(request)
            await channel.close()
            return {
                "success": response.success,
                "feedback_type": response.feedback_type,
            }
        except Exception as e:
            print(f"⚠️ CaptureFeedbackFromConversation error: {e}")
            return {"success": False, "feedback_type": "none"}


# =====================================================
# CONVERSATION SERVICE CLIENT - CHAT PERSISTENCE
# =====================================================
class ConversationServiceClient:
    """Client for conversation_service - chat persistence"""

    def __init__(self, host: str = "conversation_service", port: int = 5002):
        self.host = host
        self.port = port

    async def get_chat_history(
        self, user_id: str, tenant_id: str, limit: int = 30, offset: int = 0
    ):
        """Get paginated chat history"""
        if GetChatHistoryRequest is None or ConversationServiceStub is None:
            print(
                "❌ [ConversationServiceClient] conversation_service_pb2 not available"
            )
            raise Exception("conversation_service protobuf not available")

        channel = None
        try:
            print(
                f"📖 [ConversationServiceClient] GetChatHistory: user={user_id}, tenant={tenant_id}, limit={limit}, offset={offset}"
            )
            channel = grpc.aio.insecure_channel(f"{self.host}:{self.port}")

            stub = ConversationServiceStub(channel)
            request = GetChatHistoryRequest(
                user_id=user_id, tenant_id=tenant_id, limit=limit, offset=offset
            )

            print(
                f"📞 [ConversationServiceClient] Calling gRPC: {self.host}:{self.port}"
            )
            response = await stub.GetChatHistory(request)
            print(
                f"✅ [ConversationServiceClient] Response received: status={response.status}, messages={len(response.messages)}"
            )

            # Convert to dict
            messages = []
            for msg in response.messages:
                messages.append(
                    {
                        "id": msg.id,
                        "user_id": msg.user_id,
                        "tenant_id": msg.tenant_id,
                        "message": msg.message,
                        "response": msg.response,
                        "intent": msg.intent,
                        "metadata_json": msg.metadata_json,
                        "created_at": msg.created_at,
                    }
                )

            result = {
                "status": response.status,
                "messages": messages,
                "total_count": response.total_count,
                "has_more": response.has_more,
            }
            print(f"✅ [ConversationServiceClient] Returning {len(messages)} messages")
            return result

        except grpc.RpcError as e:
            print(
                f"❌ [ConversationServiceClient] gRPC error: code={e.code()}, details={e.details()}"
            )
            raise Exception(f"gRPC error: {e.code()} - {e.details()}")
        except Exception as e:
            print(
                f"❌ [ConversationServiceClient] GetChatHistory error: {type(e).__name__}: {str(e)}"
            )
            import traceback

            traceback.print_exc()
            raise Exception(f"Failed to get chat history: {str(e)}")
        finally:
            if channel:
                try:
                    await channel.close()
                except:
                    pass


# =====================================================
# HELPER FUNCTIONS
# =====================================================


def detect_message_mood(message: str) -> tuple:
    """Simple mood detection from user message"""
    message_lower = message.lower()

    # Frustrated/angry indicators
    if any(
        word in message_lower
        for word in [
            "susah",
            "ribet",
            "lama",
            "mahal",
            "sulit",
            "rumit",
            "lambat",
            "gak bisa",
            "tidak bisa",
            "error",
            "gagal",
        ]
    ):
        return "frustrated", "negative_keywords_detected"

    # Happy/satisfied indicators
    elif any(
        word in message_lower
        for word in [
            "bagus",
            "oke",
            "terima kasih",
            "makasih",
            "mantap",
            "keren",
            "sip",
            "good",
            "baik",
            "senang",
        ]
    ):
        return "satisfied", "positive_keywords_detected"

    # Confused/questioning indicators
    elif any(
        word in message_lower
        for word in [
            "gimana",
            "bagaimana",
            "bingung",
            "tidak tahu",
            "gak ngerti",
            "tidak mengerti",
            "tidak paham",
        ]
    ):
        return "confused", "question_pattern_detected"

    # Urgent/impatient indicators
    elif any(
        word in message_lower
        for word in [
            "cepat",
            "urgent",
            "penting",
            "segera",
            "buru-buru",
            "sekarang",
            "asap",
        ]
    ):
        return "urgent", "urgency_keywords_detected"

    # Default neutral
    else:
        return "neutral", "no_specific_mood_indicators"


def detect_user_intent(message: str, entities: list) -> str:
    """Intent detection based on message and entities"""
    message_lower = message.lower()

    # Product inquiry intents
    if any(entity.get("type") == "product" for entity in entities):
        if any(word in message_lower for word in ["harga", "biaya", "berapa"]):
            return "product_pricing_inquiry"
        elif any(
            word in message_lower for word in ["syarat", "dokumen", "requirement"]
        ):
            return "product_requirements_inquiry"
        elif any(word in message_lower for word in ["fitur", "benefit", "keuntungan"]):
            return "product_features_inquiry"
        else:
            return "general_product_inquiry"

    # Service-related intents
    elif any(word in message_lower for word in ["buka rekening", "daftar", "apply"]):
        return "account_opening_intent"
    elif any(word in message_lower for word in ["complaint", "keluhan", "masalah"]):
        return "complaint_intent"
    elif any(word in message_lower for word in ["lokasi", "cabang", "atm"]):
        return "location_inquiry"

    # Reference resolution intents
    elif any(word in message_lower for word in ["yang itu", "yang tadi", "itu"]):
        return "reference_resolution_intent"

    # Default
    else:
        return "general_inquiry"


def extract_entities_from_content(content: str, query: str) -> list:
    """Extract entities from FAQ content for Level 13 context"""
    entities = []

    # Extract product/service mentions
    products = [
        "tahapan xpresi",
        "tahapan bca",
        "tabunganku",
        "tapres",
        "simpel",
        "tahapan gold",
    ]
    for product in products:
        if product.lower() in content.lower() or product.lower() in query.lower():
            entities.append(
                {
                    "type": "product",
                    "name": product.title(),
                    "details": {"mentioned_in": "faq_response", "turn": "current"},
                }
            )

    # Extract pricing entities
    if any(word in query.lower() for word in ["harga", "biaya", "setoran", "admin"]):
        entities.append(
            {
                "type": "intent",
                "name": "pricing_inquiry",
                "details": {"category": "pricing", "user_intent": "price_information"},
            }
        )

    return entities


def extract_answer_only(faq_content: str) -> str:
    """Extract only the answer part from FAQ content"""
    if "A:" in faq_content:
        answer_part = faq_content.split("A:", 1)[1].strip()
        return answer_part
    return faq_content


# =====================================================
# SALES INTENT PARSER - HYBRID CONVERSATIONAL POS
# =====================================================


def parse_sales_intent(message: str) -> Optional[Dict[str, Any]]:
    """
    Parse sales intent from natural language.

    Examples:
    - "Jual Aqua 2 botol cash" → opens POS with Aqua x2, tunai
    - "Bu Siti beli beras 25kg, bon" → opens POS with beras x25, hutang, customer Bu Siti
    - "Transaksi Indomie 5 bungkus" → opens POS with Indomie x5

    Returns:
        dict with intent data if sales detected, None otherwise
    """
    message_lower = message.lower().strip()

    # Sales trigger keywords
    SALES_TRIGGERS = [
        r"^jual\s+",  # "jual ..."
        r"^transaksi\s+",  # "transaksi ..."
        r"^penjualan\s+",  # "penjualan ..."
        r"^catat\s+penjualan",  # "catat penjualan ..."
        r"\bbeli\s+",  # "... beli ..." (customer buying)
        r"^kasir\s+",  # "kasir ..."
    ]

    # Check if message matches sales pattern
    is_sales = False
    for pattern in SALES_TRIGGERS:
        if re.search(pattern, message_lower):
            is_sales = True
            break

    if not is_sales:
        return None

    # Extract items from message
    # Pattern: product_name quantity unit
    # Examples: "Aqua 2 botol", "beras 25 kg", "Indomie goreng 5 bungkus"
    items = []

    # Multi-word product patterns with quantity
    ITEM_PATTERNS = [
        # "Indomie goreng 5 bungkus" - product with adjective
        r"([A-Za-z][A-Za-z\s]{1,30}?)\s+(\d+(?:[.,]\d+)?)\s*(pcs|botol|bungkus|kg|gram|g|dus|box|karton|lusin|liter|l|pack|sachet|biji|buah|unit)?",
        # Simpler: "Aqua 2"
        r"([A-Za-z][A-Za-z]{2,15})\s+(\d+(?:[.,]\d+)?)",
    ]

    # Try to extract items
    for pattern in ITEM_PATTERNS:
        matches = re.finditer(pattern, message, re.IGNORECASE)
        for match in matches:
            product_name = match.group(1).strip()
            quantity = float(match.group(2).replace(",", "."))
            unit = (
                match.group(3) if len(match.groups()) > 2 and match.group(3) else "pcs"
            )

            # Skip common non-product words
            skip_words = [
                "jual",
                "beli",
                "transaksi",
                "kasir",
                "penjualan",
                "catat",
                "cash",
                "tunai",
                "qris",
                "bon",
                "hutang",
                "transfer",
                "bu",
                "pak",
                "ibu",
                "bapak",
            ]
            if product_name.lower() not in skip_words:
                items.append(
                    {
                        "productQuery": product_name,
                        "qty": int(quantity) if quantity == int(quantity) else quantity,
                        "unit": unit,
                    }
                )

    # Extract payment method
    payment_method = None
    if re.search(r"\b(cash|tunai|kontan)\b", message_lower):
        payment_method = "tunai"
    elif re.search(r"\b(qris|qr|scan)\b", message_lower):
        payment_method = "qris"
    elif re.search(r"\b(bon|hutang|kredit|piutang|nanti)\b", message_lower):
        payment_method = "hutang"
    elif re.search(r"\b(transfer|tf|bank)\b", message_lower):
        payment_method = "transfer"

    # Extract customer name (Bu/Pak/Ibu/Bapak + name)
    customer_name = None
    customer_match = re.search(
        r"\b(bu|pak|ibu|bapak|mbak|mas)\s+([A-Za-z]+)", message_lower
    )
    if customer_match:
        title = customer_match.group(1).title()
        name = customer_match.group(2).title()
        customer_name = f"{title} {name}"

    # Calculate confidence
    confidence = 0.0
    if items:
        confidence += 0.5  # Has items
    if payment_method:
        confidence += 0.25  # Has payment method
    if customer_name:
        confidence += 0.15  # Has customer
    if is_sales:
        confidence += 0.1  # Has sales keyword

    # Only return if we have at least items
    if not items:
        return None

    return {
        "intent": "sales_pos",
        "items": items,
        "payment_method": payment_method,
        "customer_name": customer_name,
        "confidence": min(confidence, 1.0),
        "raw_message": message,
    }


# =====================================================
# MAIN ENDPOINTS
# =====================================================


@router.post("/chat/")
async def chat_endpoint(req: ChatRequest):
    """Setup Mode Chat - Business owner manages chatbot"""
    chatbot_client = ChatbotClient()
    intent_client = IntentParserClient()

    try:
        parsed = await intent_client.parse(
            user_id=req.user_id, reason="message_analysis"
        )
        intent_type = parsed.get("intent_type", "unknown")
        entities = parsed.get("entities", {})

        chatbot_response = await chatbot_client.chat(
            req.user_id, req.session_id, req.message, req.tenant_id
        )

        return ChatResponse(reply=chatbot_response)

    except Exception as e:
        import traceback

        traceback.print_exc()
        raise HTTPException(
            status_code=500, detail=f"gRPC or HTTP call failed: {str(e)}"
        )


@router.post("/tenant/{tenant_id}/chat")
async def customer_chat_endpoint(tenant_id: str, req: CustomerChatRequest):
    """🚀 ENHANCED Customer Mode Chat - WITH CONFIDENCE ENGINE + SALES INTENT"""

    # Performance tracking
    start_time = time.time()

    # ========== PHASE 0: SALES INTENT DETECTION ==========
    # Check for sales intent FIRST - shortcut to POS
    sales_intent = parse_sales_intent(req.message)

    if sales_intent and sales_intent.get("confidence", 0) >= 0.5:
        # 🎯 SALES INTENT DETECTED - Return action payload for frontend
        print(f"\n{'='*60}")
        print(f"🛒 SALES INTENT DETECTED - Session: {req.session_id}")
        print(f"📝 Query: {req.message}")
        print(f"🎯 Items: {sales_intent.get('items', [])}")
        print(f"💳 Payment: {sales_intent.get('payment_method', 'not specified')}")
        print(f"👤 Customer: {sales_intent.get('customer_name', 'not specified')}")
        print(f"📊 Confidence: {sales_intent.get('confidence', 0):.2f}")
        print(f"{'='*60}")

        # Build friendly response message
        items_text = ", ".join(
            [
                f"{item['productQuery']} x{item['qty']}"
                for item in sales_intent.get("items", [])
            ]
        )
        payment_text = sales_intent.get("payment_method", "")
        customer_text = sales_intent.get("customer_name", "")

        response_parts = ["Siap! Membuka POS"]
        if items_text:
            response_parts.append(f"dengan {items_text}")
        if payment_text:
            response_parts.append(f"({payment_text})")
        if customer_text:
            response_parts.append(f"untuk {customer_text}")

        milky_response = " ".join(response_parts) + "..."

        # Calculate processing time
        processing_time = round((time.time() - start_time) * 1000, 2)

        # Return with action payload for frontend
        return {
            "status": "success",
            "reply": milky_response,
            "intent": "sales_pos",
            "action": {
                "type": "open_pos",
                "payload": {
                    "items": sales_intent.get("items", []),
                    "paymentMethod": sales_intent.get("payment_method"),
                    "customerName": sales_intent.get("customer_name"),
                    "navigateTo": "pos",  # Always go to POS for user validation
                },
            },
            "confidence_metadata": {
                "confidence_score": sales_intent.get("confidence", 0),
                "route_taken": "sales_intent_shortcut",
                "cost_estimate": 0.0,
                "tokens_used": 0,
                "optimization_active": True,
            },
            "processing_time_ms": processing_time,
            "session_id": req.session_id,
            "tenant_id": tenant_id,
        }

    # ========== CONTINUE WITH NORMAL CHAT FLOW ==========
    # Initialize clients - ADD TENANT_PARSER CLIENT
    rag_crud_client = RagCrudClient()
    level13_client = Level13ContextClient()
    tenant_parser_client = TenantParserClient()  # 🎯 CONFIDENCE ENGINE CLIENT

    # Method tracking
    method_results = {}
    confidence_data = {}  # 🎯 CONFIDENCE METADATA

    try:
        print(f"\n{'='*60}")
        print(f"🚀 Enhanced Customer Mode - Session: {req.session_id}")
        print(f"📝 Query: {req.message}")
        print("🎯 Confidence Engine: ACTIVE")
        print(f"{'='*60}")

        # ========== PHASE 1: CONTEXT INITIALIZATION ==========
        print("\n📂 PHASE 1: Context Initialization")

        # Method 1: CreateContext
        context_created = await level13_client.create_context(
            session_id=req.session_id, tenant_id=tenant_id, ttl_seconds=3600
        )
        method_results["CreateContext"] = context_created
        print(f"  1. CreateContext: {'✅' if context_created else '❌'}")

        # ========== PHASE 2: MOOD & INTENT DETECTION ==========
        print("\n🎭 PHASE 2: Mood & Intent Detection")

        # Method 7: SetConversationMood
        detected_mood, mood_reason = detect_message_mood(req.message)
        mood_result = await level13_client.set_conversation_mood(
            session_id=req.session_id,
            tenant_id=tenant_id,
            mood=detected_mood,
            reason=mood_reason,
        )
        method_results["SetConversationMood"] = mood_result.get("success", False)
        print(
            f"  7. SetConversationMood: {'✅' if mood_result.get('success') else '❌'} [{detected_mood}]"
        )

        # Extract initial entities
        entities = extract_entities_from_content("", req.message)

        # Method 8: TrackUserIntent
        detected_intent = detect_user_intent(req.message, entities)
        intent_result = await level13_client.track_user_intent(
            session_id=req.session_id,
            tenant_id=tenant_id,
            intent=detected_intent,
            confidence=0.8,
            detected_from="message_content",
        )
        method_results["TrackUserIntent"] = intent_result.get("success", False)
        print(
            f"  8. TrackUserIntent: {'✅' if intent_result.get('success') else '❌'} [{detected_intent}]"
        )

        # ========== PHASE 3: ENHANCED CONFIDENCE ENGINE PROCESSING ==========
        print("\n🎯 PHASE 3: Enhanced Confidence Engine Processing")

        try:
            # 🎯 CALL ENHANCED TENANT_PARSER WITH CONFIDENCE ENGINE
            tenant_parser_response = await tenant_parser_client.parse_customer_query(
                tenant_id=tenant_id, message=req.message, session_id=req.session_id
            )

            print("  🚀 Enhanced tenant_parser: ✅ CALLED")

            # Parse tenant_parser response
            if (
                tenant_parser_response
                and isinstance(tenant_parser_response, dict)
                and "confidence_metadata" in tenant_parser_response
            ):
                # Response is already dict, use directly
                parsed_result = tenant_parser_response

                # Extract response content
                natural_response = parsed_result.get(
                    "answer", "Informasi tidak tersedia"
                )

                # 🎯 EXTRACT CONFIDENCE METADATA
                confidence_metadata = parsed_result.get("confidence_metadata", {})
                if confidence_metadata:
                    confidence_data = {
                        "confidence_score": confidence_metadata.get(
                            "confidence_score", 0.0
                        ),
                        "route_taken": confidence_metadata.get(
                            "route_taken", "unknown"
                        ),
                        "cost_estimate": confidence_metadata.get("cost_estimate", 0.0),
                        "tokens_used": confidence_metadata.get("tokens_used", 0),
                        "optimization_active": confidence_metadata.get(
                            "optimization_active", True
                        ),
                    }
                    print(
                        f"  🎯 Confidence Score: {confidence_data['confidence_score']:.3f}"
                    )
                    print(f"  🎯 Route Taken: {confidence_data['route_taken']}")
                    print(f"  💰 Cost Estimate: Rp {confidence_data['cost_estimate']}")
                else:
                    confidence_data = {
                        "confidence_score": 0.0,
                        "route_taken": "legacy_fallback",
                        "cost_estimate": 0.0,
                        "tokens_used": 0,
                        "optimization_active": False,
                    }

                method_results["EnhancedTenantParser"] = True

            else:
                print("  ✅ Enhanced tenant_parser: SUCCESS")
                # Extract response from tenant_parser
                if isinstance(tenant_parser_response, dict):
                    natural_response = tenant_parser_response.get(
                        "answer", "FAQ content tidak ditemukan"
                    )
                    confidence_data = {
                        "confidence_score": 0.8,
                        "route_taken": "enhanced_tenant_parser",
                        "cost_estimate": 0.0,
                        "tokens_used": 0,
                        "optimization_active": True,
                    }
                else:
                    natural_response = str(tenant_parser_response)
                    confidence_data = {
                        "confidence_score": 0.8,
                        "route_taken": "enhanced_tenant_parser",
                        "cost_estimate": 0.0,
                        "tokens_used": 0,
                        "optimization_active": True,
                    }
                method_results["EnhancedTenantParser"] = True
        except Exception as e:
            print(f"  ❌ Enhanced tenant_parser error: {str(e)}")
            # Fallback to original FAQ search if enhanced engine fails
            print("\n📚 FALLBACK: Direct FAQ Search")

            search_results = await rag_crud_client.search_documents(
                tenant_id=tenant_id, query=req.message, limit=3
            )

            if search_results and len(search_results) > 0:
                best_result = search_results[0]
                natural_response = extract_answer_from_faq(
                    best_result.get("content", "")
                )
            else:
                natural_response = f"Maaf, informasi yang Anda cari untuk {tenant_id} belum tersedia saat ini."

            confidence_data = {
                "confidence_score": 0.0,
                "route_taken": "fallback_faq",
                "cost_estimate": 0.0,
                "tokens_used": 0,
                "optimization_active": False,
            }
            method_results["EnhancedTenantParser"] = False

        # ========== PHASE 4: CONTEXT UPDATE & RESPONSE ==========
        print("\n📝 PHASE 4: Context Update & Response")

        # Method 2: UpdateContext
        context_updated = await level13_client.update_context(
            session_id=req.session_id,
            tenant_id=tenant_id,
            message=req.message,
            entities=entities,
        )
        method_results["UpdateContext"] = context_updated
        print(f"  2. UpdateContext: {'✅' if context_updated else '❌'}")

        # Performance metrics
        end_time = time.time()
        processing_time = round((end_time - start_time) * 1000, 2)

        print("\n📊 PROCESSING COMPLETE")
        print(f"  ⏱️  Processing Time: {processing_time}ms")
        print(
            f"  🎯 Confidence Engine: {'✅ ACTIVE' if confidence_data.get('optimization_active') else '❌ INACTIVE'}"
        )
        print(f"  💰 Estimated Cost: Rp {confidence_data.get('cost_estimate', 0)}")
        print(f"  🚀 Route: {confidence_data.get('route_taken', 'unknown')}")

        # 🎯 ENHANCED RESPONSE WITH CONFIDENCE METADATA
        response_data = {
            "confidence": confidence_data.get("confidence_score", 0.0),
            "routing": confidence_data.get(
                "route", confidence_data.get("route_taken", "unknown")
            ),
            "confidence": confidence_data.get("confidence_score", 0.0),
            "routing": confidence_data.get(
                "route", confidence_data.get("route_taken", "unknown")
            ),
            "reply": natural_response,
            "confidence_metadata": confidence_data,  # 🎯 NEW: CONFIDENCE DATA
            "processing_time_ms": processing_time,
            "methods_executed": method_results,
            "session_id": req.session_id,
            "tenant_id": tenant_id,
        }

        return response_data

    except Exception as e:
        print(f"❌ Customer chat error: {str(e)}")
        return ChatResponse(
            reply=f"Maaf ada kendala untuk {tenant_id}, silakan coba lagi."
        )


# Helper function for FAQ answer extraction
def extract_answer_from_faq(content: str) -> str:
    """Extract clean answer from FAQ content"""
    if content.startswith("Q:") and "\nA:" in content:
        return content.split("\nA:", 1)[1].strip()
    return content.strip()


# =====================================================
# CHAT HISTORY ENDPOINT - GOAL 1 COMPLETION
# =====================================================
@router.get("/history")
async def get_chat_history(
    user_id: str, tenant_id: str, limit: int = 30, offset: int = 0
):
    """
    Get paginated chat history for user

    Query Parameters:
    - user_id: User identifier (required)
    - tenant_id: Tenant identifier (required)
    - limit: Number of messages to return (default: 30, max: 100)
    - offset: Pagination offset (default: 0)

    Returns:
    - messages: List of chat messages (newest first)
    - total_count: Total number of messages
    - has_more: Boolean indicating if more messages available
    """
    print(
        f"📥 [get_chat_history] Request: user_id={user_id}, tenant_id={tenant_id}, limit={limit}, offset={offset}"
    )

    # Validate limit
    if limit > 100:
        limit = 100
    if limit < 1:
        limit = 30

    conversation_client = ConversationServiceClient()

    try:
        print(
            "📞 [get_chat_history] Calling ConversationServiceClient.get_chat_history()"
        )
        result = await conversation_client.get_chat_history(
            user_id=user_id, tenant_id=tenant_id, limit=limit, offset=offset
        )

        print(
            f"✅ [get_chat_history] Success: {len(result.get('messages', []))} messages"
        )

        return {
            "status": "success",
            "data": {
                "messages": result["messages"],
                "total_count": result["total_count"],
                "has_more": result["has_more"],
                "limit": limit,
                "offset": offset,
            },
        }
    except Exception as e:
        print(f"❌ [get_chat_history] Error: {type(e).__name__}: {str(e)}")
        import traceback

        traceback.print_exc()
        raise HTTPException(
            status_code=500, detail=f"Failed to retrieve chat history: {str(e)}"
        )


# Phase 2 Authentication Testing - GET Endpoints
@router.get("/")
async def chat_get_test():
    """
    GET endpoint for chat - Phase 2 authentication testing
    Protected endpoint requiring Bearer token authentication
    """
    return {
        "message": "Chat GET endpoint - Authentication successful",
        "endpoint": "/chat/",
        "method": "GET",
        "authentication": "Bearer token validated",
        "phase": "Phase 2 - 100% Complete",
    }


@router.get("/test")
async def chat_test_endpoint():
    """Additional GET test endpoint"""
    return {
        "message": "Chat test endpoint operational",
        "status": "protected",
        "authentication": "required",
    }
