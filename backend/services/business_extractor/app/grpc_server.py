import asyncio
import signal
import logging
import json
from typing import Dict, Any, Optional

import grpc
from grpc import aio
from grpc_health.v1 import health, health_pb2, health_pb2_grpc
from openai import AsyncOpenAI

from app.config import settings
from app import business_extractor_pb2_grpc as pb_grpc
from app import business_extractor_pb2 as pb

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("BusinessExtractor")

# OpenAI client initialization
openai_client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)


class BusinessExtractorServicer(pb_grpc.BusinessExtractorServicer):
    """
    Business Extractor Service - Phase 1 Enhanced Implementation
    
    Purpose: Extract structured business information from natural language
    Enhancement: Incremental extraction with context merge support
    Using: GPT-3.5-turbo for cost-effective extraction
    """
    
    async def ExtractBusinessInfo(self, request, context):
        """
        PHASE 1 ENHANCED: Extract business details with incremental merge
        
        Input: {message, context (optional - existing extracted data)}
        Output: {business_name, business_type, products, common_questions, etc}
        
        Enhancement: Merges new extraction with existing context data
        """
        try:
            logger.info(f"[PHASE 1] Extracting business info from: {request.message[:100]}...")
            
            # ============================================
            # PHASE 1: Parse existing context
            # ============================================
            existing_data = {}
            if request.context:
                try:
                    existing_data = json.loads(request.context)
                    logger.info(f"[PHASE 1] Existing context loaded: {list(existing_data.keys())}")
                except json.JSONDecodeError as e:
                    logger.warning(f"Failed to parse context, starting fresh: {e}")
                    existing_data = {}
            else:
                logger.info("[PHASE 1] No existing context, fresh extraction")
            
            # ============================================
            # PHASE 1: Build context-aware extraction prompt
            # ============================================
            system_prompt = """You are a business information extraction assistant.
Extract structured business information from user messages.

IMPORTANT: Only extract NEW information mentioned in the current message.
If a field is not mentioned, return null (do not guess or repeat previous info).

INDONESIAN LANGUAGE PATTERNS - Recognize these business ownership patterns:
- Formal possessive: "cafe saya", "toko saya", "warung saya", "usaha saya"
- Casual possessive: "cafe gue", "toko gue", "warung gue", "bisnis gue"
- With ownership verb: "saya punya cafe", "gue punya toko", "aku buka warung"
- Direct mention with business details: When user mentions "cafe", "toko", "warung" followed by hours/pricing/products, treat it as their business

Context clues that indicate business ownership:
- Talking about operating hours → "cafe buka jam 8" means it's their cafe
- Talking about pricing → "toko harga mulai 50rb" means it's their toko
- Talking about products → "warung jual nasi goreng" means it's their warung


Return JSON with these fields (use null if not mentioned in THIS message):
{
  "business_name": "string or null",
  "business_type": "cafe|retail|service|ecommerce|custom_cake_shop|skincare|consulting|other",
  "target_customers": "string or null",
  "products_services": ["array of products/services"],
  "common_questions": ["array of common customer questions"],
  "pricing_info": "string or null",
  "operating_hours": "string or null",
  "location_delivery": "string or null",
  "additional_info": "any other relevant details or null"
}

Important:
- Extract ONLY what is explicitly mentioned in the current message
- Infer business_type from description if obvious
- common_questions should be actual questions customers ask
- Keep responses concise and factual
- Return null for fields not mentioned
"""
            
            # Add context awareness if existing data present
            context_info = ""
            if existing_data:
                context_info = f"\n\nExisting information already collected:\n{json.dumps(existing_data, indent=2)}\n\nExtract ONLY NEW information from the current message."
            
            user_prompt = f"""Extract business information from this message:

"{request.message}"{context_info}

Return only valid JSON, no markdown or explanation."""
            
            # ============================================
            # PHASE 1: Call GPT-3.5-turbo for extraction
            # ============================================
            response = await openai_client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                response_format={"type": "json_object"},
                temperature=0.3,  # Low temperature for consistent extraction
                max_tokens=800
            )
            
            # Parse GPT response
            new_extracted = json.loads(response.choices[0].message.content)
            logger.info(f"[PHASE 1] New extraction: {new_extracted}")
            
            # ============================================
            # PHASE 1: MERGE new extraction with existing context
            # ============================================
            merged_data = existing_data.copy()
            
            # Only update fields that have new non-null values
            if new_extracted.get("business_name"):
                merged_data["business_name"] = new_extracted["business_name"]
                logger.info(f"[MERGE] Updated business_name: {new_extracted['business_name']}")
            
            if new_extracted.get("business_type"):
                merged_data["business_type"] = new_extracted["business_type"]
                logger.info(f"[MERGE] Updated business_type: {new_extracted['business_type']}")
            
            if new_extracted.get("target_customers"):
                merged_data["target_customers"] = new_extracted["target_customers"]
                logger.info(f"[MERGE] Updated target_customers")
            
            if new_extracted.get("products_services"):
                # Merge arrays - combine with existing, avoid duplicates
                existing_products = set(merged_data.get("products_services", []))
                new_products = set(new_extracted["products_services"])
                merged_data["products_services"] = list(existing_products | new_products)
                logger.info(f"[MERGE] Merged products_services: {len(merged_data['products_services'])} items")
            
            if new_extracted.get("common_questions"):
                # Merge arrays - combine with existing, avoid duplicates
                existing_questions = set(merged_data.get("common_questions", []))
                new_questions = set(new_extracted["common_questions"])
                merged_data["common_questions"] = list(existing_questions | new_questions)
                logger.info(f"[MERGE] Merged common_questions: {len(merged_data['common_questions'])} items")
            
            if new_extracted.get("pricing_info"):
                merged_data["pricing_info"] = new_extracted["pricing_info"]
                logger.info(f"[MERGE] Updated pricing_info")
            
            if new_extracted.get("operating_hours"):
                merged_data["operating_hours"] = new_extracted["operating_hours"]
                logger.info(f"[MERGE] Updated operating_hours")
            
            if new_extracted.get("location_delivery"):
                merged_data["location_delivery"] = new_extracted["location_delivery"]
                logger.info(f"[MERGE] Updated location_delivery")
            
            if new_extracted.get("additional_info"):
                # Append additional info instead of replacing
                existing_info = merged_data.get("additional_info", "")
                new_info = new_extracted["additional_info"]
                if existing_info and new_info:
                    merged_data["additional_info"] = f"{existing_info}. {new_info}"
                elif new_info:
                    merged_data["additional_info"] = new_info
                logger.info(f"[MERGE] Appended additional_info")
            
            logger.info(f"[PHASE 1] Merge complete. Final data keys: {list(merged_data.keys())}")
            
            # ============================================
            # PHASE 1: Build protobuf response from MERGED data
            # ============================================
            return pb.ExtractBusinessInfoResponse(
                status="success",
                business_name=merged_data.get("business_name", ""),
                business_type=merged_data.get("business_type", "other"),
                target_customers=merged_data.get("target_customers", ""),
                products_services=merged_data.get("products_services", []),
                common_questions=merged_data.get("common_questions", []),
                pricing_info=merged_data.get("pricing_info", ""),
                operating_hours=merged_data.get("operating_hours", ""),
                location_delivery=merged_data.get("location_delivery", ""),
                additional_info=merged_data.get("additional_info", ""),
                confidence_score=0.85  # Can be enhanced with validation logic
            )
            
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse GPT response: {e}")
            await context.abort(
                grpc.StatusCode.INTERNAL,
                "Failed to parse extraction results"
            )
        except Exception as e:
            logger.error(f"Extraction error: {e}", exc_info=True)
            await context.abort(
                grpc.StatusCode.INTERNAL,
                f"Extraction failed: {str(e)}"
            )
    
    async def GenerateFAQSuggestions(self, request, context):
        """
        Generate FAQ suggestions based on business type and extracted info
        
        Input: {business_type, extracted_data}
        Output: {suggested_faqs: [{question, suggested_answer, category}]}
        """
        try:
            logger.info(f"Generating FAQ suggestions for: {request.business_type}")
            
            # Build FAQ generation prompt
            system_prompt = """You are an FAQ generation expert.
Generate relevant FAQ questions and answers based on business information.

Return JSON array of FAQs:
[
  {
    "question": "Question customers commonly ask",
    "suggested_answer": "Suggested answer template (use placeholders like [PRICE], [HOURS])",
    "category": "pricing|products|hours|delivery|services|policies|other",
    "priority": "high|medium|low"
  }
]

Generate 5-10 most important FAQs for the business type.
Focus on questions that reduce customer support burden.
"""
            
            user_prompt = f"""You are a business FAQ generator.
            
            Generate 5-10 frequently asked questions for this business:
            Business Type: {request.business_type}
            Products/Services: {request.products_services}
            
            Return ONLY valid JSON in this EXACT format:
            {{
              "faqs": [
                {{
                  "question": "Question in Indonesian",
                  "suggested_answer": "Detailed answer in Indonesian (minimum 3 sentences, be specific and helpful)",
                  "category": "pricing|delivery|hours|payment|other",
                  "priority": "high|medium|low"
                }}
              ]
            }}
            
            Generate minimum 5 FAQs using natural Indonesian language.
            """
            
            # Call GPT-3.5-turbo
            response = await openai_client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                response_format={"type": "json_object"},
                temperature=0.5,  # Slightly higher for creative FAQ generation
                max_tokens=1200
            )
            
            # Parse response
            result = json.loads(response.choices[0].message.content)
            
            # Handle different GPT response formats
            if isinstance(result, dict) and "faqs" in result:
                faqs = result["faqs"]
            elif isinstance(result, dict) and "question" in result:
                # Single FAQ - wrap in array
                faqs = [result]
                logger.warning("GPT returned single FAQ, expected array")
            elif isinstance(result, list):
                faqs = result
            else:
                faqs = []
                logger.error(f"Unexpected GPT response format: {type(result)}")
            
            logger.info(f"Generated {len(faqs)} FAQ suggestions")
            
            # Build protobuf response
            faq_responses = []
            for faq in faqs:
                faq_responses.append(pb.FAQSuggestion(
                    question=faq.get("question", ""),
                    suggested_answer=faq.get("suggested_answer", ""),
                    category=faq.get("category", "other"),
                    priority=faq.get("priority", "medium")
                ))
            
            return pb.GenerateFAQSuggestionsResponse(
                status="success",
                suggested_faqs=faq_responses
            )
            
        except Exception as e:
            logger.error(f"FAQ generation error: {e}")
            await context.abort(
                grpc.StatusCode.INTERNAL,
                f"FAQ generation failed: {str(e)}"
            )
    
    async def ValidateBusinessData(self, request, context):
        """
        Validate extracted business data for completeness
        
        Input: {extracted_data}
        Output: {is_complete, missing_fields, suggestions}
        """
        try:
            logger.info("Validating business data completeness")
            
            missing_fields = []
            critical_fields = ["business_name", "business_type", "common_questions"]
            
            # Check critical fields
            if not request.business_name:
                missing_fields.append("business_name")
            if not request.business_type or request.business_type == "other":
                missing_fields.append("business_type")
            if not request.common_questions:
                missing_fields.append("common_questions")
            
            is_complete = len(missing_fields) == 0
            
            # Generate suggestions for missing fields
            suggestions = []
            if "business_name" in missing_fields:
                suggestions.append("Tanya: 'Apa nama bisnis kamu?'")
            if "business_type" in missing_fields:
                suggestions.append("Tanya: 'Bisnis kamu bergerak di bidang apa?'")
            if "common_questions" in missing_fields:
                suggestions.append("Tanya: 'Biasanya customer tanya apa aja?'")
            
            logger.info(f"Validation complete: is_complete={is_complete}, missing={len(missing_fields)}")
            
            return pb.ValidateBusinessDataResponse(
                is_complete=is_complete,
                missing_fields=missing_fields,
                suggestions=suggestions,
                completeness_score=1.0 - (len(missing_fields) / len(critical_fields))
            )
            
        except Exception as e:
            logger.error(f"Validation error: {e}")
            await context.abort(
                grpc.StatusCode.INTERNAL,
                f"Validation failed: {str(e)}"
            )


async def serve() -> None:
    """Start gRPC server"""
    server = aio.server()
    pb_grpc.add_BusinessExtractorServicer_to_server(
        BusinessExtractorServicer(), 
        server
    )

    # Health check
    health_servicer = health.HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)
    health_servicer.set("", health_pb2.HealthCheckResponse.SERVING)

    listen_addr = f"[::]:{settings.GRPC_PORT}"
    server.add_insecure_port(listen_addr)
    
    logger.info(f"Business Extractor gRPC server listening on port {settings.GRPC_PORT}")
    logger.info("Phase 1 Enhanced Implementation: Incremental extraction with context merge ready")

    # Graceful shutdown handling
    stop_event = asyncio.Event()

    def handle_shutdown(*_):
        logger.info("Shutdown signal received. Cleaning up...")
        stop_event.set()

    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    try:
        await server.start()
        await stop_event.wait()
    finally:
        await server.stop(5)
        logger.info("gRPC server shut down cleanly.")


if __name__ == "__main__":
    asyncio.run(serve())