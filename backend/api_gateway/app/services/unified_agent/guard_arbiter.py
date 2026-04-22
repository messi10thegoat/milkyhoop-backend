"""GuardArbiter — centralized P2 guard arbitration primitive.

Replaces the ~246-line sequential guard cascade in orchestrator.py (~5626-5869)
with a declarative policy table + single decide() call.

Source of truth: docs/plans/2026-04-22-p2-guard-audit-matrix.md v1.0 (locked).
Phase-A diffs: docs/plans/2026-04-22-guard-arbiter-phase-a-diffs.md.

Policy table (matrix summary):
    ARAP_GUARD          CONFIDENCE_AWARE 0.85   priority 60
    ARAP_SUMMARY_GUARD  ALWAYS_WIN              priority 55 (nested downgrade)
    LIST_GUARD          ALWAYS_WIN              priority 50
    REFORMAT_GUARD      ALWAYS_WIN              priority 100 (REC-exempt; pending_clar yields)
    DRILL_GUARD         CONTEXT_AWARE           priority 90  (REC-exempt)
    CALC_GUARD          CONFIDENCE_AWARE 0.85   priority 80
    MFG_GUARD           ALWAYS_WIN              priority 70
    QUERY_BOOST         WEAK_FALLBACK           priority 20
    DE_ESCALATE         CONTEXT_AWARE           (post-processor)

Tie-break:  REFORMAT > DRILL > CRUD(85,deferred) > CALC > MFG > ARAP > ARAP_SUMMARY > LIST > QUERY_BOOST

Universal skips:
    - pending_clarification active (not expired) → ALL guards skip → return clarification_response
    - REC short follow-up (<4 words + last_domain) → skip EXCEPT REFORMAT, DRILL
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional
from datetime import datetime, timezone


class PolicyType(str, Enum):
    ALWAYS_WIN = "always_win"
    CONFIDENCE_AWARE = "confidence_aware"
    CONTEXT_AWARE = "context_aware"
    WEAK_FALLBACK = "weak_fallback"


@dataclass(frozen=True)
class GuardPolicy:
    name: str
    policy: PolicyType
    priority: int
    conf_threshold: Optional[float] = None
    rec_short_exempt: bool = False
    pending_clar_exempt: bool = False


@dataclass
class GuardMatch:
    guard_name: str
    proposed_intent: str
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ArbitrationDecision:
    winner: str
    final_intent: str
    final_confidence: float
    reason: str
    guard_matches: Dict[str, str] = field(default_factory=dict)
    conflict: bool = False
    policy_applied: str = ""
    guard_from: Optional[str] = None
    guard_to: Optional[str] = None


GUARD_POLICIES: Dict[str, GuardPolicy] = {
    "REFORMAT_GUARD": GuardPolicy(
        "REFORMAT_GUARD", PolicyType.ALWAYS_WIN, priority=100, rec_short_exempt=True
    ),
    "DRILL_GUARD": GuardPolicy(
        "DRILL_GUARD", PolicyType.CONTEXT_AWARE, priority=90, rec_short_exempt=True
    ),
    "CRUD_GUARD": GuardPolicy("CRUD_GUARD", PolicyType.ALWAYS_WIN, priority=85),
    "CALC_GUARD": GuardPolicy(
        "CALC_GUARD", PolicyType.CONFIDENCE_AWARE, priority=80, conf_threshold=0.85
    ),
    "MFG_GUARD": GuardPolicy("MFG_GUARD", PolicyType.ALWAYS_WIN, priority=70),
    "ARAP_GUARD": GuardPolicy(
        "ARAP_GUARD", PolicyType.CONFIDENCE_AWARE, priority=60, conf_threshold=0.85
    ),
    "ARAP_SUMMARY_GUARD": GuardPolicy(
        "ARAP_SUMMARY_GUARD", PolicyType.ALWAYS_WIN, priority=55
    ),
    "LIST_GUARD": GuardPolicy("LIST_GUARD", PolicyType.ALWAYS_WIN, priority=50),
    "QUERY_BOOST": GuardPolicy("QUERY_BOOST", PolicyType.WEAK_FALLBACK, priority=20),
}


def _pending_clar_active(session_state: Optional[Dict[str, Any]]) -> bool:
    if not session_state:
        return False
    pc = session_state.get("pending_clarification")
    if not pc or not isinstance(pc, dict):
        return False
    expires = pc.get("expires_at")
    if not expires:
        return True
    try:
        if isinstance(expires, str):
            exp_dt = datetime.fromisoformat(expires.replace("Z", "+00:00"))
        else:
            exp_dt = expires
        now = datetime.now(timezone.utc)
        if exp_dt.tzinfo is None:
            exp_dt = exp_dt.replace(tzinfo=timezone.utc)
        return exp_dt > now
    except Exception:
        return True


def _rec_short_followup(
    session_state: Optional[Dict[str, Any]], user_text: str
) -> bool:
    if not session_state:
        return False
    last_domain = session_state.get("last_domain")
    if not last_domain:
        return False
    words = [w for w in (user_text or "").split() if w]
    return len(words) < 4


class GuardArbiter:
    def decide(
        self,
        *,
        llm_intent: str,
        llm_confidence: float,
        llm_domain: Optional[str],
        llm_needs_escalation: bool,
        guard_matches: Dict[str, GuardMatch],
        session_state: Optional[Dict[str, Any]],
        user_text: str,
        context_hint: bool = False,
    ) -> ArbitrationDecision:
        matches_telemetry = {k: m.proposed_intent for k, m in guard_matches.items()}

        # 1. Pending clar — universal skip
        if _pending_clar_active(session_state):
            return ArbitrationDecision(
                winner="PENDING_CLAR",
                final_intent="clarification_response",
                final_confidence=1.0,
                reason="pending_clarification active (ADR P4 D2)",
                guard_matches=matches_telemetry,
                policy_applied="universal_skip_pending_clar",
            )

        # 2. REC short follow-up
        rec_short = _rec_short_followup(session_state, user_text)
        eligible: Dict[str, GuardMatch] = {}
        for name, m in guard_matches.items():
            policy = GUARD_POLICIES.get(name)
            if not policy:
                continue
            if rec_short and not policy.rec_short_exempt:
                continue
            eligible[name] = m

        if not eligible:
            if rec_short and guard_matches:
                return ArbitrationDecision(
                    winner="REC",
                    final_intent=llm_intent,
                    final_confidence=llm_confidence,
                    reason="REC short follow-up owns window",
                    guard_matches=matches_telemetry,
                    policy_applied="rec_short_skip",
                )
            return ArbitrationDecision(
                winner="NO_GUARD",
                final_intent=llm_intent,
                final_confidence=llm_confidence,
                reason="no guard matched",
                guard_matches=matches_telemetry,
                policy_applied="passthrough",
            )

        # 3. Per-policy filter
        winners: list[tuple[GuardPolicy, GuardMatch]] = []
        for name, m in eligible.items():
            policy = GUARD_POLICIES[name]
            if policy.policy == PolicyType.ALWAYS_WIN:
                winners.append((policy, m))
            elif policy.policy == PolicyType.CONFIDENCE_AWARE:
                same_family = bool(m.metadata.get("same_family"))
                if (
                    llm_confidence >= (policy.conf_threshold or 1.0)
                    and llm_intent != m.proposed_intent
                    and not same_family
                ):
                    continue
                winners.append((policy, m))
            elif policy.policy == PolicyType.CONTEXT_AWARE:
                if m.metadata.get("context_ok", False):
                    winners.append((policy, m))
            elif policy.policy == PolicyType.WEAK_FALLBACK:
                llm_weak = (
                    llm_intent in ("ambiguous", "query", "chitchat")
                    or llm_confidence < 0.5
                    or llm_needs_escalation
                )
                if llm_weak and not context_hint:
                    winners.append((policy, m))

        if not winners:
            return ArbitrationDecision(
                winner="LLM",
                final_intent=llm_intent,
                final_confidence=llm_confidence,
                reason="all guards yielded to LLM",
                guard_matches=matches_telemetry,
                policy_applied="llm_wins",
            )

        # 4. Tie-break
        winners.sort(key=lambda pm: pm[0].priority, reverse=True)
        top_policy, top_match = winners[0]
        conflict = len({m.proposed_intent for _, m in winners}) > 1

        # 5. ARAP_SUMMARY nested downgrade
        if "ARAP_SUMMARY_GUARD" in eligible and top_policy.name == "ARAP_GUARD":
            top_policy = GUARD_POLICIES["ARAP_SUMMARY_GUARD"]
            top_match = eligible["ARAP_SUMMARY_GUARD"]

        return ArbitrationDecision(
            winner=top_policy.name,
            final_intent=top_match.proposed_intent,
            final_confidence=1.0,
            reason=f"{top_policy.name} ({top_policy.policy.value}) priority={top_policy.priority}",
            guard_matches=matches_telemetry,
            conflict=conflict,
            policy_applied=top_policy.policy.value,
            guard_from=llm_intent,
            guard_to=top_match.proposed_intent,
        )

    def apply_de_escalate(
        self,
        *,
        intent: str,
        needs_escalation: bool,
        context_hint: bool,
        is_pipeline_enabled_fn,
    ) -> tuple[bool, bool]:
        if (
            needs_escalation
            and intent.startswith("query_")
            and is_pipeline_enabled_fn(intent)
            and not context_hint
        ):
            return (False, True)
        return (needs_escalation, False)
