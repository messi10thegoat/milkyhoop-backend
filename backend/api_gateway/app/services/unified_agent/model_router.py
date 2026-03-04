"""
Model Router for MilkyHoop.

Rule-based model selection — no ML classifier needed.
Reads tool metadata flags + conversation signals to pick optimal model.

HARD CONSTRAINT: Financial operations ALWAYS use flagship model.

Consumed by: orchestrator.py (select model per turn).

Model IDs are aligned with what's registered in llm/llm_router.py:
  - OpenAI:  gpt-4o (flagship), gpt-4o-mini (reliable/cheap)
  - Claude:  claude-3-5-sonnet-20241022 (fallback flagship), claude-3-5-haiku-20241022 (fallback reliable)
    NOTE: Claude clients are stubs; fallback models will only work once ClaudeClient is implemented.
"""

import logging
import re
from dataclasses import dataclass
from typing import Optional

from .tool_metadata import ACTION_TYPE_METADATA

logger = logging.getLogger("unified_agent.model_router")


# ============================================================
# MODEL CHOICE
# ============================================================


@dataclass
class ModelChoice:
    """Result of model routing decision."""

    model_id: str
    provider: str  # "openai" | "claude" | "gemini"
    tier: str  # "flagship" | "reliable" | "cheap"
    reason: str
    max_tokens: int


# ============================================================
# MODEL TIER CONFIGURATION
# ============================================================


@dataclass(frozen=True)
class ModelTier:
    """Configuration for a single model tier."""

    model_id: str
    provider: str
    max_tokens: int


# Model IDs match llm/llm_router.py MODEL_ROUTING
# Primary: OpenAI (fully implemented)
# Fallback: Claude (stub — ready when ClaudeClient is implemented)

MODEL_CONFIG: dict[str, ModelTier] = {
    # ── Primary (OpenAI) ─────────────────────────────────────
    "flagship": ModelTier(
        model_id="gpt-4o-mini-2024-07-18",
        provider="openai",
        max_tokens=4096,
    ),
    "reliable": ModelTier(
        model_id="gpt-4o-mini",
        provider="openai",
        max_tokens=4096,
    ),
    "cheap": ModelTier(
        model_id="gpt-4o-mini",
        provider="openai",
        max_tokens=2048,
    ),
    # ── Fallback (Claude — different provider for resilience) ──
    "fallback_flagship": ModelTier(
        model_id="claude-3-5-sonnet-20241022",
        provider="claude",
        max_tokens=4096,
    ),
    "fallback_reliable": ModelTier(
        model_id="claude-3-5-haiku-20241022",
        provider="claude",
        max_tokens=4096,
    ),
}


# ============================================================
# FINANCIAL KEYWORDS — triggers flagship model
# ============================================================

FINANCIAL_KEYWORDS: set[str] = {
    # Indonesian
    "faktur",
    "invoice",
    "tagihan",
    "bill",
    "payment",
    "piutang",
    "hutang",
    "jurnal",
    "journal",
    "tutup buku",
    "close period",
    "neraca",
    "balance sheet",
    "laba rugi",
    "profit loss",
    "rekonsiliasi",
    "reconciliation",
    "nota kredit",
    "credit note",
    "nota debit",
    "debit note",
    "uang muka",
    "down payment",
    "pengeluaran",
    "expense",
    # Master data that involves direct actions
    "rekening",
    "bank",
    "kas",
    "vendor",
    "supplier",
}


# ============================================================
# ACTION VERBS — must accompany financial keyword for flagship
# ============================================================

ACTION_VERBS: set[str] = {
    "buat",
    "buatkan",
    "bikin",
    "bikinkan",
    "catat",
    "catatkan",
    "posting",
    "post",
    "create",
    "record",
    "bayar",
    "bayarkan",
    "lunasi",
    "transfer",
    "tutup",
    "reopen",
    "buka",
    "terima",
    "kirim",
    "hapus",
    "reverse",
    "koreksi",
}


# ============================================================
# MODEL ROUTER
# ============================================================


class ModelRouter:
    """Rule-based model routing.

    Priority order (first match wins):
    1. Retry with fallback  -> fallback_flagship (different provider)
    2. Summary generation   -> cheap (background task)
    3. Pending action exists -> flagship (must reason about action context)
    4. Post-proposal turn   -> flagship (likely confirm/reject/modify)
    5. Financial keywords   -> flagship (hard constraint)
    6. Deep conversation    -> flagship (>10 turns, needs long-context reasoning)
    7. Default              -> reliable (general queries, search, small talk)
    """

    @staticmethod
    def route(
        user_message: str,
        session_state: Optional[dict] = None,
        is_retry: bool = False,
        retry_attempt: int = 0,
        is_summary_generation: bool = False,
        conversation_depth: int = 0,
        previous_turn_proposed: bool = False,
    ) -> ModelChoice:
        """Route a conversational turn to the optimal model.

        Args:
            user_message: Current user message text.
            session_state: Session state dict (may contain pending_action_id, etc).
            is_retry: Whether this is a retry after a model failure.
            retry_attempt: Which retry attempt (0 = first retry).
            is_summary_generation: Whether this is a background summary task (Layer 4).
            conversation_depth: Number of turns in the current conversation.
            previous_turn_proposed: Whether the previous agent turn proposed an action.

        Returns:
            ModelChoice with model_id, provider, tier, reason, and max_tokens.
        """
        state = session_state or {}

        # Rule 0: Retry with fallback provider
        if is_retry and retry_attempt > 0:
            tier = MODEL_CONFIG["fallback_flagship"]
            return ModelChoice(
                model_id=tier.model_id,
                provider=tier.provider,
                tier="flagship",
                reason=f"Fallback after {retry_attempt} model failure(s) — switching provider",
                max_tokens=tier.max_tokens,
            )

        # Rule 1: Background tasks -> cheap
        if is_summary_generation:
            tier = MODEL_CONFIG["cheap"]
            return ModelChoice(
                model_id=tier.model_id,
                provider=tier.provider,
                tier="cheap",
                reason="Layer 4 summary generation (background task)",
                max_tokens=tier.max_tokens,
            )

        # Rule 2: Pending action -> flagship
        if state.get("pending_action_id"):
            tier = MODEL_CONFIG["flagship"]
            return ModelChoice(
                model_id=tier.model_id,
                provider=tier.provider,
                tier="flagship",
                reason="Pending action confirmation context",
                max_tokens=tier.max_tokens,
            )

        # Rule 3: Post-proposal turn -> flagship
        if previous_turn_proposed:
            tier = MODEL_CONFIG["flagship"]
            return ModelChoice(
                model_id=tier.model_id,
                provider=tier.provider,
                tier="flagship",
                reason="Post-proposal turn (likely confirm/reject/modify)",
                max_tokens=tier.max_tokens,
            )

        # Rule 4: Financial keywords — but ONLY if combined with an action verb
        message_lower = user_message.lower()
        words = set(re.findall(r"[a-z]+", message_lower))  # word-level matching
        for keyword in FINANCIAL_KEYWORDS:
            if keyword in words or (len(keyword) > 4 and keyword in message_lower):
                has_action_verb = any(
                    v in words or (len(v) > 4 and v in message_lower)
                    for v in ACTION_VERBS
                )
                if has_action_verb:
                    tier = MODEL_CONFIG["flagship"]
                    return ModelChoice(
                        model_id=tier.model_id,
                        provider=tier.provider,
                        tier="flagship",
                        reason=f"Financial keyword '{keyword}' + action verb detected",
                        max_tokens=tier.max_tokens,
                    )
                # Financial keyword without action verb -> user likely asking a question
                # Fall through to default (reliable)
                break

        # Rule 5: Deep conversation -> flagship
        if conversation_depth > 10:
            tier = MODEL_CONFIG["flagship"]
            return ModelChoice(
                model_id=tier.model_id,
                provider=tier.provider,
                tier="flagship",
                reason=f"Deep conversation ({conversation_depth} turns)",
                max_tokens=tier.max_tokens,
            )

        # Rule 6: Chitchat / greetings -> cheap (no tools needed)
        CHITCHAT_PATTERNS = {
            "halo",
            "hai",
            "hi",
            "hello",
            "hey",
            "selamat pagi",
            "selamat siang",
            "selamat sore",
            "selamat malam",
            "terima kasih",
            "makasih",
            "thanks",
            "ok",
            "oke",
            "baik",
            "siap",
            "mantap",
            "good",
            "bagus",
        }
        msg_stripped = message_lower.strip().rstrip("!?.,:;")
        if msg_stripped in CHITCHAT_PATTERNS or len(msg_stripped) <= 5:
            tier = MODEL_CONFIG["cheap"]
            return ModelChoice(
                model_id=tier.model_id,
                provider=tier.provider,
                tier="cheap",
                reason="Chitchat/greeting detected (no tools needed)",
                max_tokens=tier.max_tokens,
            )

        # Rule 7: Default -> reliable
        tier = MODEL_CONFIG["reliable"]
        return ModelChoice(
            model_id=tier.model_id,
            provider=tier.provider,
            tier="reliable",
            reason="Default routing (general query)",
            max_tokens=tier.max_tokens,
        )

    @staticmethod
    def route_for_action(action_type: str) -> ModelChoice:
        """Route for action execution. Flagship for HIGH risk, reliable for LOW.

        Uses ACTION_TYPE_METADATA from tool_metadata.py to determine risk level.
        Unknown action types default to flagship for safety.

        Args:
            action_type: e.g. "CREATE_SALES_INVOICE", "CREATE_CUSTOMER".

        Returns:
            ModelChoice appropriate for the action's risk level.
        """
        meta = ACTION_TYPE_METADATA.get(action_type)

        # Unknown or high-risk -> flagship (safe default)
        if not meta or meta.requires_flagship:
            tier = MODEL_CONFIG["flagship"]
            reason = (
                f"Action '{action_type}' requires flagship"
                if meta
                else f"Unknown action '{action_type}' — defaulting to flagship"
            )
            return ModelChoice(
                model_id=tier.model_id,
                provider=tier.provider,
                tier="flagship",
                reason=reason,
                max_tokens=tier.max_tokens,
            )

        # Low-risk action -> reliable
        tier = MODEL_CONFIG["reliable"]
        return ModelChoice(
            model_id=tier.model_id,
            provider=tier.provider,
            tier="reliable",
            reason=f"Action '{action_type}' allows reliable (risk: {meta.risk_level.value})",
            max_tokens=tier.max_tokens,
        )
