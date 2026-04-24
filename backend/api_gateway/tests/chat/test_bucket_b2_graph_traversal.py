"""
Bucket B2 — Entity graph multi-hop traversal tests.

Scope: unit coverage for traverse() + get_by_ordinal() in entity_graph.py.
No DB, no gateway. Tests use synthetic graph dicts built via add_node/add_edge.

Guardrail 5 (added_by_intent intent-bleed check) is SKIPPED — the graph
node schema has no `added_by_intent` field. Flagged as follow-up ticket.
"""

import pytest

from app.services.unified_agent.entity_graph import (
    _empty_graph,
    add_node,
    add_edge,
    traverse,
    get_by_ordinal,
)


def _build_chain_graph():
    """customer -owns-> invoice -contains-> item (3-level chain)."""
    g = _empty_graph()
    g, cust_key = add_node(g, "customer", "cust-1", "Maju Jaya")
    g, inv_key = add_node(g, "invoice", "inv-1", "INV-0001")
    g, item_key = add_node(g, "item", "item-1", "Router")
    g = add_edge(g, cust_key, inv_key, "owns")
    g = add_edge(g, inv_key, item_key, "contains")
    return g, cust_key, inv_key, item_key


def test_traverse_returns_direct_neighbors():
    g = _empty_graph()
    g, cust_key = add_node(g, "customer", "cust-1", "Maju Jaya")
    g, inv_key = add_node(g, "invoice", "inv-1", "INV-0001")
    g = add_edge(g, cust_key, inv_key, "owns")

    hits = traverse(g, cust_key, max_depth=1)
    assert len(hits) == 1
    assert hits[0]["id"] == "inv-1"
    assert hits[0]["type"] == "invoice"


def test_traverse_depth_2():
    g, cust_key, inv_key, item_key = _build_chain_graph()

    d2 = traverse(g, cust_key, max_depth=2)
    ids = [n["id"] for n in d2]
    assert "inv-1" in ids
    assert "item-1" in ids
    # BFS nearest first
    assert ids.index("inv-1") < ids.index("item-1")

    d1 = traverse(g, cust_key, max_depth=1)
    assert [n["id"] for n in d1] == ["inv-1"]


def test_traverse_edge_type_filter():
    g = _empty_graph()
    g, cust_key = add_node(g, "customer", "cust-1", "Maju Jaya")
    g, inv_key = add_node(g, "invoice", "inv-1", "INV-0001")
    g, bank_key = add_node(g, "bank_account", "bank-1", "BCA")
    g = add_edge(g, cust_key, inv_key, "owns")
    g = add_edge(g, cust_key, bank_key, "paid_via")

    owns_only = traverse(g, cust_key, max_depth=1, edge_type="owns")
    assert [n["id"] for n in owns_only] == ["inv-1"]

    paid_only = traverse(g, cust_key, max_depth=1, edge_type="paid_via")
    assert [n["id"] for n in paid_only] == ["bank-1"]


def test_traverse_node_type_filter():
    g = _empty_graph()
    g, cust_key = add_node(g, "customer", "cust-1", "Maju Jaya")
    g, inv_key = add_node(g, "invoice", "inv-1", "INV-0001")
    g, item_key = add_node(g, "item", "item-1", "Router")
    g = add_edge(g, cust_key, inv_key, "owns")
    g = add_edge(g, cust_key, item_key, "owns")

    inv_only = traverse(g, cust_key, max_depth=1, node_type_filter="invoice")
    assert [n["id"] for n in inv_only] == ["inv-1"]


def test_traverse_empty_returns_empty_list():
    g = _empty_graph()
    assert traverse(g, "nonexistent", max_depth=1) == []
    # Also valid on None graph
    assert traverse(None, "nonexistent", max_depth=1) == []


def test_traverse_depth_capped_at_2():
    # 4-level chain: a -> b -> c -> d
    g = _empty_graph()
    g, a = add_node(g, "customer", "a", "A")
    g, b = add_node(g, "invoice", "b", "B")
    g, c = add_node(g, "item", "c", "C")
    g, d = add_node(g, "bank_account", "d", "D")
    g = add_edge(g, a, b, "owns")
    g = add_edge(g, b, c, "contains")
    g = add_edge(g, c, d, "paid_via")

    # Caller passes 5; traversal should still be limited to depth 2
    hits = traverse(g, a, max_depth=5)
    ids = [n["id"] for n in hits]
    assert "b" in ids and "c" in ids
    assert "d" not in ids, "depth-3 node must not be returned"


def test_get_by_ordinal_first_and_last():
    g = _empty_graph()
    g, _ = add_node(g, "customer", "c1", "Alpha")
    g, _ = add_node(g, "customer", "c2", "Beta")
    g, _ = add_node(g, "customer", "c3", "Gamma")

    first = get_by_ordinal(g, "customer", 1)
    assert first is not None and first["id"] == "c1"
    third = get_by_ordinal(g, "customer", 3)
    assert third is not None and third["id"] == "c3"
    missing = get_by_ordinal(g, "customer", 4)
    assert missing is None
    invalid = get_by_ordinal(g, "customer", 0)
    assert invalid is None


@pytest.mark.skip(
    reason="Live integration: requires gateway + tenant session; run manually via B2.5 smoke"
)
def test_live_pronoun_resolution_uses_graph():
    """B2.5 live smoke — not run in unit suite.

    Manual curl:
      1. POST /api/v3/chat "customer Maju Jaya hutangnya berapa?"
      2. POST /api/v3/chat (same session) "faktur terakhir dia"
    Assert gateway logs contain `graph_traverse type=direct_relation`.
    """
    pass
