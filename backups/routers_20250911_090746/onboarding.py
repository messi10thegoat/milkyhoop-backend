from fastapi import APIRouter, HTTPException, Request, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import List, Dict, Optional
import uuid
import os, json
import logging
import asyncio
import requests
from datetime import datetime

# Import service clients for 8-service integration
from backend.api_gateway.app.services.intent_client import IntentParserClient
from backend.api_gateway.app.services.ragllm_client import RagLLMClient
from backend.api_gateway.app.services.ragcrud_client import RagCrudClient
from backend.api_gateway.app.services.memory_client import MemoryClient
from backend.api_gateway.libs.milkyhoop_prisma import Prisma


router = APIRouter()
prisma = Prisma()
memory_client = MemoryClient()
logger = logging.getLogger(__name__)

# 📦 Schemas for API requests
class FAQItem(BaseModel):
    question: str
    answer: str

class FAQUploadRequest(BaseModel):
    user_id: str
    tenant_id: str
    faqs: List[FAQItem]

class ConversationalSetupRequest(BaseModel):
    user_id: str
    tenant_id: str
    message: str
    session_id: Optional[str] = None

class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None

class TenantProfileResponse(BaseModel):
    status: str
    tenant_profile: Dict
    milky_response: str
    next_action: str

# 🧠 In-memory conversation storage (for MVP - replace with Redis in production)
conversation_memory = {}

def generate_trace_id() -> str:
    """Generate unique trace ID for request tracking"""
    return str(uuid.uuid4())[:8]

def get_conversation_history(session_id: str) -> List[Dict]:
    """Get conversation history for session"""
    return conversation_memory.get(session_id, [])

def save_conversation_turn(session_id: str, user_message: str, bot_response: str, intent: str, entities: Dict, trace_id: str):
    """Save conversation turn to memory"""
    if session_id not in conversation_memory:
        conversation_memory[session_id] = []
    
    conversation_memory[session_id].append({
        "timestamp": datetime.now().isoformat(),
        "user_message": user_message,
        "bot_response": bot_response,
        "intent": intent,
        "entities": entities,
        "trace_id": trace_id
    })
    
    # Keep only last 10 turns to prevent memory bloat
    if len(conversation_memory[session_id]) > 10:
        conversation_memory[session_id] = conversation_memory[session_id][-10:]

async def call_chatbot_service(user_id: str, tenant_id: str, message: str, trace_id: str) -> Dict:
    """Call Chatbot Service via gRPC"""
    try:
        from backend.api_gateway.app.services.chatbot_client import ChatbotClient
        
        chatbot_client = ChatbotClient()
        result = await chatbot_client.send_message(
            user_id=user_id,
            session_id=trace_id,
            message=message,
            tenant_id=tenant_id
        )
        
        logger.info(f"[{trace_id}] ✅ Chatbot Service gRPC response received")
        return {"status": "success", "result": result}
        
    except Exception as e:
        logger.error(f"[{trace_id}] ❌ Chatbot Service gRPC call failed: {e}")
        return {"status": "error", "result": str(e)}

async def call_flow_executor(intent: str, entities: Dict, user_id: str, tenant_id: str, trace_id: str) -> Dict:
    """Call Flow Executor to run business logic flows"""
    try:
        # Determine flow based on intent
        flow_mapping = {
           "business_setup": "business-setup-handler.json",
            "faq_create": "faq-create-handler.json", 
            "faq_read": "faq-read-handler.json",
            "faq_update": "faq-update-handler.json",
            "faq_delete": "faq-delete-handler.json",
            "faq_query": "faq-query-handler.json",
            "product_inquiry": "customer-inquiry-handler.json"


        }
        
        flow_file = flow_mapping.get(intent, "default-handler.json")

        # STEP 4.5: Enhance context dengan memory untuk multi-turn conversation
        if intent == "faq_update":
            # Get last FAQ action dari memory
            last_context = await memory_client.get_memory(
                user_id=user_id,
                tenant_id=tenant_id,
                key="last_faq_action"
            )
            
            if last_context:
                # Merge memory context dengan current entities
                if "entities" in last_context:
                    # Add previous context ke current entities
                    entities["previous_action"] = last_context["entities"]
                    entities["last_intent"] = last_context["intent"]
                    entities["reference_context"] = last_context.get("flow_result", {})
                    
                logger.info(f"Enhanced entities dengan memory context untuk user {user_id}")
            else:
                logger.warning(f"No memory context found untuk faq_update user {user_id}")



        
        # Call Flow Executor (port 8088)
        response = requests.post(
            f"http://flow-executor:8088/run-flow/{flow_file}",
            json={
                "input": {
                    "user_id": user_id,
                    "tenant_id": tenant_id,
                    "intent": intent,
                    "entities": entities,
                    "trace_id": trace_id
                }
            },
            timeout=30
        )
        
        if response.status_code == 200:
            result = response.json()
            logger.info(f"[{trace_id}] ✅ Flow Executor completed: {flow_file}")
            return result
        else:
            logger.error(f"[{trace_id}] ❌ Flow Executor error: {response.status_code}")
            return {"status": "error", "message": "Flow execution failed"}
            
    except Exception as e:
        logger.error(f"[{trace_id}] ❌ Flow Executor call failed: {e}")
        return {"status": "error", "message": "Flow service unavailable"}

async def send_notification(user_id: str, notification_type: str, details: Dict, trace_id: str):
    """Send notification via Notification Service (Kafka)"""
    try:
        # Send to Flow Executor which will publish to Kafka
        notification_payload = {
            "user_id": user_id,
            "type": notification_type,
            "details": details,
            "timestamp": datetime.now().isoformat(),
            "trace_id": trace_id
        }
        
        response = requests.post(
            "http://flow-executor:8088/send-notification",
            json=notification_payload,
            timeout=5
        )
        
        if response.status_code == 200:
            logger.info(f"[{trace_id}] ✅ Notification sent: {notification_type}")
        else:
            logger.warning(f"[{trace_id}] ⚠️ Notification failed: {response.status_code}")
            
    except Exception as e:
        logger.error(f"[{trace_id}] ❌ Notification service error: {e}")

# 🎯 MAIN ENDPOINT: 8-Service Integration for Conversational Setup
@router.post("/conversational-setup", response_model=TenantProfileResponse)
async def conversational_setup(data: ConversationalSetupRequest):
    """
    8-Service Integration Pipeline for Conversational AI Builder
    
    Flow: API Gateway → Chatbot Service → Intent Parser → Flow Executor → RAG Services → Notification
    """
    # Generate trace ID for end-to-end tracking
    trace_id = generate_trace_id()
    session_id = data.session_id or str(uuid.uuid4())
    
    logger.info(f"[{trace_id}] 🚀 Starting 8-service integration pipeline")
    logger.info(f"[{trace_id}] 📥 Request: user={data.user_id}, tenant={data.tenant_id}, message='{data.message[:50]}...'")
    
    try:
        # STEP 1: Call Chatbot Service for session management
        logger.info(f"[{trace_id}] 🤖 Step 1: Calling Chatbot Service")
        chatbot_response = await call_chatbot_service(
            data.user_id, data.tenant_id, data.message, trace_id
        )
        
        # STEP 1.5: Context Resolution
        resolved_message = data.message
        resolved_references = []
        try:
                resolved_message = context_await result.get("resolved_message", data.message)
                resolved_references = context_await result.get("references", [])
        except Exception as e:
            logger.warning(f"Context resolution failed: {e}")
            resolved_message = data.message
            resolved_references = []





        # STEP 2: Call Intent Parser for AI classification
        logger.info(f"[{trace_id}] 🧠 Step 2: Calling Intent Parser")
        intent_client = IntentParserClient()
        parsed = await intent_client.parse(user_id=data.user_id, message=resolved_message)
        
        intent = parsed.get("intent", "unknown")
        entities_raw = parsed.get("entities", {})
        
        # STEP 2.5: Override intent if Context Service suggests better one
        suggested_intent = context_await result.get("suggested_intent", "") if 'context_result' in locals() else ""
        if suggested_intent and suggested_intent.strip():
            logger.info(f"[{trace_id}] 🔄 Overriding intent: {intent} → {suggested_intent}")
            intent = suggested_intent
        
        # Parse entities safely
        entities = entities_raw if isinstance(entities_raw, dict) else {}
        
        logger.info(f"[{trace_id}] 🎯 Final Intent: {intent}, Entities: {len(entities)} items")
        
        # STEP 3: Route to Flow Executor based on intent
        logger.info(f"[{trace_id}] ⚡ Step 3: Calling Flow Executor")
        flow_result = await call_flow_executor(
            intent, entities, data.user_id, data.tenant_id, trace_id
        )
        
        # STEP 4: Generate natural response using RAG LLM
        logger.info(f"[{trace_id}] 💬 Step 4: Generating natural response")
        rag_client = RagLLMClient()
        
        # Build context for natural response
        response_context = {
            "intent": intent,
            "entities": entities,
            "flow_result": flow_result,
            "conversation_history": get_conversation_history(session_id)
        }
        
        # Generate contextual response
        natural_response = await rag_client.generate_answer(
            user_id=data.user_id,
            session_id=session_id,
            tenant_id=data.tenant_id,
            message=f"Generate natural response for {intent} with context: {json.dumps(response_context)}"
        )
        
        # STEP 5: Create/Update business profile
        logger.info(f"[{trace_id}] 📊 Step 5: Building business profile")
        business_profile = {
            "session_id": session_id,
            "intent_history": [intent],
            "last_interaction": datetime.now().isoformat(),
            "conversation_turns": len(get_conversation_history(session_id)) + 1,
            "trace_id": trace_id
        }
        
        # Extract business info from entities
        if entities:
            if "Business" in entities:
                business_profile.update({
                    "business_type": entities["Business"].get("type", "Unknown"),
                    "category": entities["Business"].get("category", "General")
                })
            if "Customer" in entities:
                business_profile.update({
                    "customer_type": entities["Customer"].get("customer_type", "Unknown")
                })
        
        # Determine next action based on intent and flow result
        next_action_mapping = {
            "business_setup": "suggest_document_upload",
            "faq_create": "suggest_document_upload", 
            "faq_read": "show_existing_faq",
            "product_inquiry": "provide_recommendation",
            "unknown": "collect_more_info"
        }
        next_action = next_action_mapping.get(intent, "collect_more_info")
        
        # STEP 6: Save conversation turn
        save_conversation_turn(
            session_id, data.message, natural_response, intent, entities, trace_id
        )
        
        # STEP 7: Send notification for business events
        if intent in ["business_setup", "faq_create"]:
            await send_notification(
                data.user_id, 
                f"{intent}_completed",
                {"tenant_id": data.tenant_id, "intent": intent},
                trace_id
            )
        

        # STEP 7.5: Store conversation context in memory
        if intent == "faq_create":
            # Store last FAQ action untuk multi-turn conversation
            context_data = {
                "intent": intent,
                "entities": entities,
                "flow_result": flow_result,
                "timestamp": datetime.now().isoformat(),
                "trace_id": trace_id
            }
            
            await memory_client.store_memory(
                user_id=data.user_id,
                tenant_id=data.tenant_id, 
                key="last_faq_action",
                value=context_data,
                ttl=3600  # 1 hour
            )
            
            logger.info(f"Context stored in memory for user {data.user_id}")






        # STEP 8: Save business profile
        os.makedirs("data/tenant_profiles", exist_ok=True)
        profile_path = f"data/tenant_profiles/{data.tenant_id}.json"
        
        # Load and merge existing profile
        existing_profile = {}
        if os.path.exists(profile_path):
            try:
                with open(profile_path, "r") as f:
                    existing_profile = json.load(f)
            except:
                pass
        
        merged_profile = {**existing_profile, **business_profile}
        
        with open(profile_path, "w") as f:
            json.dump(merged_profile, f, indent=2, ensure_ascii=False)
        
        logger.info(f"[{trace_id}] ✅ 8-service pipeline completed successfully")
        
        return TenantProfileResponse(
            status="success",
            tenant_profile=merged_profile,
            milky_response=natural_response,
            next_action=next_action
        )
        
    except Exception as e:
        logger.error(f"[{trace_id}] ❌ Pipeline error: {str(e)}")
        
        # Graceful fallback
        fallback_profile = {
            "session_id": session_id,
            "error": str(e),
            "last_interaction": datetime.now().isoformat(),
            "trace_id": trace_id
        }
        
        return TenantProfileResponse(
            status="error",
            tenant_profile=fallback_profile,
            milky_response="Hai! Aku Milky 😊 Aku bisa bantu kamu bikin asisten AI untuk bisnis kamu! Cerita dong apa tantangannya?",
            next_action="collect_more_info"
        )

# 🤖 CUSTOMER CHAT ENDPOINT: Full Pipeline for Customer Queries
@router.post("/chat/{tenant_id}")
async def chat_with_assistant(tenant_id: str, data: ChatRequest):
    """
    Customer chat with deployed AI assistant - Full 8-service pipeline
    
    Flow: Customer Query → Intent Parser → RAG Search → RAG LLM → Natural Response
    """
    trace_id = generate_trace_id()
    session_id = data.session_id or str(uuid.uuid4())


    # STEP 0: Context Resolution (NEW)
    resolved_message = data.message
    resolved_references = []
    logger.info(f"[{trace_id}] 🧠 Step 0: Resolving context references")

    try:
            
            resolved_message = context_await result.get("resolved_message", data.message)
            resolved_references = context_await result.get("references", [])
            
            if resolved_references:
                logger.info(f"[{trace_id}] 🔗 References resolved: {len(resolved_references)} items")
                logger.info(f"[{trace_id}] 📝 Original: '{data.message}' → Resolved: '{resolved_message}'")
    except Exception as e:
        logger.warning(f"[{trace_id}] ⚠️ Context resolution failed: {e}, using original message")
        resolved_message = data.message
        resolved_references = []

    # STEP 1: Call Chatbot Service for session management
    logger.info(f"[{trace_id}] 💬 Customer chat: tenant={tenant_id}, message='{data.message[:50]}...'")
    
    try:
        # STEP 1: Intent classification for customer query
        intent_client = IntentParserClient()
        parsed = await intent_client.parse(
            user_id=f"customer_{session_id}", 
            message=data.message
        )
        
        intent = parsed.get("intent", "unknown")
        entities = parsed.get("entities", {})
        
        logger.info(f"[{trace_id}] 🎯 Customer intent: {intent}")
        
        # STEP 2: Execute customer service flow
        flow_result = await call_flow_executor(
            intent, entities, f"customer_{session_id}", tenant_id, trace_id
        )
        
        # STEP 3: Generate natural customer response
        rag_client = RagLLMClient()
        customer_response = await rag_client.generate_answer(
            user_id=f"customer_{session_id}",
            session_id=session_id,
            tenant_id=tenant_id,
            message=data.message
        )
        
        # STEP 4: Load business profile for context
        profile_path = f"data/tenant_profiles/{tenant_id}.json"
        business_name = "Business"
        
        if os.path.exists(profile_path):
            try:
                with open(profile_path, "r") as f:
                    profile = json.load(f)
                    business_name = profile.get("business_name", "Business")
            except:
                pass
        
        logger.info(f"[{trace_id}] ✅ Customer response generated")
        
        return {
            "status": "success",
            "tenant_id": tenant_id,
            "business_name": business_name,
            "response": customer_response,
            "session_id": session_id,
            "trace_id": trace_id
        }
        
    except Exception as e:
        logger.error(f"[{trace_id}] ❌ Customer chat error: {e}")
        return {
            "status": "error",
            "message": "Maaf, asisten AI sedang maintenance 😅",
            "trace_id": trace_id
        }

# 📤 FAQ UPLOAD ENDPOINT: Direct RAG Integration
@router.post("/faq/{tenant_id}")
async def upload_faq(tenant_id: str, data: FAQUploadRequest, request: Request):
    """Upload FAQ directly to RAG system with tenant isolation"""
    trace_id = generate_trace_id()
    client_ip = request.client.host
    
    logger.info(f"[{trace_id}] 📥 FAQ upload: tenant={tenant_id}, count={len(data.faqs)}, ip={client_ip}")
    
    try:
        # Direct call to RAG CRUD Service
        rag_client = RagCrudClient()
        uploaded_count = 0
        
        for faq in data.faqs:
            doc = await rag_client.create_document(
                tenant_id=tenant_id,
                title=f"FAQ: {faq.question[:50]}...",
                content=f"Q: {faq.question}\nA: {faq.answer}",
                source="FAQ_Upload",
                tags=["faq"]
            )
            logger.info(f"[{trace_id}] ✅ FAQ uploaded: {doc.id}")
            uploaded_count += 1
        
        # Send notification
        await send_notification(
            data.user_id,
            "faq_upload_completed", 
            {"tenant_id": tenant_id, "count": uploaded_count},
            trace_id
        )
        
        return {
            "status": "success",
            "message": "FAQ uploaded successfully",
            "count": uploaded_count,
            "trace_id": trace_id
        }
        
    except Exception as e:
        logger.error(f"[{trace_id}] ❌ FAQ upload failed: {e}")
        return {
            "status": "error",
            "message": "Failed to upload FAQ",
            "trace_id": trace_id
        }

# 📋 GET FAQ ENDPOINT: RAG Retrieval
@router.get("/faq")
async def get_faq(tenant_id: str):
   """Get FAQ list from RAG system with semantic support"""
   trace_id = generate_trace_id()
   
   try:
       rag_client = RagCrudClient()
       docs = await rag_client.list_documents(tenant_id=tenant_id)
       
       # Filter FAQ documents - FIX: Handle gRPC response properly
       if hasattr(docs, 'documents'):
           faq_docs = docs.documents
       elif isinstance(docs, list):
           faq_docs = docs
       else:
           faq_docs = [docs] if docs else []
       
       faqs = []
       for doc in faq_docs:
           content = doc.content
           
           # Smart semantic format detection
           if "Q:" in content and "A:" in content:
               # Legacy Q: A: format
               parts = content.split("A:", 1)
               if len(parts) == 2:
                   question = parts[0].replace("Q:", "").strip()
                   answer = parts[1].strip()
                   faqs.append({
                       "id": doc.id,
                       "question": question,
                       "answer": answer
                   })
           else:
               # Natural conversational format
               if doc.title and doc.content:
                   question = doc.title
                   answer = doc.content
                   faqs.append({
                       "id": doc.id,
                       "question": question,
                       "answer": answer
                   })
       
       return {
           "tenant_id": tenant_id,
           "faqs": faqs,
           "count": len(faqs),
           "trace_id": trace_id
       }
       
   except Exception as e:
       logger.error(f"[{trace_id}] ❌ FAQ retrieval failed: {e}")
       return {
           "tenant_id": tenant_id,
           "faqs": [],
           "error": str(e),
           "trace_id": trace_id
       }

# 🔍 DEBUG ENDPOINTS
@router.get("/conversation/{session_id}")
async def get_conversation_history_endpoint(session_id: str):
    """Get conversation history for debugging"""
    history = get_conversation_history(session_id)
    return {
        "session_id": session_id,
        "conversation_history": history,
        "turn_count": len(history)
    }

@router.delete("/conversation/{session_id}")
async def clear_conversation_history(session_id: str):
    """Clear conversation history for session"""
    if session_id in conversation_memory:
        del conversation_memory[session_id]
    return {"message": f"Conversation history cleared for session {session_id}"}

@router.get("/profile/{tenant_id}")
async def get_tenant_profile(tenant_id: str):
    """Get saved tenant profile"""
    profile_path = f"data/tenant_profiles/{tenant_id}.json"
    
    if not os.path.exists(profile_path):
        raise HTTPException(status_code=404, detail="Tenant profile not found")
    
    try:
        with open(profile_path, "r") as f:
            profile = json.load(f)
        return {"tenant_id": tenant_id, "profile": profile}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load profile: {str(e)}")

# 🏥 HEALTH CHECK
@router.get("/health")
async def health_check():
    """Health check for API Gateway"""
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "service": "api_gateway"
    }