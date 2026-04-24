"""
Hook gates for unified_agent StateUpdateHooks.

Defines deny lists controlling when pipeline hooks (after_resolve,
after_confirm, etc.) fire vs skip. Extracted from session_manager.py
to keep that file's line numbers stable for AST-based regression tests
(see tests/chat/test_bucket0_silent_failure_regression.py).

Design principles
-----------------
- DENY list (not allow list): new intents added post-Batch-2 fail-open
  into graph writes. Over-writing is recoverable; under-writing is
  invisible. See Batch 2 plan (2026-04-24) Risk Flag 2.
- EXACT string match (not regex): LLM router / entity extractor emit
  exact lowercase intent strings (e.g. "chitchat", "query_bom_list").
  Regex would risk both miss and over-match.
- Observability: callers MUST emit a structured skip log on every
  deny-list hit. Silent skips are the same class of bug Bucket 0 fixed.
"""
from __future__ import annotations


# Bucket A2 — after_resolve intent deny list.
#
# Enumeration source (2026-04-24 — grepped across app/services/unified_agent/):
#   - "chitchat" / "ambiguous" / "unknown" : entity_extractor.py fallback intents
#   - query_bom_* / query_work_* / query_production_* / query_material_issues /
#     query_fg_receipts / query_work_center_list : orchestrator.py _MFG_INTENTS
#     (manufacturing owns its own resolution pipeline — MFG_RESOLVE_WO / BOM)
#   - "reformat_as_table" : pure-formatting turn, no entity resolution
AFTER_RESOLVE_DENY_LIST: frozenset = frozenset(
    {
        # Chitchat / ambiguous / unknown — no entities to graph
        "chitchat",
        "ambiguous",
        "unknown",
        "",
        # Manufacturing — has its own resolution pipeline
        "query_bom_list",
        "query_bom_detail",
        "query_bom_cost_breakdown",
        "query_bom_materials_required",
        "query_work_order_list",
        "query_work_order_detail",
        "query_work_order_cost_analysis",
        "query_production_active",
        "query_production_schedule",
        "query_material_issues",
        "query_fg_receipts",
        "query_work_center_list",
        # Reformat — no entity resolution on pure reformat turns
        "reformat_as_table",
    }
)
