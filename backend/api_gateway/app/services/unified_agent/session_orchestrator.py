"""
Session-aware wrapper for UnifiedAgent

Phase B: Full 4-layer memory with session tools and auto-summarization.
- Wraps existing UnifiedAgent
- Adds 4-layer memory
- Passes session-aware ToolExecutor to agent loop
- Maintains backward compatibility
"""
import asyncio
import logging
import time
from typing import Optional, List, Dict

from .orchestrator import UnifiedAgent, AgentResponse, TenantContext
from .model_router import ModelRouter
from .tool_executor import ToolExecutor
from .session_manager import SessionManager, StateUpdateHooks
from .db_utils import get_session_db_pool
from .fsm import FSMState

logger = logging.getLogger("unified_agent.session_orchestrator")


class SessionAwareAgent:
    """Wrapper that adds session management to UnifiedAgent."""

    def __init__(self):
        self.agent = UnifiedAgent()
        self.db_pool = None

    async def process_message(
        self,
        user_text: str,
        context: TenantContext,
        conversation_history: Optional[List[Dict[str, str]]] = None,
        session_id: Optional[str] = None,
        image_content: Optional[list] = None,
        event_callback=None,
    ) -> Dict:
        """Process message with session management.

        Returns AgentResponse dict with added session_id field.
        """
        # Initialize db pool if needed
        if not self.db_pool:
            self.db_pool = await get_session_db_pool()

        # Create session manager
        session_manager = SessionManager(
            db_pool=self.db_pool,
            tenant_id=context.tenant_id,
            user_id=context.user_id
        )

        # Get or create session
        session_id = await session_manager.get_or_create_session(session_id)
        # FSM: Start planning (IDLE -> PLANNING)
        await session_manager.transition_fsm(session_id, FSMState.PLANNING.value)

        # Store user message
        await session_manager.store_message(session_id, "user", user_text)

        # Get conversation history from session (replaces gRPC history)
        # Uses full 4-layer context assembly with token budget management
        if not conversation_history:
            conversation_history = await self._assemble_session_context(
                session_manager, session_id, user_text
            )

        # === Create session-aware ToolExecutor (Phase B) ===
        # This allows get_session_events tool to work inside the agent loop
        tool_executor = ToolExecutor(
            context=context,
            session_manager=session_manager,
            session_id=session_id,
            user_text=user_text,  # Pass user text for file_ref auto-injection
        )

        # Get structured state for model routing
        state = await session_manager.get_state(session_id)

        # Model routing with session awareness (M2 — informational logging)
        _state_dict = state.__dict__ if hasattr(state, "__dict__") else {}
        _prev_proposed = (
            state.last_action_status == "proposed"
            if hasattr(state, "last_action_status") and state.last_action_status
            else False
        )
        model_choice = ModelRouter.route(
            user_message=user_text,
            session_state=_state_dict,
            conversation_depth=len(conversation_history) // 2 if conversation_history else 0,
            previous_turn_proposed=_prev_proposed,
        )
        logger.info(f"[MODEL] tier={model_choice.tier} reason='{model_choice.reason}'")

        # Call original agent with enriched context + session-aware executor
        agent_response = await self.agent.process_message(
            user_text=user_text,
            context=context,
            conversation_history=conversation_history,
            tool_executor=tool_executor,
            image_content=image_content,
            event_callback=event_callback,
        )

        # Normalize response (handle both dict and AgentResponse object)
        if isinstance(agent_response, dict):
            content = agent_response.get("content", "")
            tool_calls_made = agent_response.get("tool_calls_made", [])
            message_type = agent_response.get("message_type", "TEXT")
            pending_action_id = agent_response.get("pending_action_id")
            preview = agent_response.get("preview")
            expires_at = agent_response.get("expires_at")
            errors = agent_response.get("errors")
            iterations = agent_response.get("iterations", 0)
            model_used = agent_response.get("model_used")
            total_latency_ms = agent_response.get("total_latency_ms", 0)
            thinking_stages = agent_response.get("thinking_stages", [])
        else:
            content = agent_response.content or ""
            tool_calls_made = agent_response.tool_calls_made or []
            message_type = agent_response.message_type or "TEXT"
            pending_action_id = agent_response.pending_action_id
            preview = agent_response.preview
            expires_at = agent_response.expires_at
            errors = agent_response.errors
            iterations = agent_response.iterations or 0
            model_used = agent_response.model_used
            total_latency_ms = agent_response.total_latency_ms or 0
            thinking_stages = getattr(agent_response, "thinking_stages", []) or []

        # Extract token usage
        usage = {}
        if isinstance(agent_response, dict):
            usage = agent_response.get("usage", {})
        else:
            usage = getattr(agent_response, "usage", {}) or {}

        # FSM: Transition based on agent result
        if message_type == "ACTION_PREVIEW":
            # PLANNING -> AWAITING_CONFIRMATION
            await session_manager.transition_fsm(session_id, FSMState.AWAITING_CONFIRMATION.value)
        elif message_type in ("TEXT", "CLARIFICATION"):
            # PLANNING -> IDLE (no action needed, just answered)
            await session_manager.transition_fsm(session_id, FSMState.IDLE.value)
        elif message_type == "VALIDATION_ERROR":
            # PLANNING -> IDLE (validation failed, reset)
            await session_manager.transition_fsm(session_id, FSMState.IDLE.value)

        # Get final FSM state for response
        final_state = await session_manager.get_state(session_id)
        fsm_state = final_state.fsm_state or "IDLE"

        # === Store assistant response ===
        # Generate content summary for ACTION_PREVIEW (so it appears in chat history)
        content_to_store = content or ""
        if message_type == "ACTION_PREVIEW" and preview:
            preview_data = preview if isinstance(preview, dict) else {}
            payload = preview_data.get("payload", {})
            action_type = preview_data.get("action_type", "")

            if action_type == "CREATE_SALES_INVOICE":
                cust = payload.get("customer_name", "?")
                items_list = payload.get("items", [])
                if items_list:
                    item_desc = items_list[0].get("description", "?")
                    qty = items_list[0].get("quantity", 0)
                    content_to_store = f"[Usulan faktur untuk {cust}: {item_desc} x {qty} pcs]"
                else:
                    content_to_store = f"[Usulan faktur untuk {cust}]"
            else:
                content_to_store = f"[Usulan {action_type}]"

        await session_manager.store_message(
            session_id,
            "assistant",
            content_to_store,
            message_type=message_type,
            token_count=usage.get("total_tokens") or None,
        )

        # === Phase B: Auto-summarize if conversation is getting long ===
        try:
            all_messages = await session_manager.get_working_window(session_id, max_turns=20)
            if len(all_messages) > 12:  # More than ~6 turns
                # Summarize older messages (beyond the recent 8)
                older = all_messages[:-8] if len(all_messages) > 8 else []
                if older:
                    await session_manager.generate_summary(session_id, older)
        except Exception as e:
            logger.warning(f"[SUMMARY] Auto-summarize failed: {e}")

        # === Phase B: Update Layer 2 + Layer 3 from tool calls ===
        if tool_calls_made:
            for tool_call in tool_calls_made:
                tool_name = tool_call.get("name")
                tool_success = tool_call.get("success", False)
                tool_data = tool_call.get("data")
                tool_args = tool_call.get("args", {})

                # Layer 2 + Layer 3 updates via hooks
                if tool_success and tool_data is not None:
                    tool_result = {"success": True, "data": tool_data}

                    # Generic hook: updates Layer 2 from search results + logs Layer 3 event
                    await StateUpdateHooks.after_tool_call(
                        session_manager, session_id, tool_name, tool_args, tool_result
                    )

                # propose_action: use dedicated hook (after_propose handles
                # state_from_action + Layer 3 event log, which differs from
                # the generic after_tool_call path)
                if tool_name == "propose_action":
                    action_type = tool_args.get("action_type", "")
                    result = {"success": tool_success, "data": tool_data} if tool_success else {"success": False}
                    await StateUpdateHooks.after_propose(
                        session_manager, session_id, action_type, tool_args, result
                    )

        # Convert to dict with session_id
        return {
            "message_type": message_type,
            "content": content,
            "pending_action_id": pending_action_id,
            "preview": preview,
            "expires_at": expires_at,
            "errors": errors,
            "iterations": iterations,
            "tool_calls_made": tool_calls_made,
            "model_used": model_used,
            "total_latency_ms": total_latency_ms,
            "trace_id": agent_response.trace_id,
            "fsm_state": fsm_state,
            "session_id": session_id,
            "thinking_stages": thinking_stages,
            "usage": usage,
        }

    async def _assemble_session_context(
        self,
        session_mgr: SessionManager,
        session_id: str,
        user_text: str,
        token_budget: int = 6000,
    ) -> list:
        """Build enriched conversation_history with all 4 layers + preferences.

        L2/L3/L4/preferences fetched in PARALLEL (asyncio.gather) for speed.
        L1 working window fetched after (depends on token budget from above).

        Returns messages list: context system message + L1 working window.
        """
        ctx_start = time.time()
        parts = []

        # === Parallel fetch: L2 + L3 + L4 + Preferences ===
        state_result, events_result, summary_result, pref_result = await asyncio.gather(
            session_mgr.get_state(session_id),
            session_mgr.get_recent_events(session_id, limit=5),
            session_mgr.get_summary(session_id),
            session_mgr.get_preference_context(),
            return_exceptions=True,
        )

        # Layer 2: Structured state
        state_context = ""
        if not isinstance(state_result, Exception):
            state_context = state_result.to_context_string()
            if state_context:
                parts.append(state_context)
        else:
            logger.warning("[CONTEXT] Failed to fetch L2 state: %s", state_result)

        # Layer 3: Recent events (compact)
        if not isinstance(events_result, Exception) and events_result:
            events_lines = []
            for e in reversed(events_result):
                events_lines.append(
                    f"- [{e['event_type']}] {e.get('action_type', '')} \u2192 {e['result_summary']}"
                )
            parts.append("## RIWAYAT AKSI TERAKHIR\n" + "\n".join(events_lines))
        elif isinstance(events_result, Exception):
            logger.warning("[CONTEXT] Failed to fetch L3 events: %s", events_result)

        # Layer 4: Conversational summary
        if not isinstance(summary_result, Exception) and summary_result:
            parts.append(f"## RINGKASAN PERCAKAPAN SEBELUMNYA\n{summary_result}")
        elif isinstance(summary_result, Exception):
            logger.warning("[CONTEXT] Failed to fetch L4 summary: %s", summary_result)

        # User Preferences (cross-session)
        if not isinstance(pref_result, Exception) and pref_result:
            parts.append(pref_result)
        elif isinstance(pref_result, Exception):
            logger.warning("[CONTEXT] Failed to fetch preferences: %s", pref_result)

        # Build the context system message
        messages = []
        if parts:
            context_block = "\n\n".join(parts)
            messages.append({"role": "system", "content": context_block})

        # === Layer 1: Working window with token budget ===
        # (Sequential — depends on token budget from L2/L3/L4 above)
        context_tokens = sum(len(m.get("content", "")) for m in messages) // 4
        user_tokens = len(user_text) // 4
        remaining = token_budget - context_tokens - user_tokens - 1750 - 500

        window = []
        if remaining > 0:
            for max_turns in [8, 6, 4, 2]:
                window = await session_mgr.get_working_window(session_id, max_turns=max_turns)
                window_tokens = sum(len(m.get("content", "")) // 4 for m in window)
                if window_tokens <= remaining:
                    break
            else:
                window = []

        messages.extend(window)

        ctx_ms = int((time.time() - ctx_start) * 1000)
        logger.info(
            "[CONTEXT] session=%s | L1=%d msgs | L2=%s | L3=%s | L4=%s | prefs=%s | budget=%d | ctx_ms=%d",
            session_id[:8],
            len(window),
            "yes" if state_context else "no",
            "yes" if any("RIWAYAT" in m.get("content", "") for m in messages) else "no",
            "yes" if any("RINGKASAN" in m.get("content", "") for m in messages) else "no",
            "yes" if any("PREFERENSI" in m.get("content", "") for m in messages) else "no",
            token_budget,
            ctx_ms,
        )

        return messages
