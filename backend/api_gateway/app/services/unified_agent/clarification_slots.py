"""
Clarification slot persistence per ADR P4 v1.3.

Phase 1 scope: period slot only. One function per concern, no registry (YAGNI).
Registry abstraction deferred until second slot type lands.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
import json
import re

from .period_resolver import resolve_period

SLOT_TTL_MINUTES = 5
MAX_REASK = 1
ABANDON_WORD_THRESHOLD = 6
RESIDUE_WORD_THRESHOLD = 3


@dataclass
class PendingClarification:
    slot_type: str
    parent_intent: str
    parent_entities: Dict[str, Any]
    asked_at: datetime
    expires_at: datetime
    reask_count: int

    @property
    def is_expired(self) -> bool:
        return datetime.now(timezone.utc) >= self.expires_at


@dataclass
class SlotFillResult:
    filled: bool
    resolved_value: Optional[Any]
    residue_text: str
    residue_word_count: int
    has_residue: bool


def load_pending_clarification(
    session_state: Dict[str, Any],
) -> Optional[PendingClarification]:
    raw = session_state.get("pending_clarification")
    if not raw:
        return None
    data = raw if isinstance(raw, dict) else json.loads(raw)
    expires_raw = session_state.get("pending_clarification_expires_at")
    if not expires_raw:
        return None
    expires_at = (
        expires_raw
        if isinstance(expires_raw, datetime)
        else datetime.fromisoformat(expires_raw)
    )
    asked_at = data.get("asked_at")
    asked_at_dt = (
        asked_at if isinstance(asked_at, datetime) else datetime.fromisoformat(asked_at)
    )
    return PendingClarification(
        slot_type=data["slot_type"],
        parent_intent=data["parent_intent"],
        parent_entities=data.get("parent_entities", {}),
        asked_at=asked_at_dt,
        expires_at=expires_at,
        reask_count=data.get("reask_count", 0),
    )


def try_fill_period_slot(user_text: str) -> SlotFillResult:
    resolved = resolve_period(user_text)
    if resolved is None:
        return SlotFillResult(
            filled=False,
            resolved_value=None,
            residue_text=user_text,
            residue_word_count=len(user_text.split()),
            has_residue=False,
        )

    period_patterns = [
        r"\bbulan\s+(?:ini|lalu|kemarin)\b",
        r"\bminggu\s+(?:ini|lalu|kemarin)\b",
        r"\bhari\s+(?:ini|kemarin)\b",
        r"\b\d+\s+hari\s+(?:terakhir|lalu)\b",
        r"\bq[1-4]\b",
        r"\b(?:januari|februari|maret|april|mei|juni|juli|agustus|september|oktober|november|desember)(?:\s+\d{4})?\b",
        r"\btahun\s+(?:ini|lalu)\b",
    ]
    stripped = user_text.lower()
    for p in period_patterns:
        stripped = re.sub(p, "", stripped, flags=re.IGNORECASE)
    stripped = re.sub(r"[,\s]+", " ", stripped).strip()
    residue_words = stripped.split() if stripped else []
    residue_count = len(residue_words)
    has_residue = residue_count > RESIDUE_WORD_THRESHOLD

    return SlotFillResult(
        filled=True,
        resolved_value=resolved,
        residue_text=stripped,
        residue_word_count=residue_count,
        has_residue=has_residue,
    )


def is_explicit_domain_switch(user_text: str, parent_intent: str) -> bool:
    from .domain_vocab import DOMAIN_TOKENS

    parent_domain = _intent_to_domain(parent_intent)
    for domain, pattern in DOMAIN_TOKENS.items():
        if domain == parent_domain:
            continue
        if re.search(pattern, user_text.lower()):
            return True
    return False


def _intent_to_domain(intent: str) -> str:
    if intent.startswith("query_ar_") or intent.startswith("calc_rank_customers_by_ar"):
        return "ar"
    if intent.startswith("query_ap_") or intent.startswith("calc_rank_vendors_by_ap"):
        return "ap"
    if "expense" in intent or "beban" in intent:
        return "expense"
    if "stock" in intent or "item" in intent:
        return "stock"
    if "sales" in intent or "revenue" in intent:
        return "sales"
    return "unknown"


async def emit_period_clarification(
    db, session_id: str, parent_intent: str, parent_entities: Dict[str, Any]
) -> None:
    now = datetime.now(timezone.utc)
    payload = {
        "slot_type": "period",
        "parent_intent": parent_intent,
        "parent_entities": parent_entities,
        "asked_at": now.isoformat(),
        "reask_count": 0,
    }
    expires_at = now + timedelta(minutes=SLOT_TTL_MINUTES)
    await db.execute(
        """
        UPDATE chat_session_state
        SET pending_clarification = $1::jsonb,
            pending_clarification_expires_at = $2
        WHERE session_id = $3::uuid
    """,
        json.dumps(payload),
        expires_at,
        session_id,
    )


async def increment_reask(db, session_id: str, pending: PendingClarification) -> None:
    payload = {
        "slot_type": pending.slot_type,
        "parent_intent": pending.parent_intent,
        "parent_entities": pending.parent_entities,
        "asked_at": pending.asked_at.isoformat(),
        "reask_count": pending.reask_count + 1,
    }
    await db.execute(
        """
        UPDATE chat_session_state
        SET pending_clarification = $1::jsonb
        WHERE session_id = $2::uuid
    """,
        json.dumps(payload),
        session_id,
    )


async def clear_pending_clarification(db, session_id: str) -> None:
    await db.execute(
        """
        UPDATE chat_session_state
        SET pending_clarification = NULL,
            pending_clarification_expires_at = NULL
        WHERE session_id = $1::uuid
    """,
        session_id,
    )
