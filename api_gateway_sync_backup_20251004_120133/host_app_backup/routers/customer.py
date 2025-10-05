import json
import os
import uuid
from datetime import datetime
from typing import Dict, List, Optional

import requests
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
import grpc

from backend.api_gateway.app.services.chatbot_client import ChatbotClient
from backend.api_gateway.app.services.ragcrud_client import RagCrudClient
from backend.api_gateway.app.services.ragllm_client import RagLLMClient
from backend.api_gateway.app.services.tenant_client import TenantParserClient
from backend.api_gateway.app.services.context_client import ContextClient

import logging

logger = logging.getLogger(__name__)
router = APIRouter()

# 📊 REQUEST/RESPONSE MODELS
class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    visitor_id: Optional[str] = None

class ChatResponse(BaseModel):
    status: str
    tenant_id: str
    business_name: Optional[str] = None
    response: str
    session_id: str
    trace_id: str
    intent: Optional[str] = None

# 🔧 UTILITY FUNCTIONS
def generate_trace_id() -> str:
    """Generate unique trace ID"""
    return str(uuid.uuid4())[:8]

# 🎯 CUSTOMER MODE - SIMPLIFIED FLOW EXECUTOR PATTERN
async def call_flow_executor(profile_id: str, query: str, trace_id: str, entities: Dict, intent: str, session_id: str) -> Dict:    
    """
    Call Flow Executor for customer inquiries - SIMPLIFIED VERSION
    
    Clean implementation without complex confidence/routing logic
    """
    try:
        logger.info(f"[FLOW-{trace_id}] Executing customer flow for {profile_id}")
        
        # Simple payload structure
        payload = {
            "input": {
                "user_id": f"customer_{session_id}",
                "tenant_id": profile_id,
                "query": query,
                "entities": entities,
                "intent": intent,
                "trace_id": trace_id,
                "timestamp": datetime.now().isoformat(),
                "customer_query": query
            }
        }
        
        # Primary flow execution
        response = requests.post(
            f"http://flow-executor:8088/run-flow/customer-inquiry-handler.json",
            json=payload,
            timeout=30,
            headers={"Content-Type": "application/json"}
        )
        
        if response.status_code == 200:
            result = response.json()
            logger.info(f"[FLOW-{trace_id}] ✅ Flow Executor completed successfully")
            
            if not isinstance(result, dict):
                logger.warning(f"[FLOW-{trace_id}] Invalid response format from flow executor")
                return await fallback_direct_processing(profile_id, query, trace_id)
            
            return result
            
        elif response.status_code == 404:
            logger.error(f"[FLOW-{trace_id}] Flow file not found - falling back to direct processing")
            return await fallback_direct_processing(profile_id, query, trace_id)
            
        else:
            logger.error(f"[FLOW-{trace_id}] Flow Executor error: {response.status_code} - {response.text}")
            return await fallback_direct_processing(profile_id, query, trace_id)
            
    except requests.exceptions.Timeout:
        logger.error(f"[FLOW-{trace_id}] Flow Executor timeout - attempting fallback")
        return await fallback_direct_processing(profile_id, query, trace_id)
        
    except requests.exceptions.ConnectionError:
        logger.error(f"[FLOW-{trace_id}] Flow Executor connection failed - service unavailable")
        return await fallback_direct_processing(profile_id, query, trace_id)
        
    except Exception as e:
        logger.error(f"[FLOW-{trace_id}] Unexpected error in flow execution: {str(e)}")
        return await fallback_direct_processing(profile_id, query, trace_id)

async def fallback_direct_processing(profile_id: str, query: str, trace_id: str) -> Dict:
    """
    Direct RAG Processing Fallback - SIMPLIFIED VERSION
    
    Clean fallback without confidence/routing complexity
    """
    try:
        logger.info(f"[FALLBACK-{trace_id}] Initiating direct RAG processing for {profile_id}")
        
        # Call RAG LLM service directly
        rag_client = RagLLMClient()
        answer = await rag_client.generate_answer(
            user_id=f"customer_{trace_id}",
            session_id=trace_id,
            tenant_id=profile_id,
            message=query
        )
        
        logger.info(f"[FALLBACK-{trace_id}] ✅ Direct RAG processing successful")
        return {
            "result": {
                "message": answer
            },
            "status": "success",
            "source": "direct_rag"
        }
            
    except Exception as e:
        logger.error(f"[FALLBACK-{trace_id}] Fallback processing failed: {str(e)}")
        return {
            "result": {
                "message": "Maaf, tidak dapat memproses permintaan saat ini."
            },
            "status": "error", 
            "source": "fallback_error"
        }

# 🤖 CUSTOMER CHAT ENDPOINT - CLEAN VERSION WITH ORCHESTRATOR
@router.post("/{tenant_id}/chat")
async def customer_chat(tenant_id: str, data: ChatRequest):
    """
    Customer chat with AI assistant - ORCHESTRATOR INTEGRATION
    
    Flow: Customer Query → Customer Orchestrator → Response
    Simplified routing to orchestrator service
    """
    trace_id = generate_trace_id()
    session_id = data.session_id or str(uuid.uuid4())
    
    logger.info(f"[{trace_id}] Customer chat: tenant={tenant_id}, message='{data.message[:50]}...'")
    
    try:
        # STEP 1: Context Resolution
        resolved_message = data.message
        
        # STEP 2: Call Customer Orchestrator
        logger.info(f"[{trace_id}] Step 2: Calling Customer Orchestrator")
        
        try:
            from backend.api_gateway.app.services.orchestrator_client import OrchestratorClient
            
            orchestrator = OrchestratorClient()
            logger.info("DEBUG_ORCHESTRATOR_CALL: Creating orchestrator client")
            logger.info("DEBUG_ORCHESTRATOR_CALL: Calling orchestrator method")
            result = await orchestrator.process_customer_query(
                message=resolved_message,
                tenant_id=tenant_id,
                session_id=session_id
            )
            if result and len(str(result).strip()) > 0:
                if isinstance(result, dict):
                    response_text = result.get('response', str(result)).strip()
                    confidence = result.get('confidence')
                    tier = result.get('tier')
                else:
                    response_text = str(result).strip()
                    confidence = None
                    tier = None
                intent = "customer_inquiry"
                logger.info(f"[{trace_id}] Orchestrator success: {response_text[:50]}")
            else:
                logger.error(f"[{trace_id}] Empty orchestrator response: '{result}'")
                raise Exception("Empty orchestrator response")

        except grpc.RpcError as grpc_error:
            logger.error(f"[{trace_id}] gRPC error: {grpc_error}")
        except Exception as orch_error:
            logger.error(f"[{trace_id}] Orchestrator error: {str(orch_error)}")
            
            # Fallback to tenant parser + flow executor pattern
            logger.info(f"[{trace_id}] Falling back to original flow")
            
            tenant_client = TenantParserClient()
            parsed = await tenant_client.parse_customer_query(
                tenant_id=tenant_id, 
                message=resolved_message
            )
            
            intent = parsed.get("intent", "customer_inquiry")
            entities_raw = parsed.get("entities", {})
            entities = entities_raw if isinstance(entities_raw, dict) else {}
            
            logger.info(f"[{trace_id}] Customer Intent: {intent}, Entities: {len(entities)} items")
            
            # STEP 3: Route to Flow Executor
            logger.info(f"[{trace_id}] Step 3: Calling Flow Executor")
            flow_result = await call_flow_executor(
                profile_id=tenant_id,
                query=data.message,
                trace_id=trace_id,
                entities=entities,
                intent=intent,
                session_id=session_id
            )
            
            # STEP 4: Extract response from flow result
            logger.info(f"[{trace_id}] Step 4: Processing flow response")
            
            if flow_result.get("status") == "error":
                error_message = flow_result.get("message", "Flow execution failed")
                logger.error(f"[{trace_id}] Flow error: {error_message}")
                
                return ChatResponse(
                    status="error",
                    tenant_id=tenant_id,
                    response="Maaf, sistem sedang mengalami gangguan. Silakan coba lagi dalam beberapa menit.",
                    session_id=session_id,
                    trace_id=trace_id,
                    intent="system_error"
                )
            
            response_text = flow_result.get("result", {}).get("answer", "") or flow_result.get("result", {}).get("message", "")
            if not response_text:
                logger.warning(f"[{trace_id}] Empty response from flow executor")
                response_text = f"Halo! Terima kasih telah menghubungi {tenant_id}. Ada yang bisa saya bantu?"
        
        # STEP 5: Load business profile for context
        profile_path = f"data/tenant_profiles/{tenant_id}.json"
        business_name = tenant_id
        
        if os.path.exists(profile_path):
            try:
                with open(profile_path, "r") as f:
                    profile = json.load(f)
                    business_name = profile.get("business_name", tenant_id)
            except:
                pass
        
        logger.info(f"[{trace_id}] Customer response generated successfully")
        
        return ChatResponse(
            status="success",
            tenant_id=tenant_id,
            business_name=business_name,
            response=response_text,
            session_id=session_id,
            trace_id=trace_id,
            intent=intent
        )
        
    except Exception as e:
        logger.error(f"[{trace_id}] Customer chat error: {e}")
        return ChatResponse(
            status="error",
            tenant_id=tenant_id,
            response="Maaf, asisten AI sedang maintenance. Silakan coba lagi dalam beberapa menit.",
            session_id=session_id,
            trace_id=trace_id
        )

# 🏥 HEALTH CHECK
@router.get("/health")
async def health_check():
    """Health check for Customer Mode"""
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "service": "customer_mode"
    }