"""
Bucket A1.7 — Natural-language confirm dispatch parity across all 3 message
endpoints (/message, /message/stream, /message/upload).

Static AST/source assertions: the dispatch block is present in the streaming
and file-upload handlers, reuses the canonical CONFIRM_KEYWORDS/
CANCEL_KEYWORDS from action_service (no forked lists), and includes the
token_count guard to avoid hijacking long utterances like "betul saya beli
router".

Runtime verification of the dispatch firing end-to-end happens via
tests/chat/bucket_c_runner.py (real SSE round-trip).
"""
from __future__ import annotations

import ast
from pathlib import Path


ROUTER_PATH = (
    Path(__file__).resolve().parents[2] / "app" / "routers" / "unified_chat.py"
)


def _func_source(func_name: str) -> str:
    src = ROUTER_PATH.read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == func_name:
            return ast.get_source_segment(src, node) or ""
    raise AssertionError(f"async def {func_name} not found in {ROUTER_PATH}")


def test_stream_endpoint_has_natural_language_dispatch():
    body = _func_source("send_message_stream")
    assert "[BucketA1] Stream NL CONFIRM routed" in body
    assert "[BucketA1] Stream NL CANCEL routed" in body
    assert "ConfirmActionRequest(" in body
    assert "CancelActionRequest(" in body


def test_files_endpoint_has_natural_language_dispatch():
    body = _func_source("send_message_with_files")
    assert "[BucketA1] Upload NL CONFIRM routed" in body
    assert "[BucketA1] Upload NL CANCEL routed" in body
    assert "ConfirmActionRequest(" in body
    assert "CancelActionRequest(" in body


def test_stream_dispatch_reuses_canonical_keyword_lists():
    """Both new dispatch blocks must use the imported CONFIRM_KEYWORDS /
    CANCEL_KEYWORDS, not redefine local lists."""
    src = ROUTER_PATH.read_text()
    # Confirm import is from action_service (single source of truth)
    assert (
        "from ..services.action_service import CONFIRM_KEYWORDS, CANCEL_KEYWORDS" in src
    )
    # Confirm no local re-definition
    assert "CONFIRM_KEYWORDS = " not in src
    assert "CANCEL_KEYWORDS = " not in src

    for fname in ("send_message_stream", "send_message_with_files"):
        body = _func_source(fname)
        assert "CONFIRM_KEYWORDS" in body, f"{fname} missing CONFIRM_KEYWORDS use"
        assert "CANCEL_KEYWORDS" in body, f"{fname} missing CANCEL_KEYWORDS use"


def test_stream_dispatch_token_count_guard():
    """Risk Flag 1 mitigation: only short utterances may be routed."""
    for fname in ("send_message_stream", "send_message_with_files"):
        body = _func_source(fname)
        assert "token_count <= 3" in body, f"{fname} missing token_count guard"
        assert "token_count > 0" in body, f"{fname} missing positive guard"


def test_stream_dispatch_narrowed_exception():
    """Bucket 0 discipline: dispatch wrapped in narrowed exception."""
    for fname in ("send_message_stream", "send_message_with_files"):
        body = _func_source(fname)
        # all four narrowed exception classes present
        for exc in (
            "KeyError",
            "TypeError",
            "ValueError",
            "asyncpg.PostgresError",
            "json.JSONDecodeError",
        ):
            assert exc in body, f"{fname} missing {exc} in narrowed except"
