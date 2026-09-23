import json
import os
import uuid
from datetime import datetime
from typing import Dict, List, Optional

import requests
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
import grpc

from backend.api_gateway.app.services.ragllm_client import RagLLMClient
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

# 🏥 HEALTH CHECK
@router.get("/health")
async def health_check():
    """Health check for Customer Mode"""
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "service": "customer_mode"
    }