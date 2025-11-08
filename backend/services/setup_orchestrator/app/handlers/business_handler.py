"""
Business Handler
Handles business setup, FAQ creation, and setup confirmation

Extracted from grpc_server.py - IDENTIK, no logic changes
"""

import logging
import json
import setup_orchestrator_pb2
from services import DataCleaner, ProgressCalculator, AdaptiveResponseGenerator

logger = logging.getLogger(__name__)


class BusinessHandler:
    """Handler for business information collection and setup"""
    
    @staticmethod
    async def handle_business_setup(
        request,
        ctx_response,
        intent_response,
        trace_id: str,
        service_calls: list,
        progress: int,
        session_manager,
        client_manager
    ) -> setup_orchestrator_pb2.ProcessSetupChatResponse:
        """
        PHASE 1 ENHANCED: Handle business information collection with continuous extraction
        """
        logger.info(f"[{trace_id}] Handling business_setup intent (PHASE 1 ENHANCED)")
        
        # ============================================
        # PHASE 1: Get existing context from Redis
        # ============================================
        session_context = await session_manager.get_context(request.session_id)
        existing_business_data = session_context.get("business_data", {})
        
        logger.info(f"[{trace_id}] Existing business data: {existing_business_data}")
        
        # ============================================
        # PHASE 1: Extract NEW info from current message
        # ============================================
        from datetime import datetime
        extract_start = datetime.now()
        
        from business_extractor_pb2 import ExtractBusinessInfoRequest
        extract_request = ExtractBusinessInfoRequest(
            message=request.message,
            context=json.dumps(existing_business_data)  # Pass existing for merge
        )
        
        extract_response = await client_manager.stubs['business_extractor'].ExtractBusinessInfo(
            extract_request
        )
        
        extract_duration = (datetime.now() - extract_start).total_seconds() * 1000
        service_calls.append({
            "service_name": "business_extractor",
            "method": "ExtractBusinessInfo",
            "duration_ms": int(extract_duration),
            "status": "success"
        })
        
        logger.info(
            f"[{trace_id}] Extraction completed | "
            f"type={extract_response.business_type} | "
            f"name={extract_response.business_name} | "
            f"duration={extract_duration:.0f}ms"
        )
        
        # ============================================
        # Clean extracted data before merge
        # ============================================
        raw_data = {
            'business_name': extract_response.business_name,
            'pricing_info': extract_response.pricing_info,
            'products_services': list(extract_response.products_services) if extract_response.products_services else [],
            'location': extract_response.location_delivery,
            'target_customers': extract_response.target_customers,
            'operating_hours': extract_response.operating_hours
        }
        cleaned_data = DataCleaner.clean_all_fields(raw_data)
        logger.info(f"[{trace_id}] Data cleaned: {cleaned_data}")

        # ============================================
        # PHASE 1: MERGE extracted data (incremental, not replace)
        # ============================================
        merged_data = existing_business_data.copy()

        # Build extracted_new dict for adaptive response
        extracted_new = {}

        if extract_response.business_type:
            if not existing_business_data.get("business_type"):
                extracted_new["business_type"] = extract_response.business_type
            merged_data["business_type"] = extract_response.business_type
            
        if extract_response.business_name:
            if not existing_business_data.get("business_name"):
                extracted_new["business_name"] = extract_response.business_name
            merged_data["business_name"] = cleaned_data.get('business_name', extract_response.business_name)
            
        if extract_response.products_services:
            if not existing_business_data.get("products_services"):
                extracted_new["products_services"] = list(extract_response.products_services)
            merged_data["products_services"] = cleaned_data.get('products_services', list(extract_response.products_services))
            
        if extract_response.operating_hours:
            if not existing_business_data.get("operating_hours"):
                extracted_new["operating_hours"] = extract_response.operating_hours
            merged_data["hours"] = extract_response.operating_hours
            
        if extract_response.location_delivery:
            if not existing_business_data.get("location"):
                extracted_new["location_delivery"] = extract_response.location_delivery
            merged_data["location"] = cleaned_data.get('location', extract_response.location_delivery)
            
        if extract_response.pricing_info:
            if not existing_business_data.get("pricing"):
                extracted_new["pricing_info"] = extract_response.pricing_info
            merged_data["pricing"] = cleaned_data.get('pricing_info', extract_response.pricing_info)

        # Merge target_customers
        if cleaned_data.get('target_customers'):
            if not existing_business_data.get("target_customers"):
                extracted_new["target_customers"] = cleaned_data['target_customers']
            merged_data["target_customers"] = cleaned_data['target_customers']

        # Merge operating_hours  
        if cleaned_data.get('operating_hours'):
            if not existing_business_data.get("operating_hours"):
                extracted_new["operating_hours"] = cleaned_data['operating_hours']
            merged_data["hours"] = cleaned_data['operating_hours']
        
        logger.info(f"[{trace_id}] Merged business data: {merged_data}")
        
        # ============================================
        # PHASE 1: Calculate progress using ProgressCalculator
        # ============================================
        calculated_progress = ProgressCalculator.calculate_progress(merged_data)
        
        logger.info(
            f"[{trace_id}] Progress calculation | "
            f"progress={calculated_progress}%"
        )
        
        # ============================================
        # PHASE 1: Save to Redis session
        # ============================================
        session_context["business_data"] = merged_data
        session_context["progress"] = calculated_progress
        await session_manager.save_context(request.session_id, session_context)
        
        # ============================================
        # Also update conversation_manager (for compatibility)
        # ============================================
        from conversation_manager_pb2 import UpdateStateRequest
        update_request = UpdateStateRequest(
            session_id=request.session_id,
            new_state="collecting_info",
            data_json=json.dumps(merged_data),
            message=request.message
        )
        
        await client_manager.stubs['conversation_manager'].UpdateState(
            update_request
        )
        
        # Get fresh context with updated progress
        from conversation_manager_pb2 import GetContextRequest
        fresh_ctx = await client_manager.stubs['conversation_manager'].GetContext(
            GetContextRequest(session_id=request.session_id)
        )
        updated_progress = getattr(fresh_ctx, "progress_percentage", calculated_progress)
        
        # ============================================
        # PHASE 1: Generate adaptive response using AdaptiveResponseGenerator
        # ============================================
        milky_response = await AdaptiveResponseGenerator.generate_response(
            extracted_new=extracted_new,
            existing_data=existing_business_data,
            merged_data=merged_data
        )
        
        # ============================================
        # PHASE 1: Save conversation turn
        # ============================================
        await session_manager.add_turn(
            request.session_id,
            request.message,
            milky_response
        )
        
        # Build business data from merged data
        business_data = setup_orchestrator_pb2.BusinessData(
            business_type=merged_data.get("business_type", ""),
            business_name=merged_data.get("business_name", ""),
            products_services=", ".join(merged_data.get("products_services", [])) if merged_data.get("products_services") else "",
            pricing=merged_data.get("pricing", ""),
            hours=merged_data.get("hours", ""),
            location=merged_data.get("location", ""),
            target_customers=merged_data.get("target_customers", "")
        )

        return setup_orchestrator_pb2.ProcessSetupChatResponse(
            status="success",
            milky_response=milky_response,
            current_state="collecting_info",
            session_id=request.session_id,
            business_data=business_data,
            progress_percentage=calculated_progress,
            next_action="continue_collecting_info"
        )
    
    @staticmethod
    async def handle_faq_create(
        request,
        ctx_response,
        intent_response,
        trace_id: str,
        service_calls: list,
        progress: int,
        client_manager
    ) -> setup_orchestrator_pb2.ProcessSetupChatResponse:
        """Handle FAQ creation request"""
        logger.info(f"[{trace_id}] Handling faq_create intent")
        
        # Parse business data from context
        try:
            business_data = json.loads(ctx_response.extracted_data_json)
        except:
            business_data = {}
        
        if not business_data.get("business_type"):
            milky_response = "Hmm, kayaknya info bisnis kamu belum lengkap. Mau cerita dulu tentang bisnisnya?"
            return setup_orchestrator_pb2.ProcessSetupChatResponse(
                status="success",
                milky_response=milky_response,
                current_state=ctx_response.current_state,
                session_id=request.session_id,
                progress_percentage=progress,
                next_action="collect_business_info"
            )
        
        # Generate FAQ suggestions based on business info
        from datetime import datetime
        faq_start = datetime.now()
        
        from ragllm_service_pb2 import GenerateFAQRequest
        faq_request = GenerateFAQRequest(
            tenant_id=request.tenant_id,
            business_context=json.dumps(business_data),
            user_message=request.message
        )
        
        faq_response = await client_manager.stubs['ragllm'].GenerateFAQ(
            faq_request
        )
        
        faq_duration = (datetime.now() - faq_start).total_seconds() * 1000
        service_calls.append({
            "service_name": "ragllm",
            "method": "GenerateFAQ",
            "duration_ms": int(faq_duration),
            "status": "success"
        })
        
        # Create FAQs in database
        created_faq_ids = []
        for faq in faq_response.suggested_faqs:
            from ragcrud_service_pb2 import CreateFAQRequest
            create_req = CreateFAQRequest(
                tenant_id=request.tenant_id,
                question=faq.question,
                answer=faq.answer,
                category="auto_generated"
            )
            
            create_resp = await client_manager.stubs['ragcrud'].CreateFAQ(create_req)
            if create_resp.faq_id:
                created_faq_ids.append(create_resp.faq_id)
        
        # Update state
        from conversation_manager_pb2 import UpdateStateRequest
        
        update_request = UpdateStateRequest(
            session_id=request.session_id,
            new_state="faqs_created",
            data_json=json.dumps({
                **business_data,
                "faq_ids": created_faq_ids
            }),
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
        
        # Generate chatbot URL
        chatbot_url = f"https://milkyhoop.com/{request.tenant_id}"
        
        milky_response = f"Done! ✅ Aku udah bikin {len(created_faq_ids)} FAQs untuk bisnis kamu.\n\n"
        milky_response += f"🎉 Chatbot kamu udah siap di: {chatbot_url}\n\n"
        milky_response += "Pasang link ini di Instagram bio atau share ke customer ya!"
        
        # Format suggested FAQs for response
        suggested_faqs = [
            setup_orchestrator_pb2.SuggestedFAQ(
                question=faq.question,
                answer=faq.answer
            )
            for faq in faq_response.suggested_faqs
        ]
        
        return setup_orchestrator_pb2.ProcessSetupChatResponse(
            status="success",
            milky_response=milky_response,
            current_state="faqs_created",
            session_id=request.session_id,
            suggested_faqs=suggested_faqs,
            next_action="setup_complete",
            progress_percentage=updated_progress
        )

    @staticmethod
    async def handle_confirm_setup(
        request,
        ctx_response,
        intent_response,
        trace_id: str,
        service_calls: list,
        progress: int,
        client_manager
    ) -> setup_orchestrator_pb2.ProcessSetupChatResponse:
        """Handle setup confirmation"""
        logger.info(f"[{trace_id}] Handling confirm_setup intent")
        
        # Generate chatbot URL
        chatbot_url = f"https://milkyhoop.com/{request.tenant_id}"
        
        milky_response = "Mantap! Setup chatbot kamu udah selesai! 🎉\n\n"
        milky_response += f"Chatbot kamu bisa diakses di: {chatbot_url}\n\n"
        milky_response += "Sekarang customer bisa langsung chat dan dapet jawaban otomatis dari FAQ yang udah kita bikin."
        
        # Update state to setup_complete
        from conversation_manager_pb2 import UpdateStateRequest
        
        update_request = UpdateStateRequest(
            session_id=request.session_id,
            new_state="setup_complete",
            data_json=ctx_response.extracted_data_json,
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
            current_state="setup_complete",
            session_id=request.session_id,
            next_action="setup_complete",
            progress_percentage=updated_progress
        )