"""
Public Chat Router - Customer Mode
URL: POST /{tenant_id}/chat
Auth: None (public endpoint)
Purpose: Customer queries to business chatbots
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
import logging
import uuid

from backend.api_gateway.app.services.orchestrator_client import OrchestratorClient

logger = logging.getLogger(__name__)
router = APIRouter()

customer_client = OrchestratorClient()

class PublicChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None

class PublicChatResponse(BaseModel):
    status: str
    mode: str = "customer"
    tenant_id: str
    business_name: str
    response: str
    session_id: str
    trace_id: str
    intent: str
    confidence: Optional[float] = 0.0

@router.post("/{tenant_id}/chat", response_model=PublicChatResponse)
async def customer_mode_chat(tenant_id: str, request: PublicChatRequest):
    """
    Customer Mode Chat Endpoint
    
    Flow:
    1. No authentication required (public endpoint)
    2. Call customer_orchestrator via gRPC
    3. Return conversational response from business chatbot
    
    Example Request:
        POST /bca/chat
        Body: {
            "message": "Apa saja jenis tabungan di BCA?",
            "session_id": "optional-session-id"
        }
    
    Example Response:
        {
            "status": "success",
            "mode": "customer",
            "tenant_id": "bca",
            "business_name": "Bank Central Asia",
            "response": "BCA punya 7 jenis tabungan utama...",
            "session_id": "uuid-here",
            "trace_id": "trace-uuid",
            "intent": "customer_inquiry",
            "confidence": 0.92
        }
    
    Examples of tenant_id:
        - /bca/chat
        - /rinacakes/chat
        - /konsultanpsikologi/chat
    """
    try:
        # Generate session ID if not provided
        session_id = request.session_id or str(uuid.uuid4())
        
        logger.info(
            f"[Customer] Processing customer query | "
            f"tenant={tenant_id} | session={session_id}"
        )
        
        # Call customer orchestrator gRPC service
        result = await customer_client.process_customer_query(
            tenant_id=tenant_id,
            message=request.message,
            session_id=session_id
        )
        
        # Handle error response
        if result.get("status") == "error":
            logger.error(f"[Customer] Orchestrator error: {result.get('error')}")
            raise HTTPException(
                status_code=500,
                detail=result.get("error", "Unknown error from customer orchestrator")
            )
        
        logger.info(
            f"[Customer] Success | intent={result.get('intent')} | "
            f"confidence={result.get('confidence', 0.0):.2f}"
        )
        
        return PublicChatResponse(
            status="success",
            tenant_id=tenant_id,
            business_name=result.get("business_name", tenant_id),
            response=result.get("response", ""),
            session_id=session_id,
            trace_id=result.get("trace_id", ""),
            intent=result.get("intent", "unknown"),
            confidence=result.get("confidence", 0.0)
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Customer] Unexpected error: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error: {str(e)}"
        )
