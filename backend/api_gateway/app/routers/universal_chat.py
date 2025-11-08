from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel
from typing import Optional, Dict, List
import logging
import uuid

from backend.api_gateway.app.services.setup_orchestrator_client import SetupOrchestratorClient
from backend.api_gateway.app.services.orchestrator_client import OrchestratorClient

logger = logging.getLogger(__name__)
router = APIRouter()

setup_client = SetupOrchestratorClient()
customer_client = OrchestratorClient()

class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None

class SetupChatResponse(BaseModel):
    status: str
    mode: str = "setup"
    tenant_id: str
    milky_response: str
    current_state: str
    session_id: str
    extracted_data: Optional[Dict] = {}
    suggested_faqs: Optional[List] = []
    next_action: Optional[str] = ""

class CustomerChatResponse(BaseModel):
    status: str
    mode: str = "customer"
    tenant_id: str
    business_name: str
    response: str
    session_id: str
    trace_id: str
    intent: str

@router.post("/chat", response_model=SetupChatResponse)
async def setup_mode_chat(request: ChatRequest, authorization: Optional[str] = Header(None)):
    if not authorization:
        raise HTTPException(401, "Authentication required")
    
    try:
        token = authorization.replace("Bearer ", "")
        user_id = "test_owner"
        tenant_id = "test_tenant"
        session_id = request.session_id or str(uuid.uuid4())
        
        logger.info(f"[Setup] tenant={tenant_id} | session={session_id}")
        
        result = await setup_client.process_setup_chat(
            user_id=user_id,
            tenant_id=tenant_id,
            message=request.message,
            session_id=session_id
        )
        
        if result.get("status") == "error":
            raise HTTPException(500, detail=result.get("error"))
        
        return SetupChatResponse(
            status="success",
            tenant_id=tenant_id,
            milky_response=result.get("milky_response", ""),
            current_state=result.get("current_state", ""),
            session_id=session_id,
            extracted_data=result.get("extracted_data", {}),
            suggested_faqs=result.get("suggested_faqs", []),
            next_action=result.get("next_action", "")
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Setup] Error: {e}")
        raise HTTPException(500, str(e))

@router.post("/{tenant_id}/chat", response_model=CustomerChatResponse)
async def customer_mode_chat(tenant_id: str, request: ChatRequest):
    try:
        session_id = request.session_id or str(uuid.uuid4())
        logger.info(f"[Customer] tenant={tenant_id} | session={session_id}")
        
        result = await customer_client.process_customer_query(
            tenant_id=tenant_id,
            message=request.message,
            session_id=session_id
        )
        
        if result.get("status") == "error":
            raise HTTPException(500, detail=result.get("error"))
        
        return CustomerChatResponse(
            status="success",
            tenant_id=tenant_id,
            business_name=result.get("business_name", tenant_id),
            response=result.get("response", ""),
            session_id=session_id,
            trace_id=result.get("trace_id", ""),
            intent=result.get("intent", "")
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Customer] Error: {e}")
        raise HTTPException(500, str(e))
