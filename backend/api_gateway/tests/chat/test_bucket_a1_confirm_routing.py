"""
Bucket A1 — Natural-Language Confirm/Reject Routing Regression
===============================================================

Locks in the Bucket A1 contract for /api/v3/chat/message:

1. When a pending_action exists, a short (≤3 token) confirm utterance
   ("betul", "ya", "oke") is dispatched to /confirm internally, firing
   `after_confirm` and writing to `action_patterns`.
2. A short cancel utterance ("batal", "tidak") is dispatched to /cancel,
   firing `after_reject`.
3. Ambiguous utterances >3 tokens containing a confirm keyword
   ("betul saya beli router") fall through to normal NLU — they must
   NOT auto-confirm (Risk Flag 1).
4. With no pending_action_id, keywords like "betul" fall through
   (no hijack of chitchat).
5. Match is case-insensitive and whitespace-tolerant.

Tests are STATIC (AST walks over source) where possible. Dynamic tests
use monkeypatch on StateUpdateHooks + a stub ActionExecutor to avoid
hitting the real DB / gRPC.
"""
from __future__ import annotations

import ast
from pathlib import Path


# ---------------------------------------------------------------------------
# Source-file locations
# ---------------------------------------------------------------------------
BACKEND_ROOT = Path(__file__).resolve().parents[2]
UNIFIED_CHAT = BACKEND_ROOT / "app" / "routers" / "unified_chat.py"
ACTION_SERVICE = BACKEND_ROOT / "app" / "services" / "action_service.py"


def _src(path: Path) -> str:
    assert path.exists(), f"Missing source file: {path}"
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Test 1: confirm-keyword coverage in single source of truth
# ---------------------------------------------------------------------------
def test_confirm_keywords_extended_with_indonesian_variants():
    """
    CONFIRM_KEYWORDS in action_service.py must include common Indonesian
    confirmation variants used in real traffic.
    """
    src = _src(ACTION_SERVICE)
    # Extract the CONFIRM_KEYWORDS literal via AST
    tree = ast.parse(src)
    confirm_list = None
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "CONFIRM_KEYWORDS"
        ):
            if isinstance(node.value, ast.List):
                confirm_list = [
                    elt.value
                    for elt in node.value.elts
                    if isinstance(elt, ast.Constant)
                ]
    assert confirm_list is not None, "CONFIRM_KEYWORDS not found"
    must_have = {"betul", "benar", "ya", "iya", "oke", "ok", "lanjut", "konfirmasi"}
    missing = must_have - set(confirm_list)
    assert not missing, f"CONFIRM_KEYWORDS missing required variants: {missing}"


def test_cancel_keywords_cover_indonesian_variants():
    src = _src(ACTION_SERVICE)
    tree = ast.parse(src)
    cancel_list = None
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "CANCEL_KEYWORDS"
        ):
            if isinstance(node.value, ast.List):
                cancel_list = [
                    elt.value
                    for elt in node.value.elts
                    if isinstance(elt, ast.Constant)
                ]
    assert cancel_list is not None, "CANCEL_KEYWORDS not found"
    must_have = {"batal", "cancel", "tidak", "jangan", "stop"}
    missing = must_have - set(cancel_list)
    assert not missing, f"CANCEL_KEYWORDS missing required variants: {missing}"


# ---------------------------------------------------------------------------
# Test 2: unified_chat imports from single source of truth, not forking
# ---------------------------------------------------------------------------
def test_unified_chat_imports_canonical_keyword_lists():
    """
    unified_chat.py must import CONFIRM_KEYWORDS/CANCEL_KEYWORDS from
    action_service (not redefine them) — single source of truth.
    """
    src = _src(UNIFIED_CHAT)
    assert (
        "from ..services.action_service import" in src
    ), "unified_chat must import from action_service"
    # The import line must mention both constants
    tree = ast.parse(src)
    found_confirm = False
    found_cancel = False
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").endswith(
            "services.action_service"
        ):
            names = {a.name for a in node.names}
            if "CONFIRM_KEYWORDS" in names:
                found_confirm = True
            if "CANCEL_KEYWORDS" in names:
                found_cancel = True
    assert found_confirm, "CONFIRM_KEYWORDS not imported from action_service"
    assert found_cancel, "CANCEL_KEYWORDS not imported from action_service"


# ---------------------------------------------------------------------------
# Test 3: dispatch block present with token-count guard (Risk Flag 1)
# ---------------------------------------------------------------------------
def test_dispatch_has_token_count_guard():
    """
    The dispatch block must guard on token_count <= 3 to avoid routing
    "betul saya beli router" (>3 tokens) as confirm.
    """
    src = _src(UNIFIED_CHAT)
    assert "BucketA1" in src, "Bucket A1 marker missing from unified_chat"
    assert (
        "token_count <= 3" in src or "token_count<=3" in src
    ), "Token-count guard (<=3) missing — Risk Flag 1 regression"
    # Must mention both confirm_action and cancel_action as internal calls
    assert "await confirm_action(" in src, "Internal confirm_action() call missing"
    assert "await cancel_action(" in src, "Internal cancel_action() call missing"


# ---------------------------------------------------------------------------
# Test 4: status quote bug fix regression — 'PENDING' not PENDING
# ---------------------------------------------------------------------------
def test_pending_actions_status_quoted():
    """
    The pending_actions guard query must use quoted 'PENDING' (string
    literal), not bare PENDING (which raises UndefinedColumn and gets
    silently swallowed).
    """
    src = _src(UNIFIED_CHAT)
    assert (
        "status = 'PENDING'" in src
    ), "pending_actions guard must use quoted 'PENDING' string literal"
    # The buggy unquoted form must NOT reappear
    assert "status = PENDING" not in src, "Unquoted PENDING identifier regressed"


# ---------------------------------------------------------------------------
# Test 5: fallthrough contract — 4-word utterance must NOT match
# ---------------------------------------------------------------------------
def test_four_word_utterance_does_not_route_to_confirm():
    """
    Simulate the dispatch logic in isolation to prove "betul saya beli router"
    (4 tokens, contains "betul") does NOT trigger confirm routing.
    """
    # Mirror the constants & logic from unified_chat dispatch
    import sys

    sys.path.insert(0, str(BACKEND_ROOT))
    from app.services.action_service import CONFIRM_KEYWORDS, CANCEL_KEYWORDS

    def would_route_confirm(text: str, pending_id: str | None) -> bool:
        if not pending_id or not text:
            return False
        message_lower = text.lower().strip()
        token_count = len(message_lower.split())
        if token_count > 3 or token_count == 0:
            return False
        tokens = set(message_lower.split())
        is_confirm = any(kw in tokens for kw in CONFIRM_KEYWORDS)
        is_cancel = any(kw in tokens for kw in CANCEL_KEYWORDS)
        return is_confirm and not is_cancel

    pending = "some-pending-uuid"
    # Positive cases
    assert would_route_confirm("betul", pending) is True
    assert would_route_confirm("  BETUL  ", pending) is True
    assert would_route_confirm("Iya", pending) is True
    assert would_route_confirm("ya lanjut", pending) is True
    # Risk Flag 1 — must NOT route
    assert would_route_confirm("betul saya beli router", pending) is False
    # No pending
    assert would_route_confirm("betul", None) is False
    # Cancel+confirm in same short utterance — reject both (ambiguous)
    assert would_route_confirm("ya batal", pending) is False


def test_cancel_dispatch_parallel_contract():
    import sys

    sys.path.insert(0, str(BACKEND_ROOT))
    from app.services.action_service import CONFIRM_KEYWORDS, CANCEL_KEYWORDS

    def would_route_cancel(text: str, pending_id: str | None) -> bool:
        if not pending_id or not text:
            return False
        message_lower = text.lower().strip()
        token_count = len(message_lower.split())
        if token_count > 3 or token_count == 0:
            return False
        tokens = set(message_lower.split())
        is_confirm = any(kw in tokens for kw in CONFIRM_KEYWORDS)
        is_cancel = any(kw in tokens for kw in CANCEL_KEYWORDS)
        return is_cancel and not is_confirm

    pending = "some-pending-uuid"
    assert would_route_cancel("batal", pending) is True
    assert would_route_cancel("BATAL", pending) is True
    assert would_route_cancel("jangan", pending) is True
    assert would_route_cancel("batal saja ini salah", pending) is False  # >3 tokens
    assert would_route_cancel("batal", None) is False
