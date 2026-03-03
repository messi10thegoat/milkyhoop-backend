"""
Enrichment Orchestrator - LLM function calling loop for master data validation.
Pattern identical to ragllm_service orchestrator.
"""
import json
import logging
from datetime import date
from typing import Dict, Any, Optional, List
from openai import AsyncOpenAI

from .tools import ENRICHMENT_TOOLS, ToolExecutor
from .config import settings

logger = logging.getLogger(__name__)


class EnrichmentOrchestrator:
    """
    LLM function calling loop for master data enrichment.
    
    Flow:
    1. User request → LLM with tools
    2. LLM calls tools (search_customers, search_items, etc.)
    3. Execute tools → return results to LLM
    4. LLM analyzes results:
       - If ambiguous → return CLARIFICATION
       - If complete → return ACTION_PREVIEW with enriched payload
    """
    
    def __init__(self):
        self.llm = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        self.tool_executor = ToolExecutor(settings.API_GATEWAY_URL)
        self.max_iterations = 5
        self.model = "gpt-4o"  # Better for function calling than gpt-4o-mini
    
    async def enrich_and_plan(
        self, 
        user_text: str, 
        action_type: str,
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Main orchestration loop - LLM calls tools until data is complete.
        
        Args:
            user_text: Original user request
            action_type: Action type (CREATE_SALES_INVOICE, etc.)
            context: Request context (tenant_id, user_id, etc.)
        
        Returns:
            Either:
            - CLARIFICATION message if needs user input
            - ACTION_PREVIEW with complete enriched payload
        """
        messages = [
            {
                "role": "user",
                "content": f"Today's date is {date.today().isoformat()}.\n\nUser request: {user_text}\n\nAction type: {action_type}\n\nContext: {json.dumps(context, indent=2)}"
            }
        ]
        
        system_prompt = self._get_system_prompt(action_type)
        
        for iteration in range(self.max_iterations):
            logger.info(f"Orchestrator iteration {iteration + 1}/{self.max_iterations}")
            
            try:
                # LLM call with tools
                # Convert to OpenAI format: system prompt as first message
                openai_messages = [{"role": "system", "content": system_prompt}] + messages
                
                response = await self.llm.chat.completions.create(
                    model=self.model,
                    messages=openai_messages,
                    tools=ENRICHMENT_TOOLS,
                    tool_choice="auto"
                )
                
                # Add assistant response to conversation (OpenAI format)
                assistant_message = response.choices[0].message
                messages.append({
                    "role": "assistant",
                    "content": assistant_message.content,
                    "tool_calls": assistant_message.tool_calls if assistant_message.tool_calls else None
                })
                
                # Check if LLM wants to use tools
                tool_calls = assistant_message.tool_calls or []
                
                if tool_calls:
                    # Execute all tool calls
                    for tool_call in tool_calls:
                        logger.info(f"Executing tool: {tool_call.function.name}")
                        
                        # Parse function arguments (OpenAI sends as JSON string)
                        args = json.loads(tool_call.function.arguments)
                        
                        result = await self.tool_executor.execute(
                            tool_name=tool_call.function.name,
                            args=args,
                            context=context
                        )
                        
                        # Add tool result to conversation (OpenAI format)
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "name": tool_call.function.name,
                            "content": json.dumps(result)
                        })
                    
                    # Continue loop - LLM will see tool results
                    continue
                
                # No more tool calls - LLM has finished reasoning
                # Extract final output (OpenAI format)
                final_text = assistant_message.content
                if not final_text:
                    logger.warning("LLM returned no content")
                    return self._create_error_response("No response from LLM")
                
                # Try to extract JSON from response
                try:
                    # Look for JSON block in markdown
                    if "```json" in final_text:
                        json_start = final_text.index("```json") + 7
                        json_end = final_text.index("```", json_start)
                        json_str = final_text[json_start:json_end].strip()
                        final_output = json.loads(json_str)
                    else:
                        # Try to parse entire response as JSON
                        final_output = json.loads(final_text)
                except json.JSONDecodeError:
                    logger.warning(f"Failed to parse LLM output as JSON: {final_text[:200]}")
                    return self._create_error_response("Invalid LLM response format")
                
                # Route based on output type
                if final_output.get("needs_clarification"):
                    return {
                        "message_type": "CLARIFICATION",
                        "question": final_output.get("question", "Mohon berikan informasi lebih detail."),
                        "options": final_output.get("options", []),
                        "context": final_output.get("context", {})
                    }
                
                # All data resolved - return complete plan
                return {
                    "message_type": "ACTION_PREVIEW",
                    "draft_payload": final_output.get("payload", {}),
                    "assumptions": final_output.get("assumptions", []),
                    "warnings": final_output.get("warnings", []),
                    "side_effects": final_output.get("side_effects", [])
                }
                
            except Exception as e:
                logger.error(f"Orchestrator iteration {iteration + 1} failed: {e}", exc_info=True)
                if iteration == self.max_iterations - 1:
                    return self._create_error_response(str(e))
                continue
        
        # Max iterations reached
        logger.warning("Max iterations reached without completion")
        return {
            "message_type": "CLARIFICATION",
            "question": "Saya butuh informasi lebih lengkap untuk memproses transaksi ini. Bisa jelaskan detail lengkapnya?",
            "options": [],
            "context": {}
        }
    
    def _get_system_prompt(self, action_type: str) -> str:
        """Generate system prompt based on action type."""
        
        base_prompt = """You are a master data enrichment agent for an accounting system.

Your job:
1. Parse user request to extract entities (customer, items, amounts, dates)
2. Use available tools to search master data and resolve IDs
3. If data is ambiguous or missing → ask for clarification
4. When all required data is resolved → generate complete payload

Available tools:
- search_customers(query) → find customer by name/email
- search_items(query) → fuzzy search items/products
- get_item_details(item_id) → get full item details (price, unit, stock)
- search_accounts(query, account_type) → find GL account

Response format (JSON):
{
  "needs_clarification": bool,
  "question": "string (if clarification needed)",
  "options": [...] (if multiple choices),
  "context": {...} (state to preserve),
  "payload": {...} (if all resolved),
  "assumptions": [...],
  "warnings": [...],
  "side_effects": [...]
}

CRITICAL RULES:
- ALWAYS use tools to validate master data before generating payload
- If customer name mentioned → search_customers() to get customer_id
- If item mentioned → search_items() to check if exists
- If search returns 0 results → ask if user wants to create new
- If search returns multiple → ask user to pick one
- If search returns 1 → auto-fill data from master
- NEVER generate payload with missing IDs or zero prices
- Default quantity to 1 if not mentioned
"""
        
        if action_type == "CREATE_SALES_INVOICE":
            return base_prompt + """

For sales invoices, required fields:
- customer_id (MUST resolve via search_customers)
- customer_name (from search_customers result, for display)
- items[] array with:
  - item_id (MUST resolve via search_items)
  - description (from master data)
  - quantity (default 1 if not mentioned)
  - unit_price (from get_item_details)
  - unit (from master data)
- invoice_date (default to today if not mentioned)
- due_date (default to invoice_date + 30 days)

Example flow:
User: "faktur ke grapgrap, kaos 50"

Step 1: search_customers("grapgrap")
→ Found 1 customer: {id: "abc", name: "Grapgrap Clothing"}
→ customer_id = "abc" ✓

Step 2: search_items("kaos")
→ Found 5 items: Kaos Polos, Kaos V-neck, etc.
→ Multiple matches → CLARIFICATION needed

Return:
{
  "needs_clarification": true,
  "question": "Ada 5 jenis kaos. Pilih yang mana?",
  "options": [
    {"label": "Kaos Polos Hitam", "value": "item_id_1", "description": "Rp 85,000"},
    {"label": "Kaos V-neck Putih", "value": "item_id_2", "description": "Rp 95,000"},
    ...
  ],
  "context": {
    "customer_id": "abc",
    "quantity": 50,
    "state": "waiting_for_item_selection"
  }
}

After user selects item:
Step 3: get_item_details(selected_id)
→ {sell_price: 85000, unit: "pcs"}

Step 4: Generate complete payload
{
  "needs_clarification": false,
  "payload": {
    "customer_id": "abc",
    "items": [
      {
        "item_id": "selected_id",
        "description": "Kaos Polos Hitam",
        "quantity": 50,
        "unit_price": 85000,
        "unit": "pcs"
      }
    ],
    "invoice_date": "2026-02-14",
    "due_date": "2026-03-16"
  },
  "assumptions": [
    "Tanggal faktur: hari ini (2026-02-14)",
    "Jatuh tempo: NET 30 (2026-03-16)"
  ]
}
"""
        
        return base_prompt
    
    def _create_error_response(self, error_msg: str) -> Dict[str, Any]:
        """Create error response in CLARIFICATION format."""
        return {
            "message_type": "CLARIFICATION",
            "question": f"Maaf, terjadi kesalahan: {error_msg}. Bisa coba lagi dengan informasi lebih detail?",
            "options": [],
            "context": {}
        }
