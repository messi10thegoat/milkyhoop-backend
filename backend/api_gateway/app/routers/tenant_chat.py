"""
Tenant Chat Router - Business Query Mode
Handles: Financial reports, analytics, customer data queries
"""

import grpc
import logging
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from backend.api_gateway.libs.milkyhoop_protos import tenant_orchestrator_pb2, tenant_orchestrator_pb2_grpc

logger = logging.getLogger(__name__)

router = APIRouter()


class TenantChatRequest(BaseModel):
    message: str
    session_id: str = ""
    conversation_context: str = ""


class TenantChatResponse(BaseModel):
    status: str
    milky_response: str
    intent: str = ""
    trace_id: str = ""


@router.post("/{tenant_id}/chat", response_model=TenantChatResponse)
async def tenant_chat(tenant_id: str, request_body: TenantChatRequest, request: Request):
    """
    Tenant Mode Chat Endpoint
    
    Purpose: Business queries (financial, analytics, customer data)
    Authentication: JWT required
    """
    
    try:
        # Get user_id from JWT context (set by AuthMiddleware)
        user_id = getattr(request.state, "user_id", "")
        
        logger.info(f"Tenant chat request | tenant={tenant_id} | user={user_id} | message={request_body.message[:50]}")
        
        # Connect to tenant_orchestrator gRPC
        channel = grpc.aio.insecure_channel("tenant_orchestrator:5017")
        stub = tenant_orchestrator_pb2_grpc.TenantOrchestratorStub(channel)
        
        # Build gRPC request
        grpc_request = tenant_orchestrator_pb2.ProcessTenantQueryRequest(
            tenant_id=tenant_id,
            user_id=user_id,
            session_id=request_body.session_id,
            message=request_body.message,
            conversation_context=request_body.conversation_context
        )
        
        # Call tenant_orchestrator
        grpc_response = await stub.ProcessTenantQuery(grpc_request)
        
        # Close channel
        await channel.close()
        
        # Return response
        return TenantChatResponse(
            status=grpc_response.status,
            milky_response=grpc_response.milky_response,
            intent=grpc_response.intent,
            trace_id=grpc_response.trace_id
        )
        
    except grpc.RpcError as e:
        logger.error(f"gRPC error: {e.code()} - {e.details()}")
        raise HTTPException(status_code=500, detail=f"Service error: {e.details()}")
        
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")