"""
Session Manager — manages 4-layer memory for the unified agent.

Responsibilities:
- Create/retrieve chat sessions (Layer 0)
- Store/retrieve messages (Layer 1: Working Window)
- Manage structured state (Layer 2: Authoritative Context)
- Log events (Layer 3: Event Log)
- Generate summaries (Layer 4: Conversational Summary)
- Assemble context for LLM calls (Token Budget Manager)
- User Preferences (Cross-session auto-learning, Phase C)

CRITICAL RULES:
- LLM NEVER updates Layer 2 directly
- Layer 2 updated ONLY by backend hooks (deterministic)
- All financial data queried from DB/kernel, NEVER cached
- Layer 2 stores entity REFERENCES only, not balances/amounts
- Preferences are READ-ONLY for LLM (injected into system prompt)
- Preferences updated ONLY by backend hooks after CONFIRMED actions
- No financial data in preferences — only entity references (names, IDs)
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List, Dict, Any
import json
import logging

from ..llm import LLMRouter, TaskComplexity, LLMMessage

logger = logging.getLogger("unified_agent.session_manager")


# ============================================================================
# DATA CLASSES
# ============================================================================

@dataclass
class StructuredState:
    """Layer 2: Authoritative session state. Updated by BACKEND only, never LLM."""
    active_customer_id: Optional[str] = None
    active_customer_name: Optional[str] = None
    active_vendor_id: Optional[str] = None
    active_vendor_name: Optional[str] = None
    active_invoice_id: Optional[str] = None
    active_invoice_number: Optional[str] = None
    active_bill_id: Optional[str] = None
    active_bill_number: Optional[str] = None
    active_items: List[Dict] = field(default_factory=list)
    current_period: Optional[str] = None
    last_action_type: Optional[str] = None
    last_action_status: Optional[str] = None
    last_action_result: Optional[Dict] = None
    pending_action_id: Optional[str] = None
    fsm_state: str = "IDLE"

    def to_context_string(self) -> str:
        """Convert to minimal injection string for LLM context."""
        parts = []
        if self.active_customer_name:
            parts.append(f"Customer aktif: {self.active_customer_name} ({self.active_customer_id})")
        if self.active_vendor_name:
            parts.append(f"Vendor aktif: {self.active_vendor_name} ({self.active_vendor_id})")
        if self.active_invoice_number:
            parts.append(f"Invoice aktif: {self.active_invoice_number} ({self.active_invoice_id})")
        if self.active_bill_number:
            parts.append(f"Bill aktif: {self.active_bill_number} ({self.active_bill_id})")
        if self.active_items:
            items_list = []
            for i in self.active_items:
                desc = i.get('description') or i.get('name', '?')
                qty = i.get('quantity') or i.get('qty', '?')
                price = i.get('unit_price') or i.get('price', '?')
                items_list.append(f"{desc} ({qty} x Rp {price})")
            items_str = ", ".join(items_list)
            parts.append(f"Items: {items_str}")
        if self.last_action_type and self.last_action_status:
            parts.append(f"Last action: {self.last_action_type} → {self.last_action_status}")
            if self.last_action_result:
                r = self.last_action_result
                if r.get("invoice_number"):
                    parts.append(f"  Created: {r['invoice_number']} (Rp {r.get('total', '?')})")
        if self.pending_action_id:
            parts.append(f"Pending confirmation: {self.pending_action_id}")
        if self.fsm_state and self.fsm_state != "IDLE":
            parts.append(f"FSM state: {self.fsm_state}")
        
        if not parts:
            return ""
        return "## KONTEKS SESI AKTIF\n" + "\n".join(parts)


# ============================================================================
# SESSION MANAGER
# ============================================================================

class SessionManager:
    """Manages 4-layer memory for a chat session."""
    
    def __init__(self, db_pool, tenant_id: str, user_id: str):
        self.db = db_pool
        self.tenant_id = tenant_id
        self.user_id = user_id
    
    # ========================================================================
    # SESSION LIFECYCLE
    # ========================================================================
    
    async def get_or_create_session(self, session_id: Optional[str] = None) -> str:
        """Get existing session or create new one."""
        if session_id:
            # Verify session exists and belongs to this tenant/user
            row = await self.db.fetchrow(
                "SELECT id FROM chat_sessions WHERE id = $1::uuid AND tenant_id = $2",
                session_id, self.tenant_id
            )
            if row:
                return str(row["id"])
        
        # Create new session
        row = await self.db.fetchrow(
            "INSERT INTO chat_sessions (tenant_id, user_id) VALUES ($1, $2::uuid) RETURNING id",
            self.tenant_id, self.user_id
        )
        session_id = str(row["id"])
        
        # Initialize empty structured state
        await self.db.execute(
            "INSERT INTO chat_session_state (session_id, tenant_id) VALUES ($1::uuid, $2)",
            session_id, self.tenant_id
        )
        
        logger.info(f"[SESSION] Created new session {session_id[:8]} for tenant={self.tenant_id}")
        return session_id
    
    # ========================================================================
    # LAYER 1: WORKING WINDOW
    # ========================================================================
    
    async def store_message(
        self, 
        session_id: str, 
        role: str, 
        content: str,
        tool_calls: Optional[Dict] = None, 
        tool_call_id: Optional[str] = None,
        message_type: str = "TEXT", 
        token_count: Optional[int] = None
    ):
        """Store a message in chat history."""
        await self.db.execute("""
            INSERT INTO chat_messages (
                session_id, tenant_id, role, content, 
                tool_calls, tool_call_id, message_type, token_count
            )
            VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8)
        """, session_id, self.tenant_id, role, content,
             json.dumps(tool_calls) if tool_calls else None,
             tool_call_id, message_type, token_count)
        
        # Update session timestamp
        await self.db.execute(
            "UPDATE chat_sessions SET updated_at = now() WHERE id = $1::uuid",
            session_id
        )
    
    async def get_working_window(self, session_id: str, max_turns: int = 8) -> List[Dict]:
        """Layer 1: Get last N messages (verbatim) for LLM context."""
        rows = await self.db.fetch("""
            SELECT role, content, tool_calls, tool_call_id, message_type, created_at
            FROM chat_messages
            WHERE session_id = $1::uuid AND tenant_id = $2
            ORDER BY created_at DESC
            LIMIT $3
        """, session_id, self.tenant_id, max_turns * 2)  # *2 to account for tool messages
        
        # Reverse to chronological order
        messages = []
        for row in reversed(rows):
            msg = {"role": row["role"], "content": row["content"]}
            if row["tool_calls"]:
                msg["tool_calls"] = json.loads(row["tool_calls"])
            if row["tool_call_id"]:
                msg["tool_call_id"] = row["tool_call_id"]
            messages.append(msg)
        
        return messages
    
    # ========================================================================
    # LAYER 2: STRUCTURED STATE
    # ========================================================================
    
    async def get_state(self, session_id: str) -> StructuredState:
        """Layer 2: Get authoritative session state."""
        row = await self.db.fetchrow(
            "SELECT * FROM chat_session_state WHERE session_id = $1::uuid AND tenant_id = $2",
            session_id, self.tenant_id
        )
        if not row:
            return StructuredState()
        
        return StructuredState(
            active_customer_id=str(row["active_customer_id"]) if row["active_customer_id"] else None,
            active_customer_name=row["active_customer_name"],
            active_vendor_id=str(row["active_vendor_id"]) if row["active_vendor_id"] else None,
            active_vendor_name=row["active_vendor_name"],
            active_invoice_id=str(row["active_invoice_id"]) if row["active_invoice_id"] else None,
            active_invoice_number=row["active_invoice_number"],
            active_bill_id=str(row["active_bill_id"]) if row["active_bill_id"] else None,
            active_bill_number=row["active_bill_number"],
            active_items=json.loads(row["active_items"]) if isinstance(row["active_items"], str) and row["active_items"] else (row["active_items"] or []),
            current_period=row["current_period"],
            last_action_type=row["last_action_type"],
            last_action_status=row["last_action_status"],
            last_action_result=json.loads(row["last_action_result"]) if isinstance(row["last_action_result"], str) and row["last_action_result"] else row["last_action_result"],
            pending_action_id=str(row["pending_action_id"]) if row["pending_action_id"] else None,
            fsm_state=row["fsm_state"] or "IDLE",
        )
    
    async def update_state(self, session_id: str, **updates):
        """Update specific fields in structured state. Backend calls this, NOT LLM."""
        if not updates:
            return
            
        set_clauses = []
        values = [session_id, self.tenant_id]
        idx = 3
        
        for key, value in updates.items():
            set_clauses.append(f"{key} = ${idx}")
            if isinstance(value, (dict, list)):
                values.append(json.dumps(value))
            else:
                values.append(value)
            idx += 1
        
        set_clauses.append("updated_at = now()")
        
        await self.db.execute(f"""
            UPDATE chat_session_state 
            SET {', '.join(set_clauses)}
            WHERE session_id = $1::uuid AND tenant_id = $2
        """, *values)
    
    async def update_state_from_search(self, session_id: str, tool_name: str, result: Dict):
        """Auto-update structured state when a search tool returns results.
        
        Called by tool_executor AFTER each tool call.
        This is how Layer 2 stays in sync — deterministic, not LLM-driven.
        """
        if not result.get("success"):
            return
        
        data = result.get("data", {})
        
        if tool_name == "search_customers" and data:
            # Set first result as active customer (best match from search)
            customers = data.get("customers", data) if isinstance(data, dict) else data
            items = customers if isinstance(customers, list) else [customers]
            if items:
                await self.update_state(
                    session_id,
                    active_customer_id=items[0].get("id"),
                    active_customer_name=items[0].get("name") or items[0].get("nama")
                )
        
        elif tool_name == "search_vendors" and data:
            items = data if isinstance(data, list) else [data]
            if items:
                await self.update_state(
                    session_id,
                    active_vendor_id=items[0].get("id"),
                    active_vendor_name=items[0].get("name") or items[0].get("nama")
                )
        
        elif tool_name == "search_items" and data:
            items = data if isinstance(data, list) else [data]
            if items:
                await self.update_state(
                    session_id,
                    active_items=[{
                        "id": item.get("id"),
                        "name": item.get("name") or item.get("nama"),
                        "sku": item.get("sku"),
                        "price": item.get("selling_price") or item.get("harga_jual"),
                        "buying_price": item.get("buying_price") or item.get("harga_beli"),
                    } for item in items[:5]]
                )
        
        elif tool_name in ("get_invoice_detail", "get_invoices") and data:
            items = data if isinstance(data, list) else [data]
            if items:
                inv = items[0]
                await self.update_state(
                    session_id,
                    active_invoice_id=inv.get("id"),
                    active_invoice_number=inv.get("invoice_number"),
                )
        
        elif tool_name in ("get_bill_detail", "get_bills") and data:
            items = data if isinstance(data, list) else [data]
            if items:
                bill = items[0]
                await self.update_state(
                    session_id,
                    active_bill_id=bill.get("id"),
                    active_bill_number=bill.get("bill_number"),
                )
    
    async def update_state_from_action(
        self, 
        session_id: str, 
        action_type: str, 
        status: str, 
        result: Optional[Dict] = None
    ):
        """Auto-update structured state after propose/confirm/reject.
        
        Called by orchestrator AFTER each action lifecycle event.
        """
        updates = {
            "last_action_type": action_type,
            "last_action_status": status,
        }
        
        if result:
            updates["last_action_result"] = result
            # Extract created document info
            if result.get("invoice_number"):
                updates["active_invoice_number"] = result["invoice_number"]
                updates["active_invoice_id"] = result.get("id")
            if result.get("bill_number"):
                updates["active_bill_number"] = result["bill_number"]
                updates["active_bill_id"] = result.get("id")
        
        if status == "proposed" and result and result.get("pending_action_id"):
            updates["pending_action_id"] = result["pending_action_id"]
            updates["fsm_state"] = "AWAITING_CONFIRMATION"
        elif status in ("confirmed", "rejected"):
            updates["pending_action_id"] = None
            updates["fsm_state"] = "IDLE"
        
        await self.update_state(session_id, **updates)
    

    async def transition_fsm(self, session_id: str, target_state: str) -> str:
        """Transition FSM state with validation.
        
        Returns the new state (may be unchanged if transition is invalid).
        """
        from .fsm import FSMEngine, FSMState
        
        state = await self.get_state(session_id)
        current = FSMState(state.fsm_state or "IDLE")
        
        fsm = FSMEngine(current_state=current)
        new_state = fsm.transition_safe(FSMState(target_state))
        
        if new_state.value != current.value:
            await self.update_state(session_id, fsm_state=new_state.value)
        
        return new_state.value

    # ========================================================================
    # LAYER 3: EVENT LOG
    # ========================================================================
    
    async def log_event(
        self, 
        session_id: str, 
        event_type: str,
        action_type: Optional[str] = None, 
        payload: Optional[Dict] = None, 
        result: Optional[Dict] = None
    ):
        """Layer 3: Log structured event."""
        await self.db.execute("""
            INSERT INTO chat_events (session_id, tenant_id, event_type, action_type, payload, result)
            VALUES ($1::uuid, $2, $3, $4, $5, $6)
        """, session_id, self.tenant_id, event_type, action_type,
             json.dumps(payload) if payload else None,
             json.dumps(result) if result else None)
    
    async def get_recent_events(self, session_id: str, limit: int = 10) -> List[Dict]:
        """Get recent events for context (used by LLM if needed via tool)."""
        rows = await self.db.fetch("""
            SELECT event_type, action_type, result, created_at
            FROM chat_events
            WHERE session_id = $1::uuid AND tenant_id = $2
            ORDER BY created_at DESC
            LIMIT $3
        """, session_id, self.tenant_id, limit)
        
        return [
            {
                "event_type": row["event_type"],
                "action_type": row["action_type"],
                "result_summary": self._summarize_event_result(row["result"]),
                "timestamp": row["created_at"].isoformat()
            }
            for row in rows
        ]
    
    def _summarize_event_result(self, result_json: Any) -> str:
        """Compact event result for context injection."""
        if not result_json:
            return ""
        result = json.loads(result_json) if isinstance(result_json, str) else result_json
        parts = []
        if result.get("invoice_number"):
            parts.append(f"Invoice {result['invoice_number']}")
        if result.get("bill_number"):
            parts.append(f"Bill {result['bill_number']}")
        if result.get("total"):
            parts.append(f"Rp {result['total']:,.0f}")
        if result.get("status"):
            parts.append(result["status"])
        return " | ".join(parts) if parts else "ok"
    
    # ========================================================================
    # LAYER 4: CONVERSATIONAL SUMMARY (Phase C — LLM-powered)
    # ========================================================================
    
    async def get_summary(self, session_id: str) -> str:
        """Get stored summary."""
        row = await self.db.fetchrow(
            "SELECT summary FROM chat_sessions WHERE id = $1::uuid",
            session_id
        )
        return row["summary"] if row and row["summary"] else ""

    async def generate_summary(self, session_id: str, messages: list) -> str:
        """Layer 4: Generate semantic summary using LLM (Phase C).

        Called when working window is reduced (token budget pressure).
        Uses gpt-4o-mini for cheap, fast summarization.
        Falls back to rule-based extraction if LLM call fails.

        Rules:
        - Max 5 sentences
        - Focus: user intent, decisions made, actions taken, entity names
        - NEVER include: amounts, saldo, journal details (those are in DB)
        - Include: entity names (customers, vendors mentioned), preferences
        """
        if not messages:
            return ""

        # Build conversation text from messages being summarized
        conversation_parts = []
        for msg in messages:
            role = msg.get("role", "")
            content_text = msg.get("content", "")
            if not content_text or role == "system":
                continue
            conversation_parts.append(f"{role}: {content_text[:200]}")

        if not conversation_parts:
            return ""

        conversation_text = "\n".join(conversation_parts)

        # Get events for accuracy
        events = await self.get_recent_events(session_id, limit=10)
        events_context = ""
        if events:
            events_context = "\n".join(
                f"[{e['event_type']}] {e.get('action_type', '')} -> {e['result_summary']}"
                for e in events
            )

        # Build LLM prompt
        summary_system = (
            "Kamu meringkas percakapan akuntansi. Max 5 kalimat.\n"
            "SERTAKAN: siapa customer/vendor yang dibahas, keputusan yang diambil, preferensi user.\n"
            "JANGAN SERTAKAN: angka/nominal/saldo/jurnal (itu ada di database).\n"
            "Format: paragraf singkat, bukan bullet points."
        )

        user_content = (
            f"Events:\n{events_context}\n\nPercakapan:\n{conversation_text}"
            if events_context
            else f"Percakapan:\n{conversation_text}"
        )

        # Call LLM for semantic summary
        try:
            router = self._get_llm_router()
            client, model = router.get_client_and_model(TaskComplexity.SIMPLE_READ)
            response = await client.chat(
                messages=[
                    LLMMessage(role="system", content=summary_system),
                    LLMMessage(role="user", content=user_content),
                ],
                tools=[],
                model=model,
                temperature=0.3,
                max_tokens=300,
            )
            summary = response.content or ""
        except Exception as e:
            logger.warning("[SUMMARY] LLM summary failed, falling back to rule-based: %s", e)
            summary = self._rule_based_summary(messages)

        # Merge with existing summary (rolling window)
        existing = await self.get_summary(session_id)
        if existing and summary:
            combined = f"{existing}\n{summary}"
            if len(combined) > 500:
                # Newer summary replaces old when combined is too long
                combined = summary
            summary = combined

        # Store in DB
        await self.db.execute(
            "UPDATE chat_sessions SET summary = $1 WHERE id = $2::uuid",
            summary, session_id
        )

        logger.info("[SUMMARY] Smart summary for session %s: %d chars", session_id[:8], len(summary))
        return summary

    def _get_llm_router(self) -> LLMRouter:
        """Get or create cached LLMRouter instance."""
        if not hasattr(self, "_llm_router"):
            self._llm_router = LLMRouter.from_env()
        return self._llm_router

    def _rule_based_summary(self, messages: list) -> str:
        """Fallback: rule-based summary extraction (original Phase B logic)."""
        parts = []
        for msg in messages:
            role = msg.get("role", "")
            content_text = msg.get("content", "")
            if not content_text or role == "system":
                continue

            if role == "user":
                parts.append("- User: " + content_text[:150])
            elif role == "assistant":
                if content_text.startswith("[Usulan"):
                    parts.append("- Asisten: " + content_text)
                else:
                    parts.append("- Asisten: " + content_text[:100])

        if not parts:
            return ""

        # Keep last 8 exchanges max
        return chr(10).join(parts[-8:])




    # ========================================================================
    # CROSS-SESSION SEARCH (Phase C)
    # ========================================================================

    async def search_chat_history(self, query: str, days_back: int = 7) -> list:
        """Search across past sessions. Events first (structured, fast), messages fallback.

        Called by tool_executor when agent uses search_chat_history tool.
        READ-ONLY, no data mutation (Iron Law 10: AI Safety).
        All queries filtered by tenant_id (Iron Law 24: Tenant Isolation).
        """
        # First: search events (structured, fast, accurate)
        events = await self.db.fetch("""
            SELECT ce.event_type, ce.action_type, ce.result::text, ce.created_at,
                   cs.id as session_id
            FROM chat_events ce
            JOIN chat_sessions cs ON cs.id = ce.session_id
            WHERE ce.tenant_id = $1
              AND ce.created_at > now() - make_interval(days => $2)
              AND (ce.action_type ILIKE $3
                   OR ce.result::text ILIKE $3
                   OR ce.payload::text ILIKE $3)
            ORDER BY ce.created_at DESC
            LIMIT 10
        """, self.tenant_id, days_back, f"%{query}%")

        if events:
            results = []
            for e in events:
                result_data = None
                if e["result"]:
                    try:
                        result_data = json.loads(e["result"]) if isinstance(e["result"], str) else e["result"]
                    except Exception:
                        result_data = None
                results.append({
                    "source": "event",
                    "session_id": str(e["session_id"]),
                    "event_type": e["event_type"],
                    "action_type": e["action_type"],
                    "result": result_data,
                    "date": e["created_at"].isoformat()
                })
            return results

        # Fallback: search messages
        messages = await self.db.fetch("""
            SELECT cm.content, cm.message_type, cm.created_at, cs.id as session_id
            FROM chat_messages cm
            JOIN chat_sessions cs ON cs.id = cm.session_id
            WHERE cm.tenant_id = $1
              AND cm.created_at > now() - make_interval(days => $2)
              AND cm.content ILIKE $3
              AND cm.role IN ('user', 'assistant')
            ORDER BY cm.created_at DESC
            LIMIT 10
        """, self.tenant_id, days_back, f"%{query}%")

        return [
            {
                "source": "message",
                "session_id": str(m["session_id"]),
                "content_preview": m["content"][:200],
                "type": m["message_type"],
                "date": m["created_at"].isoformat()
            }
            for m in messages
        ]


    # ========================================================================
    # USER PREFERENCES (Cross-Session, Auto-Learning — Phase C)
    # ========================================================================

    async def get_preferences(self) -> dict:
        """Get user preferences for context injection.

        Cross-session: keyed by (tenant_id, user_id), not session_id.
        Preferences persist across all sessions for this user+tenant.
        """
        row = await self.db.fetchrow(
            "SELECT * FROM user_preferences WHERE tenant_id = $1 AND user_id = $2::uuid",
            self.tenant_id, self.user_id
        )
        if not row:
            return {}

        return {
            "preferred_language": row["preferred_language"],
            "formality": row["formality"],
            "default_payment_account_id": str(row["default_payment_account_id"]) if row["default_payment_account_id"] else None,
            "default_payment_account_name": row["default_payment_account_name"],
            "frequent_customers": json.loads(row["frequent_customers"]) if isinstance(row["frequent_customers"], str) else (row["frequent_customers"] or []),
            "frequent_vendors": json.loads(row["frequent_vendors"]) if isinstance(row["frequent_vendors"], str) else (row["frequent_vendors"] or []),
            "frequent_items": json.loads(row["frequent_items"]) if isinstance(row["frequent_items"], str) else (row["frequent_items"] or []),
        }

    async def get_preference_context(self) -> str:
        """Generate system prompt injection from user preferences.

        READ-ONLY for LLM — this is injected into the system prompt
        so the LLM knows the user's frequently used entities.
        """
        prefs = await self.get_preferences()
        if not prefs:
            return ""

        parts = ["## PREFERENSI USER"]

        freq_customers = prefs.get("frequent_customers", [])
        if freq_customers:
            top3 = freq_customers[:3]
            names = ", ".join(c.get("name", "?") for c in top3)
            parts.append(f"Customer sering dipakai: {names}")

        freq_vendors = prefs.get("frequent_vendors", [])
        if freq_vendors:
            top3 = freq_vendors[:3]
            names = ", ".join(v.get("name", "?") for v in top3)
            parts.append(f"Vendor sering dipakai: {names}")

        freq_items = prefs.get("frequent_items", [])
        if freq_items:
            top3 = freq_items[:3]
            names = ", ".join(i.get("name", "?") for i in top3)
            parts.append(f"Item sering dipakai: {names}")

        if prefs.get("default_payment_account_name"):
            parts.append(f"Default pembayaran dari: {prefs['default_payment_account_name']}")

        if len(parts) <= 1:
            return ""  # Only header, no actual preferences

        return "\n".join(parts)

    async def update_preferences_from_action(self, action_type: str, payload: dict):
        """Learn user preferences from confirmed actions.

        Called by StateUpdateHooks.after_confirm() — NOT by LLM.
        Tracks most frequently used entities (names + IDs only, no amounts).

        CRITICAL: All queries filter by tenant_id (Iron Law 24).
        """
        # Ensure preferences row exists
        await self.db.execute("""
            INSERT INTO user_preferences (tenant_id, user_id)
            VALUES ($1, $2::uuid)
            ON CONFLICT (tenant_id, user_id) DO NOTHING
        """, self.tenant_id, self.user_id)

        # Track customer usage
        if payload.get("customer_id") and payload.get("customer_name"):
            await self._increment_frequent(
                "frequent_customers",
                {"id": payload["customer_id"], "name": payload["customer_name"]}
            )

        # Track vendor usage
        if payload.get("vendor_id") and payload.get("vendor_name"):
            await self._increment_frequent(
                "frequent_vendors",
                {"id": payload["vendor_id"], "name": payload["vendor_name"]}
            )

        # Track item usage
        items = payload.get("items", [])
        for item in items:
            if item.get("item_id") and (item.get("description") or item.get("name")):
                await self._increment_frequent(
                    "frequent_items",
                    {"id": item["item_id"], "name": item.get("description") or item.get("name")}
                )

        # Track payment account preference
        if payload.get("payment_account_id"):
            await self.db.execute("""
                UPDATE user_preferences
                SET default_payment_account_id = $1::uuid,
                    default_payment_account_name = $2,
                    updated_at = now()
                WHERE tenant_id = $3 AND user_id = $4::uuid
            """, payload["payment_account_id"],
                 payload.get("payment_account_name", ""),
                 self.tenant_id, self.user_id)

    async def _increment_frequent(self, field_name: str, entity: dict):
        """Increment usage count for a frequent entity. Keep top 10.

        NOTE: field_name is always one of the hardcoded constants
        ("frequent_customers", "frequent_vendors", "frequent_items").
        These are NOT from user input, so f-string SQL is safe here.
        """
        # Validate field_name against whitelist
        allowed_fields = ("frequent_customers", "frequent_vendors", "frequent_items")
        if field_name not in allowed_fields:
            logger.error(f"[PREFS] Invalid field_name: {field_name}")
            return

        row = await self.db.fetchrow(
            f"SELECT {field_name} FROM user_preferences WHERE tenant_id = $1 AND user_id = $2::uuid",
            self.tenant_id, self.user_id
        )

        if not row:
            return

        current = row[field_name]
        if isinstance(current, str):
            current = json.loads(current)
        if not current:
            current = []

        # Find existing or create new
        found = False
        for item in current:
            if item.get("id") == entity["id"]:
                item["count"] = item.get("count", 1) + 1
                item["name"] = entity["name"]  # Update name in case it changed
                found = True
                break

        if not found:
            current.append({"id": entity["id"], "name": entity["name"], "count": 1})

        # Sort by count desc, keep top 10
        current.sort(key=lambda x: x.get("count", 0), reverse=True)
        current = current[:10]

        await self.db.execute(
            f"UPDATE user_preferences SET {field_name} = $1, updated_at = now() WHERE tenant_id = $2 AND user_id = $3::uuid",
            json.dumps(current), self.tenant_id, self.user_id
        )

    # ========================================================================
    # CONTEXT ASSEMBLY (Token Budget Manager)
    # ========================================================================
    
    async def assemble_context(
        self, 
        session_id: str, 
        system_prompt: str,
        user_message: str, 
        token_budget: int = 6000
    ) -> List[Dict]:
        """Assemble complete context for LLM call.
        
        Priority order (high to low):
        1. System prompt (MUST include, ~1750 tokens)
        2. User's current message (MUST include)
        3. Layer 2: Structured state (small, ~100-200 tokens)
        4. Layer 1: Working window (up to 6-8 turns)
        5. Layer 3: Recent events summary (compact, ~50-100 tokens)
        6. Layer 4: Conversational summary (if older turns exist, ~100 tokens)
        7. User preferences (cross-session, ~50-100 tokens)
        
        Token Budget Manager ensures total stays within budget.
        """
        messages = []
        
        # === System prompt (always included) ===
        system_content = system_prompt
        
        # === Layer 2: Inject structured state ===
        state = await self.get_state(session_id)
        state_context = state.to_context_string()
        if state_context:
            system_content += f"\n\n{state_context}"
        
        # === Layer 3: Recent events (compact) ===
        events = await self.get_recent_events(session_id, limit=5)
        if events:
            events_str = "\n## RIWAYAT AKSI TERAKHIR\n"
            for e in reversed(events):  # chronological
                events_str += f"- [{e['event_type']}] {e.get('action_type', '')} → {e['result_summary']}\n"
            system_content += events_str
        
        # === Layer 4: Summary (if exists) ===
        summary = await self.get_summary(session_id)
        if summary:
            system_content += f"\n\n## RINGKASAN PERCAKAPAN SEBELUMNYA\n{summary}"

        # === User Preferences (cross-session) ===
        pref_context = await self.get_preference_context()
        if pref_context:
            system_content += f"\n\n{pref_context}"

        # Estimate system tokens (~4 chars per token for mixed ID/EN)
        system_tokens = len(system_content) // 4
        messages.append({"role": "system", "content": system_content})
        
        # === User message tokens ===
        user_tokens = len(user_message) // 4
        
        # === Layer 1: Working window (fill remaining budget) ===
        remaining_tokens = token_budget - system_tokens - user_tokens - 500  # 500 buffer for response
        
        window = []
        if remaining_tokens > 0:
            # Start with 8 turns, reduce if needed
            for max_turns in [8, 6, 4, 2]:
                window = await self.get_working_window(session_id, max_turns=max_turns)
                window_tokens = sum(len(m.get("content", "")) // 4 for m in window)
                if window_tokens <= remaining_tokens:
                    break
            else:
                window = []  # No room for history
            
            messages.extend(window)
        
        # === Current user message ===
        messages.append({"role": "user", "content": user_message})
        
        # Log assembled context stats
        total_tokens = sum(len(m.get("content", "")) // 4 for m in messages)
        logger.info(
            f"[CONTEXT] session={session_id[:8]} | "
            f"L1={len(window)} turns | "
            f"L2={'yes' if state_context else 'no'} | "
            f"L3={len(events)} events | "
            f"L4={'yes' if summary else 'no'} | "
            f"prefs={'yes' if pref_context else 'no'} | "
            f"total≈{total_tokens} tokens"
        )
        
        return messages


# ============================================================================
# STATE UPDATE HOOKS
# ============================================================================

class StateUpdateHooks:
    """Hooks that fire after tool calls and actions to keep Layer 2 in sync.
    
    CRITICAL: These are DETERMINISTIC backend logic.
    LLM NEVER updates structured state directly.
    State changes are DERIVED from tool results and action outcomes.
    """
    
    @staticmethod
    async def after_tool_call(
        session_manager: SessionManager, 
        session_id: str,
        tool_name: str, 
        tool_args: Dict, 
        tool_result: Dict
    ):
        """Called by tool_executor after EVERY tool call."""
        # Update Layer 2 structured state
        await session_manager.update_state_from_search(session_id, tool_name, tool_result)
        
        # Log Layer 3 event
        event_type = "search" if tool_name.startswith(("search_", "get_")) else "tool"
        await session_manager.log_event(
            session_id,
            event_type=event_type,
            payload={"tool": tool_name, "args": tool_args},
            result={"success": tool_result.get("success"), "count": len(tool_result.get("data", []))}
        )
    
    @staticmethod
    async def after_propose(
        session_manager: SessionManager, 
        session_id: str,
        action_type: str, 
        payload: Dict, 
        result: Dict
    ):
        """Called by orchestrator after propose_action."""
        await session_manager.update_state_from_action(session_id, action_type, "proposed", result)
        await session_manager.log_event(session_id, "propose", action_type, payload, result)
    
    @staticmethod
    async def after_confirm(
        session_manager: SessionManager, 
        session_id: str,
        action_type: str, 
        result: Dict
    ):
        """Called by orchestrator/chat.py after user confirms."""
        await session_manager.update_state_from_action(session_id, action_type, "confirmed", result)
        await session_manager.log_event(session_id, "confirm", action_type, result=result)

        # Phase C: Update user preferences from confirmed actions
        try:
            payload = result.get("payload", result)  # Result may contain payload
            await session_manager.update_preferences_from_action(action_type, payload)
        except Exception as e:
            logger.warning(f"[PREFS] Failed to update preferences: {e}")
    
    @staticmethod
    async def after_reject(
        session_manager: SessionManager, 
        session_id: str,
        action_type: str
    ):
        """Called by orchestrator/chat.py after user rejects."""
        await session_manager.update_state_from_action(session_id, action_type, "rejected")
        await session_manager.log_event(session_id, "reject", action_type)
