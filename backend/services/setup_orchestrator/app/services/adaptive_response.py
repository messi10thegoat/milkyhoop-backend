"""
Adaptive Response Generator - OpenAI v1.x Powered Version
Generates truly natural, context-aware conversational responses using OpenAI GPT
"""

import logging
import os
import json
from typing import Dict, Any, List, Optional
import asyncio
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)


class AdaptiveResponseGenerator:
    """
    OpenAI-Powered response generator for natural setup conversations
    
    Features:
    - GPT-like natural conversation flow
    - Context-aware acknowledgments
    - Dynamic follow-up questions
    - Persona detection and adaptation
    - Emoji usage for engagement
    - No repetitive templates
    
    Technical:
    - Uses OpenAI v1.x AsyncOpenAI client
    - Async/await compatible
    - Field mapping corrected (operating_hours->hours, etc)
    """
    
    # Field mapping: Extractor -> Proto canonical names
    FIELD_MAPPING = {
        'operating_hours': 'hours',
        'location_delivery': 'location',
        'pricing_info': 'pricing'
    }
    
    # Field importance for response prioritization
    FIELD_PRIORITY = {
        'business_type': 1,      # Critical
        'business_name': 1,      # Critical
        'target_customers': 1,   # Critical
        'products_services': 2,  # Core
        'pricing': 2,            # Core
        'hours': 2,              # Core
        'location': 3            # Context
    }
    
    @staticmethod
    def _normalize_field_name(field_name: str) -> str:
        """Convert extractor field names to proto canonical names"""
        return AdaptiveResponseGenerator.FIELD_MAPPING.get(field_name, field_name)
    
    @staticmethod
    def _build_conversation_context(
        extracted_new: Dict[str, Any],
        existing_data: Dict[str, Any],
        merged_data: Dict[str, Any]
    ) -> str:
        """
        Build conversation context for LLM prompt
        
        Args:
            extracted_new: Newly extracted data
            existing_data: Previous conversation data
            merged_data: Complete merged data
            
        Returns:
            Formatted context string
        """
        context_parts = []
        
        # What we just learned
        new_info = []
        for key, value in extracted_new.items():
            if value and key not in existing_data:
                canonical_key = AdaptiveResponseGenerator._normalize_field_name(key)
                if isinstance(value, list):
                    new_info.append(f"- {canonical_key}: {', '.join(value)}")
                else:
                    new_info.append(f"- {canonical_key}: {value}")
        
        if new_info:
            context_parts.append("NEWLY EXTRACTED INFO:\n" + "\n".join(new_info))
        
        # What we already know
        existing_info = []
        for key, value in existing_data.items():
            if value:
                canonical_key = AdaptiveResponseGenerator._normalize_field_name(key)
                if isinstance(value, list) and value:
                    existing_info.append(f"- {canonical_key}: {', '.join(value)}")
                elif not isinstance(value, list):
                    existing_info.append(f"- {canonical_key}: {value}")
        
        if existing_info:
            context_parts.append("ALREADY KNOWN:\n" + "\n".join(existing_info))
        
        # What's still missing
        missing = AdaptiveResponseGenerator._identify_missing_fields(merged_data)
        if missing:
            missing_readable = [AdaptiveResponseGenerator._field_to_readable(f) for f in missing]
            context_parts.append(f"STILL MISSING: {', '.join(missing_readable)}")
        else:
            context_parts.append("STATUS: All core info collected! ✅")
        
        return "\n\n".join(context_parts)
    
    @staticmethod
    def _field_to_readable(field_name: str) -> str:
        """Convert field name to human-readable Indonesian"""
        readable = {
            'business_name': 'nama bisnis',
            'hours': 'jam operasional',
            'location': 'lokasi',
            'products_services': 'produk/layanan',
            'pricing': 'harga',
            'target_customers': 'target customer'
        }
        return readable.get(field_name, field_name)
    
    @staticmethod
    async def generate_response(
        extracted_new: Dict[str, Any],
        existing_data: Dict[str, Any],
        merged_data: Dict[str, Any]
    ) -> str:
        """
        Generate natural OpenAI-powered response
        
        Args:
            extracted_new: Newly extracted data from current turn
            existing_data: Data from previous turns
            merged_data: Combined data after merge
            
        Returns:
            Natural language response string
        """
        try:
            # Build conversation context
            context = AdaptiveResponseGenerator._build_conversation_context(
                extracted_new, existing_data, merged_data
            )
            
            # Identify missing fields for follow-up
            missing_fields = AdaptiveResponseGenerator._identify_missing_fields(merged_data)
            next_field = missing_fields[0] if missing_fields else None
            
            # Build LLM prompt
            prompt = AdaptiveResponseGenerator._build_llm_prompt(
                context=context,
                has_new_info=bool(extracted_new),
                next_field=next_field,
                is_complete=not missing_fields
            )
            
            # Call OpenAI
            response = await AdaptiveResponseGenerator._call_openai(prompt)
            
            return response.strip()
            
        except Exception as e:
            logger.error(f"OpenAI response generation failed: {e}", exc_info=True)
            # Fallback to basic template if OpenAI fails
            return AdaptiveResponseGenerator._fallback_response(
                extracted_new, existing_data, missing_fields if 'missing_fields' in locals() else []
            )
    
    @staticmethod
    def _build_llm_prompt(
        context: str,
        has_new_info: bool,
        next_field: Optional[str],
        is_complete: bool
    ) -> str:
        """
        Build optimized LLM prompt for response generation
        
        Args:
            context: Conversation context
            has_new_info: Whether new info was extracted
            next_field: Next field to ask about (if any)
            is_complete: Whether all info is collected
            
        Returns:
            Prompt string
        """
        prompt = f"""Kamu adalah Milky, AI assistant yang membantu business owner setup chatbot mereka. Personality kamu:
- Casual, friendly, seperti teman yang helpful
- Pakai bahasa Indonesia casual (gue/kamu/oke/sip/mantap)
- Natural conversation flow, bukan robotic
- Pakai emoji secukupnya untuk warmth (😊 🎉 👍 ✨)
- Acknowledge info baru dengan cara yang varied, jangan template
- Follow-up questions yang natural, bukan checklist

CONVERSATION CONTEXT:
{context}

TASK: Generate 1 response message yang:
1. Acknowledge info baru (kalau ada) dengan cara natural dan varied
2. {"Ask about: " + AdaptiveResponseGenerator._field_to_readable(next_field) if next_field else "Confirm completion dan next steps"}
3. Max 2-3 sentences, conversational
4. Jangan repetitive pattern seperti "Namanya X. Buka jam Y."
5. Sound like WhatsApp chat, bukan form filling

{"SITUATION: User baru kasih info baru. Acknowledge dengan enthusiastic tapi varied." if has_new_info else ""}
{"SITUATION: Semua info sudah lengkap! Celebrate dan suggest next action." if is_complete else ""}

Generate response sekarang (Indonesian casual only, no English):"""

        return prompt
    
    @staticmethod
    async def _call_openai(prompt: str) -> str:
        """
        Call OpenAI GPT via OpenAI v1.x AsyncOpenAI client
        
        Args:
            prompt: Formatted prompt
            
        Returns:
            Generated response text
        """
        try:
            # Get API key from environment
            api_key = os.getenv('OPENAI_API_KEY')
            if not api_key:
                logger.warning("OPENAI_API_KEY not set, using fallback")
                raise ValueError("API key not configured")
            
            # Initialize OpenAI v1.x AsyncOpenAI client
            client = AsyncOpenAI(api_key=api_key)
            
            # Call OpenAI Chat Completions API
            response = await client.chat.completions.create(
                model="gpt-4o-mini",  # Cost-effective, fast, good quality
                messages=[
                    {
                        "role": "system",
                        "content": "Kamu adalah Milky, friendly AI assistant untuk setup chatbot bisnis. Respond dalam bahasa Indonesia casual dengan emoji natural."
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                max_tokens=200,      # Keep responses concise
                temperature=0.9,     # High creativity for natural variation
                top_p=0.95,
                frequency_penalty=0.3,  # Reduce repetition
                presence_penalty=0.3    # Encourage topic diversity
            )
            
            # Extract response text (OpenAI v1.x format)
            response_text = response.choices[0].message.content
            
            logger.info(f"OpenAI response generated: {len(response_text)} chars, model={response.model}")
            return response_text
            
        except Exception as e:
            logger.error(f"OpenAI API call failed: {e}")
            raise
    
    @staticmethod
    def _fallback_response(
        extracted_new: Dict[str, Any],
        existing_data: Dict[str, Any],
        missing_fields: List[str]
    ) -> str:
        """
        Fallback response if OpenAI fails (simple but functional)
        
        Args:
            extracted_new: New data
            existing_data: Existing data
            missing_fields: Missing fields list
            
        Returns:
            Basic response string
        """
        response = "Oke, noted! "
        
        # Acknowledge something new
        if extracted_new:
            if extracted_new.get('business_name'):
                response = f"Mantap! {extracted_new['business_name']} ya. "
            elif extracted_new.get('business_type'):
                response = f"Sip! Bisnis {extracted_new['business_type']} nih. "
        
        # Ask next question
        if missing_fields:
            next_field = missing_fields[0]
            questions = {
                'business_name': 'Nama bisnisnya apa? 😊',
                'hours': 'Jam operasionalnya gimana?',
                'location': 'Lokasinya dimana nih?',
                'products_services': 'Produk atau jasa apa aja yang dijual?',
                'pricing': 'Untuk harganya gimana?',
                'target_customers': 'Target customernya siapa?'
            }
            response += questions.get(next_field, 'Ada info lain?')
        else:
            response += 'Info bisnis udah lengkap! Mau lanjut setup FAQ? 🎉'
        
        return response
    
    @staticmethod
    def _identify_missing_fields(data: Dict[str, Any]) -> List[str]:
        """
        Identify missing required fields in priority order
        
        Args:
            data: Current business data (uses canonical proto field names)
            
        Returns:
            List of missing field names (canonical proto names)
        """
        # Required fields with priorities (using proto canonical names)
        required_fields = [
            ('business_name', 1),
            ('business_type', 1),
            ('target_customers', 1),
            ('products_services', 2),
            ('pricing', 2),
            ('hours', 2),  # NOT operating_hours
            ('location', 3)
        ]
        
        missing = []
        
        for field, priority in required_fields:
            value = data.get(field)
            # Check if field is empty
            if not value:
                missing.append(field)
            # Special handling for lists
            elif isinstance(value, list) and not value:
                missing.append(field)
        
        # Sort by priority (lower number = higher priority)
        missing_with_priority = [
            (field, AdaptiveResponseGenerator.FIELD_PRIORITY.get(field, 99))
            for field in missing
        ]
        missing_with_priority.sort(key=lambda x: x[1])
        
        return [field for field, _ in missing_with_priority]
    
    @staticmethod
    def generate_completion_response(tenant_id: str) -> str:
        """
        Generate response for setup completion
        
        Args:
            tenant_id: Tenant identifier for chatbot URL
            
        Returns:
            Completion response with chatbot URL
        """
        chatbot_url = f"https://milkyhoop.com/{tenant_id}"
        
        # Varied completion messages
        import random
        celebrations = [
            "Mantap! Setup chatbot kamu udah selesai! 🎉",
            "Yeay! Chatbot kamu sudah ready! ✨",
            "Sukses! Setup chatbot complete! 🚀",
            "Done! Chatbot kamu udah aktif nih! 💪"
        ]
        
        response = random.choice(celebrations) + "\n\n"
        response += f"Chatbot kamu bisa diakses di: {chatbot_url}\n\n"
        response += "Customer sekarang bisa langsung chat dan dapet jawaban otomatis. "
        response += "Share link ini di Instagram bio atau social media kamu ya! 😊"
        
        return response