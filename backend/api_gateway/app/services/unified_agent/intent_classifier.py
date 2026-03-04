"""
Intent Classifier for MilkyHoop Bot Cost Optimization Phase 4.

Two-stage routing:
1. Code check: active workflow? → skip classifier, route to WORKFLOW_CONTINUE
2. LLM classifier: ~600 token input, ~30 token output, ~100ms
   → high confidence (≥ 0.7): narrow domain routing
   → low confidence (< 0.7): broad domain fallback

Model: gpt-4o-mini (same as agent — consistent quality).
Cost: ~$0.0001 per classification.
"""

import json
import time
import logging
import os
from dataclasses import dataclass, field
from typing import Optional, Set, List

import httpx

logger = logging.getLogger(__name__)

# ─── OpenAI direct client for classifier (lightweight, no LLMRouter overhead) ──
_OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"
_classifier_client: Optional[httpx.AsyncClient] = None
_classifier_headers: dict = {}


def _get_classifier_client():
    """Lazy-init a lightweight httpx client for classification calls."""
    global _classifier_client, _classifier_headers
    if _classifier_client is None:
        api_key = os.environ.get("OPENAI_API_KEY", "")
        _classifier_client = httpx.AsyncClient(
            timeout=10.0,
            limits=httpx.Limits(max_connections=5, max_keepalive_connections=3),
        )
        _classifier_headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
    return _classifier_client, _classifier_headers


INTENTS = [
    "CHITCHAT",
    "SIMPLE_READ",
    "COMPLEX_READ",
    "ACTION",
    "CHART",
    "RECON",
    "FOLLOWUP",
]

CLASSIFIER_PROMPT = """You are an intent classifier for an Indonesian accounting chatbot.

Given a user message, classify into exactly one intent:
- CHITCHAT: greetings, thanks, small talk, questions about the bot (halo, terima kasih, siapa kamu)
- SIMPLE_READ: single entity lookup, balance check, list query (berapa saldo, siapa vendor, cari invoice)
- COMPLEX_READ: multi-entity comparison, cross-module analysis, aggregation (bandingkan hutang A vs B, total per bulan)
- ACTION: create, edit, delete, void, pay — any data mutation (buatkan faktur, hapus vendor, void invoice)
- CHART: visualization request (grafik, chart, diagram, tampilkan grafik)
- RECON: bank reconciliation (rekon, rekonsiliasi)
- FOLLOWUP: continuation of previous topic, unclear intent (lanjutkan, yang tadi, iya)

IMPORTANT:
- Greeting/thanks → CHITCHAT (high confidence)
- Ambiguous between READ and ACTION → prefer SIMPLE_READ
- Very short and unclear → FOLLOWUP (low confidence)
- "berapa yang belum dibayar si X" = SIMPLE_READ not ACTION
- "tampilkan stok" or "berapa stok" = SIMPLE_READ

Return JSON only: {"intent": "...", "confidence": 0.0-1.0}

Examples:
"halo" → {"intent": "CHITCHAT", "confidence": 0.99}
"makasih ya" → {"intent": "CHITCHAT", "confidence": 0.95}
"siapa kamu?" → {"intent": "CHITCHAT", "confidence": 0.93}
"berapa piutang PT Maju?" → {"intent": "SIMPLE_READ", "confidence": 0.95}
"berapa yang belum dibayar si Budi?" → {"intent": "SIMPLE_READ", "confidence": 0.80}
"tampilkan stok barang" → {"intent": "SIMPLE_READ", "confidence": 0.90}
"berapa saldo kas dan piutang?" → {"intent": "COMPLEX_READ", "confidence": 0.82}
"buatkan faktur untuk Budi" → {"intent": "ACTION", "confidence": 0.92}
"void invoice INV-001" → {"intent": "ACTION", "confidence": 0.90}
"tampilkan grafik penjualan" → {"intent": "CHART", "confidence": 0.95}
"rekon BCA" → {"intent": "RECON", "confidence": 0.97}
"lanjutkan" → {"intent": "FOLLOWUP", "confidence": 0.85}
"iya" → {"intent": "FOLLOWUP", "confidence": 0.70}"""

CONFIDENCE_THRESHOLD = 0.7
CHITCHAT_SHORTCIRCUIT_THRESHOLD = 0.9


@dataclass
class ClassificationResult:
    intent: str
    confidence: float
    classifier_tokens_in: int = 0
    classifier_tokens_out: int = 0
    classifier_latency_ms: int = 0


@dataclass
class RouteResult:
    """Complete routing decision."""
    intent: str
    confidence: float = 1.0
    classifier_skipped: bool = False
    low_confidence_fallback: bool = False
    classifier_tokens_in: int = 0
    classifier_tokens_out: int = 0
    classifier_latency_ms: int = 0
    workflow_type: str = ""


async def classify_intent(user_message: str) -> ClassificationResult:
    """
    Classify user intent with gpt-4o-mini.
    ~600 token input, ~30 token output, ~100ms latency.
    """
    client, headers = _get_classifier_client()
    start = time.time()

    try:
        payload = {
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "system", "content": CLASSIFIER_PROMPT},
                {"role": "user", "content": user_message},
            ],
            "max_tokens": 50,
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }

        resp = await client.post(_OPENAI_API_URL, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()

        latency_ms = int((time.time() - start) * 1000)

        usage = data.get("usage", {})
        content = data["choices"][0]["message"]["content"]
        parsed = json.loads(content)

        result = ClassificationResult(
            intent=parsed.get("intent", "FOLLOWUP"),
            confidence=float(parsed.get("confidence", 0.5)),
            classifier_tokens_in=usage.get("prompt_tokens", 0),
            classifier_tokens_out=usage.get("completion_tokens", 0),
            classifier_latency_ms=latency_ms,
        )

        # Validate intent
        if result.intent not in INTENTS:
            logger.warning("[CLASSIFIER] Unknown intent '%s', defaulting to FOLLOWUP", result.intent)
            result.intent = "FOLLOWUP"
            result.confidence = 0.5

        logger.warning(
            "[CLASSIFIER] intent=%s confidence=%.2f tokens=%d+%d latency=%dms",
            result.intent, result.confidence,
            result.classifier_tokens_in, result.classifier_tokens_out,
            result.classifier_latency_ms,
        )

        return result

    except Exception as e:
        latency_ms = int((time.time() - start) * 1000)
        logger.error("[CLASSIFIER] Error: %s, falling back to FOLLOWUP", e)
        return ClassificationResult(
            intent="FOLLOWUP",
            confidence=0.0,
            classifier_latency_ms=latency_ms,
        )


async def check_active_workflow(db_pool, chat_session_id: str) -> Optional[dict]:
    """
    Check if there's an active workflow for this session.
    Returns dict with workflow_type if active, None otherwise.
    """
    if not db_pool or not chat_session_id:
        return None

    try:
        row = await db_pool.fetchrow(
            """SELECT workflow_type, current_state, status
               FROM chat_workflow_state
               WHERE chat_session_id = $1
                 AND status IN ('active', 'failed')
               LIMIT 1""",
            chat_session_id,
        )
        if row:
            return {
                "workflow_type": row["workflow_type"],
                "current_state": row["current_state"],
                "status": row["status"],
            }
    except Exception as e:
        logger.warning("[CLASSIFIER] Workflow check error: %s", e)

    return None


async def classify_and_route(
    user_message: str,
    chat_session_id: str = None,
    db_pool=None,
) -> RouteResult:
    """
    Main routing function. Called ONCE per turn.

    Priority:
    1. Active workflow → skip classifier (cost: $0, latency: ~0ms)
    2. Classify → high confidence (≥ 0.7) → narrow routing
    3. Classify → low confidence (< 0.7) → broad fallback
    """
    # ─── CHECK 1: Active workflow → skip classifier ───
    active_wf = await check_active_workflow(db_pool, chat_session_id)
    if active_wf:
        wf_type = active_wf["workflow_type"]
        logger.warning(
            "[CLASSIFIER] SKIP — active workflow type=%s state=%s → WORKFLOW_CONTINUE",
            wf_type, active_wf.get("current_state"),
        )
        return RouteResult(
            intent="WORKFLOW_CONTINUE",
            confidence=1.0,
            classifier_skipped=True,
            workflow_type=wf_type,
        )

    # ─── CHECK 2: Classify intent ───
    result = await classify_intent(user_message)

    # ─── CHECK 3: Low confidence → broad fallback ───
    if result.confidence < CONFIDENCE_THRESHOLD:
        logger.warning(
            "[CLASSIFIER] LOW CONFIDENCE (%.2f < %.2f) → broad fallback for intent=%s",
            result.confidence, CONFIDENCE_THRESHOLD, result.intent,
        )
        return RouteResult(
            intent=result.intent,
            confidence=result.confidence,
            low_confidence_fallback=True,
            classifier_tokens_in=result.classifier_tokens_in,
            classifier_tokens_out=result.classifier_tokens_out,
            classifier_latency_ms=result.classifier_latency_ms,
        )

    # ─── High confidence → narrow routing ───
    return RouteResult(
        intent=result.intent,
        confidence=result.confidence,
        classifier_tokens_in=result.classifier_tokens_in,
        classifier_tokens_out=result.classifier_tokens_out,
        classifier_latency_ms=result.classifier_latency_ms,
    )
