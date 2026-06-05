"""Phase 2 — query_business_drivers routing + 4-place registration tests.

Two concerns, both static / pure (no network, no DB):

1. CLASSIFIER (DRIVER_WHY_GATE): classify_query_intent() must map financial
   "why" questions -> query_business_drivers, leave non-financial "why" (which
   _infer_intent routes to TUTORIAL/RAG) untouched, and not disturb plain
   lookups.

2. 4-PLACE REGISTRATION: query_business_drivers must be present in all four
   single-source-of-truth places, or routing breaks silently (Bug C+G+I class):
     #1 direct_action_registry.py  (QueryActionConfig entry)
     #2 llm_intent_router.py       (ROUTER_SYSTEM_PROMPT intent enum)
     #3 entity_extractor.py        (LLM extractor intent enum)
     #4 entity_extractor.py        (PIPELINE_ENABLED_INTENTS set)

Run (bypassing the auth-scoped coverage gate in pytest.ini):
    pytest tests/chat/test_business_drivers_routing.py -o addopts="" \
        -p no:cacheprovider -v
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.services.unified_agent.entity_extractor import classify_query_intent


# --------------------------------------------------------------------------- #
# 1. Classifier table (DRIVER_WHY_GATE)
# --------------------------------------------------------------------------- #
# (utterance, expected intent or None). None = classify returns no match here,
# so it flows to _infer_intent (TUTORIAL for "kenapa", SIMPLE_READ otherwise).
_FINANCIAL_WHY = [
    ("kenapa cash flow seret bulan ini?", "query_business_drivers"),
    ("mengapa laba turun bulan ini?", "query_business_drivers"),
    ("kok arus kas minus ya", "query_business_drivers"),
    ("kenapa omzet turun", "query_business_drivers"),
    ("kenapa pengeluaran naik bulan ini", "query_business_drivers"),
    ("kenapa piutang membengkak", "query_business_drivers"),
    ("kenapa hutang saya naik terus", "query_business_drivers"),
    ("kenapa untung tipis bulan ini", "query_business_drivers"),
    ("kok beban operasional gede", "query_business_drivers"),
    ("ngapa kas berkurang banyak", "query_business_drivers"),
]

# Tutorial-why: "kenapa" WITHOUT a financial-driver noun -> classifier returns
# None here -> _infer_intent sends it to TUTORIAL (userguide RAG).
_TUTORIAL_WHY = [
    "kenapa faktur harus di-void",
    "kenapa stok bisa minus",
    "mengapa harus pakai jurnal penyesuaian",
    "kok aplikasi error ya",
]

# Plain lookups must be unaffected by the new gate.
_PLAIN_LOOKUPS = [
    "daftar pelanggan",
    "berapa saldo kas saya",  # no why-word
    "piutang siapa saja",
    "ringkasan penjualan bulan ini",
]


@pytest.mark.parametrize("text,expected", _FINANCIAL_WHY)
def test_financial_why_routes_to_business_drivers(text, expected):
    intent, _entity, _field = classify_query_intent(text)
    assert intent == expected, f"{text!r} -> {intent!r}, expected {expected!r}"


@pytest.mark.parametrize("text", _TUTORIAL_WHY)
def test_tutorial_why_not_captured_by_gate(text):
    # Must NOT be query_business_drivers (so it can fall through to TUTORIAL).
    intent, _entity, _field = classify_query_intent(text)
    assert intent != "query_business_drivers", (
        f"{text!r} wrongly captured as query_business_drivers; "
        "non-financial why must reach TUTORIAL"
    )


@pytest.mark.parametrize("text", _PLAIN_LOOKUPS)
def test_plain_lookups_not_business_drivers(text):
    intent, _entity, _field = classify_query_intent(text)
    assert (
        intent != "query_business_drivers"
    ), f"{text!r} wrongly captured as query_business_drivers"


# --------------------------------------------------------------------------- #
# 2. 4-place registration (grep-style static assertions)
# --------------------------------------------------------------------------- #
_THIS = Path(__file__).resolve()
_API_GATEWAY = _THIS.parents[2]
_UA = _API_GATEWAY / "app" / "services" / "unified_agent"
_REGISTRY = _UA / "direct_action_registry.py"
_ROUTER = _UA / "llm_intent_router.py"
_EXTRACTOR = _UA / "entity_extractor.py"

_INTENT = "query_business_drivers"


def test_place1_registry_query_action_config():
    src = _REGISTRY.read_text(encoding="utf-8")
    # Must be registered as a QueryActionConfig key (action_key=...).
    assert (
        f'"{_INTENT}": QueryActionConfig(' in src
    ), "missing registry QueryActionConfig"
    assert f'action_key="{_INTENT}"' in src, "missing action_key in registry entry"
    assert 'response_format="summary"' in src  # sanity: summary format used


def test_place2_router_prompt_enum():
    src = _ROUTER.read_text(encoding="utf-8")
    m = re.search(r'ROUTER_SYSTEM_PROMPT\s*=\s*"""(.*?)"""', src, re.DOTALL)
    assert m, "ROUTER_SYSTEM_PROMPT literal not found"
    assert _INTENT in m.group(1), "intent absent from ROUTER_SYSTEM_PROMPT"


def test_place3_extractor_intent_enum():
    src = _EXTRACTOR.read_text(encoding="utf-8")
    # The extractor intent enum lists the intent as a quoted string literal.
    # PIPELINE_ENABLED_INTENTS (place #4) also contains it, so require >= 2
    # occurrences to ensure BOTH the enum and the set carry it.
    occurrences = len(re.findall(rf'["\']{re.escape(_INTENT)}["\']', src))
    assert occurrences >= 2, (
        f"expected query_business_drivers in BOTH extractor enum and "
        f"PIPELINE_ENABLED_INTENTS (>=2 occurrences), found {occurrences}"
    )


def test_place4_pipeline_enabled_intents():
    src = _EXTRACTOR.read_text(encoding="utf-8")
    m = re.search(r"PIPELINE_ENABLED_INTENTS\s*=\s*\{(.*?)\n\}\s*\n", src, re.DOTALL)
    assert m, "PIPELINE_ENABLED_INTENTS literal not found"
    body = re.sub(r"#[^\n]*", "", m.group(1))  # strip comments
    members = set(re.findall(r"[\"']([a-zA-Z_][a-zA-Z0-9_]*)[\"']", body))
    assert _INTENT in members, "intent absent from PIPELINE_ENABLED_INTENTS"


def test_all_four_places_present():
    """Single roll-up assertion mirroring the 4-place single-source-of-truth."""
    reg = _REGISTRY.read_text(encoding="utf-8")
    rou = _ROUTER.read_text(encoding="utf-8")
    ext = _EXTRACTOR.read_text(encoding="utf-8")

    place1 = f'"{_INTENT}": QueryActionConfig(' in reg
    rou_prompt = re.search(r'ROUTER_SYSTEM_PROMPT\s*=\s*"""(.*?)"""', rou, re.DOTALL)
    place2 = bool(rou_prompt and _INTENT in rou_prompt.group(1))
    pei = re.search(r"PIPELINE_ENABLED_INTENTS\s*=\s*\{(.*?)\n\}\s*\n", ext, re.DOTALL)
    place4 = bool(pei and _INTENT in re.sub(r"#[^\n]*", "", pei.group(1)))
    # place3 = present in extractor source outside the PIPELINE set (the enum).
    place3 = len(re.findall(rf'["\']{re.escape(_INTENT)}["\']', ext)) >= 2

    missing = [
        name
        for name, ok in (
            ("#1 registry", place1),
            ("#2 router_prompt", place2),
            ("#3 extractor_enum", place3),
            ("#4 pipeline_enabled", place4),
        )
        if not ok
    ]
    assert not missing, f"query_business_drivers missing from: {missing}"
