"""
General Handler
Handles general queries and welcome messages

Extracted from grpc_server.py - IDENTIK, no logic changes
"""

import logging
import setup_orchestrator_pb2

logger = logging.getLogger(__name__)


class GeneralHandler:
    """Handler for general queries and welcome messages"""
    
    @staticmethod
    async def handle_general(
        request,
        ctx_response,
        intent_response,
        trace_id: str,
        service_calls: list,
        progress: int,
        client_manager
    ) -> setup_orchestrator_pb2.ProcessSetupChatResponse:
        """Handle general queries"""
        logger.info(f"[{trace_id}] Handling general intent")
        
        milky_response = "Halo! Aku Milky, assistant kamu untuk setup chatbot bisnis. "
        milky_response += "Cerita dong tentang bisnis kamu? Misalnya: jenis bisnis, produk/jasa yang dijual, dll."
        
        # Update state to initial
        from conversation_manager_pb2 import UpdateStateRequest
        
        update_request = UpdateStateRequest(
            session_id=request.session_id,
            new_state="initial",
            data_json="{}",
            message=request.message
        )
        
        await client_manager.stubs['conversation_manager'].UpdateState(
            update_request
        )
        
        # Get fresh progress after state update
        from conversation_manager_pb2 import GetContextRequest
        fresh_ctx = await client_manager.stubs['conversation_manager'].GetContext(
            GetContextRequest(session_id=request.session_id)
        )
        updated_progress = getattr(fresh_ctx, "progress_percentage", 0)
        
        return setup_orchestrator_pb2.ProcessSetupChatResponse(
            status="success",
            milky_response=milky_response,
            current_state="initial",
            session_id=request.session_id,
            next_action="start_business_info_collection",
            progress_percentage=updated_progress
        )
    
    @staticmethod
    async def handle_welcome(
        request,
        ctx_response,
        trace_id: str,
        service_calls: list,
        progress: int,
        client_manager
    ) -> setup_orchestrator_pb2.ProcessSetupChatResponse:
        """Handle welcome message for first-time users"""
        logger.info(f"[{trace_id}] Handling welcome trigger")
        
        # Update state to welcome
        from conversation_manager_pb2 import UpdateStateRequest
        
        update_request = UpdateStateRequest(
            session_id=request.session_id,
            new_state="welcome",
            data_json="{}",
            message="__WELCOME__"
        )
        
        await client_manager.stubs['conversation_manager'].UpdateState(
            update_request
        )
        
        # Get fresh progress after state update
        from conversation_manager_pb2 import GetContextRequest
        fresh_ctx = await client_manager.stubs['conversation_manager'].GetContext(
            GetContextRequest(session_id=request.session_id)
        )
        updated_progress = getattr(fresh_ctx, "progress_percentage", 0)
        
        milky_response = "Halo! 👋 Aku Milky, assistant kamu untuk setup chatbot bisnis dalam 10 menit.\n\n"
        milky_response += "Gampang kok! Kita ngobrol biasa aja. Cerita dong tentang bisnis kamu? "
        milky_response += "Misalnya: jenis bisnis, produk/jasa yang dijual, dll."
        
        return setup_orchestrator_pb2.ProcessSetupChatResponse(
            status="success",
            milky_response=milky_response,
            current_state="welcome",
            session_id=request.session_id,
            next_action="start_business_info_collection",
            progress_percentage=updated_progress
        )