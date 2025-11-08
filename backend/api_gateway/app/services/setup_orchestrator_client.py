import grpc
import logging
from typing import Dict, Optional

# Import from libs (same pattern as other clients)
from milkyhoop_protos.setup_orchestrator_pb2 import (
    ProcessSetupChatRequest,
    ProcessSetupChatResponse
)
from milkyhoop_protos.setup_orchestrator_pb2_grpc import SetupOrchestratorStub

logger = logging.getLogger(__name__)


class SetupOrchestratorClient:
    """gRPC client for Setup Orchestrator service"""
    
    def __init__(self, host: str = "milkyhoop-dev-setup_orchestrator-1", port: int = 5014):
        self.target = f"{host}:{port}"
        logger.info(f"SetupOrchestratorClient initialized for {self.target}")
    
    async def process_setup_chat(
        self,
        user_id: str,
        tenant_id: str,
        message: str,
        session_id: str
    ) -> Dict:
        """
        Process setup mode chat message through orchestrator
        
        Args:
            user_id: Business owner user ID
            tenant_id: Tenant/business ID
            message: User message
            session_id: Conversation session ID
            
        Returns:
            Dict with status, response, state, etc.
        """
        try:
            async with grpc.aio.insecure_channel(self.target) as channel:
                stub = SetupOrchestratorStub(channel)
                
                request = ProcessSetupChatRequest(
                    user_id=user_id,
                    tenant_id=tenant_id,
                    message=message,
                    session_id=session_id
                )
                request = ProcessSetupChatRequest(
                    user_id=user_id,
                    tenant_id=tenant_id,
                    message=message,
                    session_id=session_id
                )
                
                # CRITICAL DEBUG
                logger.info(f"🔴 [DEBUG] PRE-GRPC VALUES: user_id={user_id}, tenant_id={tenant_id}")
                logger.info(f"�� [DEBUG] REQUEST OBJECT: user_id={request.user_id}, tenant_id={request.tenant_id}")
                
                logger.info(f"[SetupOrch] Calling ProcessSetupChat | session={session_id}")
                response = await stub.ProcessSetupChat(request)
                response = await stub.ProcessSetupChat(request)
                
                # DEBUG: Log raw response progress
                logger.info(f"[DEBUG] Raw gRPC response.progress_percentage = {response.progress_percentage if hasattr(response, 'progress_percentage') else 'NOT_SET'}")
                
                # Parse response
                result = {
                    "status": response.status,
                    "milky_response": response.milky_response,
                    "current_state": response.current_state,
                    "extracted_data": dict(response.extracted_data) if hasattr(response, 'extracted_data') else {},
                    "business_data": {
                        "business_type": response.business_data.business_type if hasattr(response, 'business_data') and response.business_data else "",
                        "business_name": response.business_data.business_name if hasattr(response, 'business_data') and response.business_data else "",
                        "products_services": response.business_data.products_services if hasattr(response, 'business_data') and response.business_data else "",
                        "pricing": response.business_data.pricing if hasattr(response, 'business_data') and response.business_data else "",
                        "hours": response.business_data.hours if hasattr(response, 'business_data') and response.business_data else "",
                        "location": response.business_data.location if hasattr(response, 'business_data') and response.business_data else "",
                        "target_customers": response.business_data.target_customers if hasattr(response, 'business_data') and response.business_data else ""
                    } if hasattr(response, 'business_data') and response.business_data else None,
                    "suggested_faqs": [
                        {
                            "question": faq.question,
                            "answer": faq.answer,
                            "category": faq.category
                        }
                        for faq in (response.suggested_faqs if hasattr(response, 'suggested_faqs') else [])
                    ],
                    "next_action": response.next_action if hasattr(response, 'next_action') else "",
                    "progress_percentage": response.progress_percentage if hasattr(response, "progress_percentage") else 0,
                    "session_id": response.session_id if hasattr(response, 'session_id') else session_id
                }
                
                logger.info(f"[SetupOrch] Success | state={result['current_state']}")
                return result
                
        except grpc.aio.AioRpcError as e:
            logger.error(f"[SetupOrch] gRPC error: {e.code()} - {e.details()}")
            return {
                "status": "error",
                "error": str(e.details()),
                "code": e.code().name
            }
        except Exception as e:
            logger.error(f"[SetupOrch] Unexpected error: {str(e)}")
            return {
                "status": "error",
                "error": str(e)
            }