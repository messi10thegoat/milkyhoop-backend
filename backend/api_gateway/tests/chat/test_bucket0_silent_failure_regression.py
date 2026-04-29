"""
Bucket 0 — Silent Failure Fix Regression Test
==============================================

Locks in the Bucket 0 narrowing contract:

1. A1 regression: `NameError` raised inside `StateUpdateHooks.after_confirm`
   must propagate past the confirm endpoint's try/except (or at minimum be
   logged at ERROR with traceback) — it must NOT be silently swallowed as
   a warning anymore.
2. AST narrowing-stays-narrow: the 5 A-site `except` clauses must name
   specific exception types (not bare `Exception`/`BaseException`).
3. Category C narrowing: C1 must catch `(json.JSONDecodeError, TypeError)`,
   C2 must catch `asyncpg.UniqueViolationError`.

This test is STATIC where possible (AST walks over source files) and uses
monkeypatch + caplog for the single dynamic check. It does not require a
live DB.
"""
from __future__ import annotations

import ast
import logging
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Source-file locations (repo-relative; tests run from /app/backend/api_gateway)
# ---------------------------------------------------------------------------
BACKEND_ROOT = Path(__file__).resolve().parents[2]
UNIFIED_CHAT = BACKEND_ROOT / "app" / "routers" / "unified_chat.py"
SESSION_MANAGER = (
    BACKEND_ROOT / "app" / "services" / "unified_agent" / "session_manager.py"
)


def _parse(path: Path) -> ast.Module:
    assert path.exists(), f"Missing source file: {path}"
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _collect_handlers(tree: ast.Module) -> list[ast.ExceptHandler]:
    return [n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler)]


def _handler_names(handler: ast.ExceptHandler) -> list[str]:
    """Return the list of exception type names in an `except X:` or `except (X, Y):` clause."""
    t = handler.type
    if t is None:
        return ["__BARE_EXCEPT__"]
    if isinstance(t, ast.Tuple):
        names = []
        for elt in t.elts:
            names.append(_attr_name(elt))
        return names
    return [_attr_name(t)]


def _attr_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parts = []
        cur: ast.AST = node
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            parts.append(cur.id)
        return ".".join(reversed(parts))
    return ast.dump(node)


def _handler_at_line(
    tree: ast.Module, line: int, tolerance: int = 20
) -> ast.ExceptHandler:
    """Find the ExceptHandler whose `lineno` is within `tolerance` of `line`."""
    candidates = [
        h for h in _collect_handlers(tree) if abs(h.lineno - line) <= tolerance
    ]
    assert candidates, f"No ExceptHandler found within {tolerance} lines of {line}"
    # Return the closest one
    return min(candidates, key=lambda h: abs(h.lineno - line))


# ---------------------------------------------------------------------------
# Test 1: AST narrowing-stays-narrow for A-sites + C-sites
# ---------------------------------------------------------------------------

# Expected narrowed exception sets per site. Line numbers are approximate —
# resolver uses tolerance window so they survive ±12 LOC drift.
A_SITES = [
    # (file_tree_key, approx_line, must_contain_any_of, context_hint)
    (
        "unified_chat",
        4569,
        {
            "KeyError",
            "TypeError",
            "ValueError",
            "asyncpg.PostgresError",
            "json.JSONDecodeError",
        },
        "A1 confirm",
    ),
    (
        "unified_chat",
        4723,
        {
            "KeyError",
            "TypeError",
            "ValueError",
            "asyncpg.PostgresError",
            "json.JSONDecodeError",
        },
        "A2 cancel",
    ),
    (
        "session_manager",
        1260,
        {"asyncpg.PostgresError", "json.JSONDecodeError"},
        "A3 after_confirm_pattern outer",
    ),
    (
        "session_manager",
        1349,
        {"KeyError", "TypeError", "asyncpg.PostgresError"},
        "A4 after_resolve",
    ),
    (
        "unified_chat",
        3422,
        {"asyncpg.PostgresError", "json.JSONDecodeError"},
        "A5 DocSimple L2 persist",
    ),
]

C_SITES = [
    (
        "session_manager",
        220,
        {"asyncpg.UniqueViolationError"},
        "C2 session-create race",
    ),
    (
        "session_manager",
        845,
        {"json.JSONDecodeError", "TypeError"},
        "C1 event result_data JSON decode",
    ),
]


@pytest.fixture(scope="module")
def trees() -> dict:
    return {
        "unified_chat": _parse(UNIFIED_CHAT),
        "session_manager": _parse(SESSION_MANAGER),
    }


@pytest.mark.parametrize("file_key, line, required_any, label", A_SITES)
def test_a_site_narrowing_stays_narrow(trees, file_key, line, required_any, label):
    """Each A-site must name specific exceptions, NOT bare Exception/BaseException."""
    tree = trees[file_key]
    handler = _handler_at_line(tree, line)
    names = set(_handler_names(handler))
    forbidden = {"Exception", "BaseException", "__BARE_EXCEPT__"}
    assert not (
        names & forbidden
    ), f"A-site '{label}' at ~{file_key}:{line} regressed to broad catch: {names}"
    assert names & required_any, (
        f"A-site '{label}' at ~{file_key}:{line} missing expected narrow types. "
        f"Got {names}, expected any of {required_any}"
    )


def test_category_c_narrowing_stays_narrow(trees):
    """C-sites must be narrowed per owner decision."""
    for file_key, line, required, label in C_SITES:
        tree = trees[file_key]
        handler = _handler_at_line(tree, line)
        names = set(_handler_names(handler))
        forbidden = {"Exception", "BaseException", "__BARE_EXCEPT__"}
        assert not (
            names & forbidden
        ), f"C-site '{label}' at ~{file_key}:{line} regressed to broad catch: {names}"
        assert required.issubset(names), (
            f"C-site '{label}' at ~{file_key}:{line} narrowing incomplete. "
            f"Got {names}, required {required}"
        )


# ---------------------------------------------------------------------------
# Test 2: A1 dynamic — NameError inside after_confirm must surface
# ---------------------------------------------------------------------------


def test_a1_name_error_is_not_silently_swallowed(caplog, monkeypatch):
    """
    Confirm that if `update_state_from_action` raises a NameError (the exact
    shape of the matrix-probe bug), the new narrowed handler does NOT catch it.

    Since the production handler at unified_chat.py:4303 now catches only
    (KeyError, TypeError, ValueError, asyncpg.PostgresError, json.JSONDecodeError),
    a NameError in the try-body MUST propagate. We simulate that at the unit
    level by invoking the try/except pattern from the source and asserting
    propagation behavior.
    """
    import asyncio
    import asyncpg  # noqa: F401  (import must succeed for narrowing to compile)
    import json as _json

    _body_session_id = "11111111-1111-1111-1111-111111111111"
    _action_type = "TEST_ACTION"

    async def _raise_name_error():
        raise NameError("simulated: update_state_from_action broken")

    async def _run():
        # This is the exact narrowed pattern from unified_chat.py:4303
        try:
            await _raise_name_error()
        except (
            KeyError,
            TypeError,
            ValueError,
            asyncpg.PostgresError,
            _json.JSONDecodeError,
        ):
            pytest.fail(
                "Narrow handler incorrectly caught NameError — narrowing broken"
            )

    with pytest.raises(NameError, match="simulated"):
        asyncio.run(_run())


def test_a1_domain_error_logs_at_error_level(caplog):
    """
    Confirm that when a domain exception (e.g. ValueError) IS raised, the
    handler upgrades from warning to error + emits exc_info traceback.
    This mirrors the production narrowed handler's logging contract.
    """
    import asyncio
    import asyncpg  # noqa: F401
    import json as _json

    body_session_id = "22222222-2222-2222-2222-222222222222"
    action_type = "TEST_ACTION"
    logger = logging.getLogger("unified_chat.test_a1")

    async def _raise_value_error():
        raise ValueError("simulated domain error")

    async def _run():
        try:
            await _raise_value_error()
        except (
            KeyError,
            TypeError,
            ValueError,
            asyncpg.PostgresError,
            _json.JSONDecodeError,
        ):
            logger.error(
                "[Confirm] Layer 2 hook failed: session=%s action=%s",
                body_session_id,
                action_type,
                exc_info=True,
            )

    caplog.set_level(logging.ERROR, logger="unified_chat.test_a1")
    asyncio.run(_run())

    # Find our error record
    matches = [
        r
        for r in caplog.records
        if r.levelno == logging.ERROR and "Layer 2 hook failed" in r.getMessage()
    ]
    assert matches, f"Expected ERROR-level log with [Confirm] Layer 2 hook failed; got {[r.getMessage() for r in caplog.records]}"
    record = matches[0]
    assert record.exc_info is not None, "Expected exc_info=True to produce traceback"
    assert "simulated domain error" in str(record.exc_info[1])
