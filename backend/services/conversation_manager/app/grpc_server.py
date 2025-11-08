"""
conversation_manager - Redis-backed State Machine for Setup Workflow
Purpose: Track multi-turn conversation state and manage setup progress
"""
import asyncio
import signal
import logging
import os
import json
from typing import Optional, Dict, Any, List
from datetime import datetime

import grpc
from grpc import aio
from grpc_health.v1 import health, health_pb2, health_pb2_grpc
from google.protobuf import empty_pb2

from app.config import settings
from app import conversation_manager_pb2_grpc as pb_grpc
from app import conversation_manager_pb2 as pb
from app.redis_client import ConversationRedisClient
from ragllm_service_pb2_grpc import RagLlmServiceStub
from ragllm_service_pb2 import GenerateAnswerRequest

# Logging config
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(settings.SERVICE_NAME)


# ============================================
# STATE MACHINE CONFIGURATION
# ============================================

VALID_STATES = [
    "initial",
    "welcome",
    "collecting_info",
    "confirming_data",
    "generating_faqs",
    "review_faqs",
    "setup_complete"
]

STATE_TRANSITIONS = {
    "initial": ["welcome", "collecting_info"],
    "welcome": ["collecting_info"],
    "collecting_info": ["confirming_data", "collecting_info"],  # Can loop
    "confirming_data": ["generating_faqs", "collecting_info"],  # Can go back
    "generating_faqs": ["review_faqs"],
    "review_faqs": ["setup_complete", "generating_faqs"],  # Can regenerate
    "setup_complete": []  # Terminal state
}

REQUIRED_FIELDS = {
    "welcome": [],
    "collecting_info": ["business_type"],
    "confirming_data": ["business_type", "business_name"],
    "generating_faqs": ["business_type", "business_name", "products_services"],
    "review_faqs": ["business_type", "business_name", "products_services"],
    "setup_complete": ["business_type", "business_name", "products_services"]
}


# ============================================
# GRPC SERVICE IMPLEMENTATION
# ============================================

class ConversationManagerServicer(pb_grpc.ConversationManagerServicer):
    """
    State machine for setup workflow with Redis persistence
    """
    
    def __init__(self, redis_client: ConversationRedisClient):
        self.redis = redis_client
        
        # Initialize LLM client for intelligent question generation
        self.llm_channel = grpc.aio.insecure_channel(
            os.getenv("RAGLLM_ADDRESS", "ragllm_service:5000")
        )
        self.llm_stub = RagLlmServiceStub(self.llm_channel)
        
        logger.info("ConversationManagerServicer initialized with LLM client")
    
    def _calculate_progress(self, state: str, extracted_data: dict) -> int:
        """
        Calculate progress percentage based on state and data completeness
        
        State-based progress:
        - initial: 0%
        - welcome: 10%
        - collecting_info: 30-60% (base 30% + 5% per filled field)
        - confirming_setup: 80%
        - setup_complete: 100%
        """
        # Base progress by state
        state_progress = {
            "initial": 0,
            "welcome": 10,
            "collecting_info": 30,
            "confirming_setup": 80,
            "setup_complete": 100
        }
        
        base_progress = state_progress.get(state, 0)
        
        # If collecting_info, add bonus for filled fields
        if state == "collecting_info" and extracted_data:
            required_fields = [
                "business_type", "business_name", "products_services",
                "operating_hours", "location", "pricing_info", "target_customers"
            ]
            filled_count = sum(1 for field in required_fields if extracted_data.get(field))
            # Add 5% per field (max 6 fields = 30% bonus)
            field_bonus = min(filled_count * 5, 30)
            return min(base_progress + field_bonus, 60)  # Cap at 60%
        
        return base_progress

    async def GetContext(self, request: pb.GetContextRequest, context) -> pb.GetContextResponse:
        """
        Retrieve current conversation context from Redis
        
        Returns full session state including:
        - Current state
        - Extracted business data
        - Conversation history
        - Session metadata
        """
        try:
            session_id = request.session_id
            logger.info(f"GetContext: session_id={session_id}")
            
            # Get session from Redis
            session = await self.redis.get_session(session_id)
            
            if not session:
                # New session - return initial state
                logger.info(f"New session {session_id}, returning initial state")
                return pb.GetContextResponse(
                    status="success",
                    session_id=session_id,
                    current_state="initial",
                    extracted_data_json="{}",
                    progress_percentage=0,
                    conversation_history=[],
                    created_at=datetime.utcnow().isoformat(),
                    updated_at=datetime.utcnow().isoformat(),
                    ttl_remaining=3600
                )
            
            # Build conversation history
            history = []
            for turn in session.get("conversation_history", []):
                history.append(pb.ConversationTurn(
                    role=turn.get("role", ""),
                    message=turn.get("message", ""),
                    timestamp=turn.get("timestamp", "")
                ))
            
            # Get TTL
            ttl = await self.redis.get_session_ttl(session_id)
            
                        # Calculate progress
            extracted_data = session.get("extracted_data", {})
            current_state = session.get("state", "initial")
            progress = self._calculate_progress(current_state, extracted_data)
            logger.info(f"[DEBUG] GetContext - state: {current_state}, extracted_data: {extracted_data}, progress: {progress}")
            
            return pb.GetContextResponse(
                status="success",
                session_id=session_id,
                current_state=current_state,
                extracted_data_json=json.dumps(extracted_data),
                conversation_history=history,
                created_at=session.get("created_at", ""),
                updated_at=session.get("updated_at", ""),
                ttl_remaining=ttl if ttl > 0 else 0,
                progress_percentage=progress
            )
            
        except Exception as e:
            logger.error(f"GetContext error: {e}")
            await context.abort(
                grpc.StatusCode.INTERNAL,
                f"Failed to get context: {str(e)}"
            )
    
    async def UpdateState(self, request: pb.UpdateStateRequest, context) -> pb.UpdateStateResponse:
        """
        Update conversation state with validation
        
        Validates state transition and stores new state in Redis
        """
        try:
            session_id = request.session_id
            new_state = request.new_state
            
            logger.info(f"UpdateState: session_id={session_id}, new_state={new_state}")
            
            # Validate state
            if new_state not in VALID_STATES:
                return pb.UpdateStateResponse(
                    status="error",
                    message=f"Invalid state: {new_state}",
                    transition_allowed=False
                )
            
            # Get current session
            session = await self.redis.get_session(session_id)
            current_state = session.get("state", "initial") if session else "initial"
            
            # Validate transition
            allowed_transitions = STATE_TRANSITIONS.get(current_state, [])
            if new_state not in allowed_transitions and new_state != current_state:
                logger.warning(f"Invalid transition: {current_state} -> {new_state}")
                return pb.UpdateStateResponse(
                    status="error",
                    previous_state=current_state,
                    current_state=current_state,
                    message=f"Cannot transition from {current_state} to {new_state}",
                    transition_allowed=False
                )
            
            # Prepare session data
            if not session:
                session = {
                    "session_id": session_id,
                    "user_id": request.user_id,
                    "tenant_id": request.tenant_id,
                    "state": new_state,
                    "extracted_data": {},
                    "conversation_history": []
                }
            else:
                session["state"] = new_state
                # Ensure extracted_data exists for existing sessions
                if "extracted_data" not in session:
                    session["extracted_data"] = {}
                # Ensure conversation_history exists for existing sessions
                if "conversation_history" not in session:
                    session["conversation_history"] = []
                # Ensure conversation_history exists for existing sessions
                if "conversation_history" not in session:
                    session["conversation_history"] = []
            
            # Add optional data
            if request.data_json:
                try:
                    data = json.loads(request.data_json)
                    extracted_before = session.get("extracted_data", {})
                    logger.info(f"[DEBUG] UpdateState BEFORE - extracted_data: {extracted_before}")
                    logger.info(f"[DEBUG] UpdateState incoming data: {data}")
                    session["extracted_data"].update(data)
                    extracted_after = session["extracted_data"]
                    logger.info(f"[DEBUG] UpdateState AFTER - extracted_data: {extracted_after}")
                except json.JSONDecodeError:
                    logger.warning(f"Invalid JSON in data_json: {request.data_json}")
            
            # Add conversation turn if message provided
            if request.message:
                turn = {
                    "role": "user",
                    "message": request.message,
                    "timestamp": datetime.utcnow().isoformat()
                }
                session["conversation_history"].append(turn)
            
            # Store in Redis
            success = await self.redis.set_session(session_id, session)
            
            if success:
                logger.info(f"State updated: {current_state} -> {new_state}")
                return pb.UpdateStateResponse(
                    status="success",
                    previous_state=current_state,
                    current_state=new_state,
                    message=f"State updated to {new_state}",
                    transition_allowed=True
                )
            else:
                return pb.UpdateStateResponse(
                    status="error",
                    message="Failed to store session",
                    transition_allowed=False
                )
            
        except Exception as e:
            logger.error(f"UpdateState error: {e}")
            await context.abort(
                grpc.StatusCode.INTERNAL,
                f"Failed to update state: {str(e)}"
            )
    
    async def StoreExtractedData(self, request: pb.StoreExtractedDataRequest, context) -> pb.StoreExtractedDataResponse:
        """
        Store or merge extracted business data
        """
        try:
            session_id = request.session_id
            logger.info(f"StoreExtractedData: session_id={session_id}")
            
            # Parse new data
            try:
                new_data = json.loads(request.data_json)
            except json.JSONDecodeError as e:
                return pb.StoreExtractedDataResponse(
                    status="error",
                    message=f"Invalid JSON: {str(e)}",
                    updated_data_json="{}"
                )
            
            # Get current session
            session = await self.redis.get_session(session_id)
            
            if not session:
                # Create new session
                session = {
                    "session_id": session_id,
                    "state": "collecting_info",
                    "extracted_data": new_data,
                    "conversation_history": []
                }
            else:
                # Merge or replace
                if request.merge:
                    session["extracted_data"].update(new_data)
                else:
                    session["extracted_data"] = new_data
            
            # Store in Redis
            success = await self.redis.set_session(session_id, session)
            
            if success:
                return pb.StoreExtractedDataResponse(
                    status="success",
                    message="Data stored successfully",
                    updated_data_json=json.dumps(session["extracted_data"])
                )
            else:
                return pb.StoreExtractedDataResponse(
                    status="error",
                    message="Failed to store data",
                    updated_data_json="{}"
                )
            
        except Exception as e:
            logger.error(f"StoreExtractedData error: {e}")
            await context.abort(
                grpc.StatusCode.INTERNAL,
                f"Failed to store data: {str(e)}"
            )
    
    async def GetNextQuestion(self, request: pb.GetNextQuestionRequest, context) -> pb.GetNextQuestionResponse:
        """
        Generate intelligent next question using LLM with conversation context
        
        Uses ragllm_service to generate contextual, natural questions
        based on current state, extracted data, and conversation history
        """
        try:
            session_id = request.session_id
            logger.info(f"GetNextQuestion: session_id={session_id}")
            
            # Get session
            session = await self.redis.get_session(session_id)
            
            # NEW USER - Simple welcome fallback
            if not session:
                logger.info(f"New session {session_id}, returning welcome question")
                return pb.GetNextQuestionResponse(
                    status="success",
                    next_question="Halo! 👋 Aku Milky. Ceritain dong, bisnis kamu tentang apa?",
                    missing_fields=["business_type", "business_name"],
                    suggestion="New user onboarding"
                )
            
            current_state = session.get("state", "initial")
            extracted_data = session.get("extracted_data", {})
            conversation_history = session.get("conversation_history", [])
            
            # Determine missing fields
            required = REQUIRED_FIELDS.get(current_state, [])
            missing = [field for field in required if not extracted_data.get(field)]
            
            # Build context for LLM
            llm_context = self._build_llm_context(
                state=current_state,
                extracted=extracted_data,
                missing=missing,
                history=conversation_history[-3:]  # Last 3 turns only
            )
            
            # Call LLM for intelligent question generation
            try:
                llm_request = GenerateAnswerRequest(
                    tenant_id="system_milky",  # Use Milky's system context
                    message=llm_context,
                    session_id=session_id
                )
                
                llm_response = await self.llm_stub.GenerateAnswer(llm_request)
                
                # Extract question from LLM response
                question = llm_response.answer.strip()
                
                logger.info(f"LLM generated question for state={current_state}: {question[:50]}...")
                
                return pb.GetNextQuestionResponse(
                    status="success",
                    next_question=question,
                    missing_fields=missing,
                    suggestion=f"LLM-generated | State: {current_state}"
                )
                
            except Exception as llm_error:
                # FALLBACK: If LLM fails, use safe default
                logger.warning(f"LLM call failed: {llm_error}, using fallback")
                
                # Smart fallback based on missing fields
                if "business_type" in missing:
                    fallback_q = "Ceritain dong tentang bisnis kamu? Jualan apa?"
                elif "business_name" in missing:
                    fallback_q = f"Oke! Nama bisnis {extracted_data.get('business_type', '')} kamu apa?"
                elif "products_services" in missing:
                    fallback_q = "Produk atau layanan apa aja yang kamu tawarkan?"
                else:
                    fallback_q = "Ada info lain yang mau kamu tambahin?"
                
                return pb.GetNextQuestionResponse(
                    status="success",
                    next_question=fallback_q,
                    missing_fields=missing,
                    suggestion=f"Fallback question | State: {current_state}"
                )
            
        except Exception as e:
            logger.error(f"GetNextQuestion error: {e}", exc_info=True)
            
            # CRITICAL FALLBACK: Always return something
            return pb.GetNextQuestionResponse(
                status="success",
                next_question="Ceritain dong lebih detail tentang bisnis kamu?",
                missing_fields=[],
                suggestion="Error fallback"
            )
    
    def _build_llm_context(
        self, 
        state: str, 
        extracted: dict, 
        missing: list, 
        history: list
    ) -> str:
        """
        Build rich context prompt for LLM to generate next question
        
        Context includes:
        - Current conversation state
        - Business data extracted so far
        - Missing required fields
        - Recent conversation turns (for reference)
        """
        
        # Format conversation history
        history_str = ""
        if history:
            history_str = "\n".join([
                f"  {turn.get('role', 'user')}: {turn.get('message', '')[:100]}"
                for turn in history
            ])
        
        # Format extracted data
        extracted_str = ""
        if extracted:
            extracted_str = "\n".join([
                f"  - {key}: {value}"
                for key, value in extracted.items()
                if value
            ])
        
        # Build context prompt
        context = f"""You are Milky, a friendly AI assistant helping business owners create chatbots.

CURRENT SITUATION:
- Conversation State: {state}
- Progress: {"Just started" if state in ["initial", "welcome"] else "Collecting business info"}

BUSINESS DATA COLLECTED:
{extracted_str if extracted_str else "  (none yet)"}

MISSING INFORMATION:
{', '.join(missing) if missing else 'All basic info collected'}

RECENT CONVERSATION:
{history_str if history_str else "  (first interaction)"}

YOUR TASK:
Generate the next question to ask the business owner in casual Indonesian (bahasa gaul).

GUIDELINES:
1. If state is "welcome" or no data yet: Ask about their business type naturally
2. If missing business_type: "Bisnis kamu tentang apa? Cafe, toko, jasa konsultasi, atau apa?"
3. If missing business_name: Reference their business type, then ask name
4. If missing products_services: Ask what they sell/offer
5. If missing pricing/hours/location: Ask naturally based on business type
6. Reference previous conversation naturally (don't repeat questions)
7. Keep it ONE short question (1-2 sentences max)
8. Use casual tone: kamu, dong, nih, sih, gue (when appropriate)
9. Be enthusiastic but not pushy

TONE EXAMPLES:
✅ "Wah seru! Jadi kamu jualan skincare ya. Produknya apa aja nih?"
✅ "Oke paham! Range harganya berapa kak?"
✅ "Mantap! Biasanya buka jam berapa?"
❌ "Please provide your business operating hours."
❌ "What is your business type? Select from the following options:"

OUTPUT FORMAT:
Return ONLY the question text, nothing else. No explanations, no metadata.
"""
        
        return context
    
    async def ClearSession(self, request: pb.ClearSessionRequest, context) -> pb.ClearSessionResponse:
        """
        Clear session from Redis (for testing or explicit cleanup)
        """
        try:
            session_id = request.session_id
            logger.info(f"ClearSession: session_id={session_id}")
            
            success = await self.redis.delete_session(session_id)
            
            if success:
                return pb.ClearSessionResponse(
                    status="success",
                    message=f"Session {session_id} cleared"
                )
            else:
                return pb.ClearSessionResponse(
                    status="error",
                    message="Failed to clear session"
                )
            
        except Exception as e:
            logger.error(f"ClearSession error: {e}")
            await context.abort(
                grpc.StatusCode.INTERNAL,
                f"Failed to clear session: {str(e)}"
            )
    
    async def HealthCheck(self, request: empty_pb2.Empty, context) -> empty_pb2.Empty:
        """Health check endpoint"""
        return empty_pb2.Empty()


# ============================================
# SERVER STARTUP
# ============================================

async def serve() -> None:
    """Start gRPC server with Redis connection"""
    
    # Initialize Redis client
    logger.info("🔌 Connecting to Redis...")
    redis_client = ConversationRedisClient(
        redis_url=settings.REDIS_URL,
        password=settings.REDIS_PASSWORD
    )
    
    try:
        await redis_client.connect()
    except Exception as e:
        logger.error(f"❌ Failed to connect to Redis: {e}")
        raise
    
    # Create gRPC server
    server = aio.server()
    
    # Add servicer
    servicer = ConversationManagerServicer(redis_client)
    pb_grpc.add_ConversationManagerServicer_to_server(servicer, server)
    
    # Add health check
    health_servicer = health.HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)
    health_servicer.set("", health_pb2.HealthCheckResponse.SERVING)
    
    # Listen on port
    listen_addr = f"0.0.0.0:{settings.GRPC_PORT}"
    server.add_insecure_port(listen_addr)
    
    # Start server
    await server.start()
    logger.info(f"🚀 ConversationManager gRPC server listening on port {settings.GRPC_PORT}")
    logger.info(f"📊 State machine: {len(VALID_STATES)} states, Redis-backed")
    
    # Graceful shutdown handler
    def handle_shutdown(*_):
        logger.info("🛑 Shutdown signal received. Cleaning up...")
        asyncio.create_task(shutdown(server, redis_client))
    
    signal.signal(signal.SIGTERM, handle_shutdown)
    signal.signal(signal.SIGINT, handle_shutdown)
    
    # Wait for termination
    await server.wait_for_termination()


async def shutdown(server, redis_client):
    """Graceful shutdown"""
    logger.info("Stopping server...")
    await server.stop(grace=5)
    
    logger.info("Disconnecting Redis...")
    await redis_client.disconnect()
    
    logger.info("✅ Shutdown complete")


if __name__ == "__main__":
    asyncio.run(serve())