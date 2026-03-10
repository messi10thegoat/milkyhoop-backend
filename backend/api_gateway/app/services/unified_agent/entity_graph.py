"""
Entity Graph — Relational entity tracking per chat session.

Stores relationships between entities mentioned in conversation.
Enables: "yang tadi", "hapus yang Router", "seperti yang kemarin".

RULES:
- Graph is READ from DB at start of turn, MODIFIED in memory, WRITTEN back at end
- LLM never touches graph — only backend hooks update it
- No financial amounts cached (Law 1) — only entity refs, names, structure
- Max 30 nodes per session (prevent unbounded growth)
"""

import time
import logging
from typing import Optional

logger = logging.getLogger("unified_agent.entity_graph")

MAX_NODES = 30


def _empty_graph():
    return {"nodes": {}, "edges": [], "focus": None, "counter": 0}


def _ensure_graph(graph):
    if not graph or not isinstance(graph, dict):
        return _empty_graph()
    graph.setdefault("nodes", {})
    graph.setdefault("edges", [])
    graph.setdefault("focus", None)
    graph.setdefault("counter", 0)
    return graph


def add_node(graph, entity_type, entity_id, name, **extra):
    graph = _ensure_graph(graph)
    for key, node in graph["nodes"].items():
        if node.get("type") == entity_type and node.get("id") == entity_id:
            node["name"] = name
            node["ts"] = int(time.time())
            node.update({k: v for k, v in extra.items() if v is not None})
            graph["focus"] = key
            return graph, key
    graph["counter"] += 1
    node_key = "n%d" % graph["counter"]
    graph["nodes"][node_key] = {
        "type": entity_type,
        "id": entity_id,
        "name": name,
        "ts": int(time.time()),
        **{k: v for k, v in extra.items() if v is not None},
    }
    graph["focus"] = node_key
    if len(graph["nodes"]) > MAX_NODES:
        _prune_oldest(graph)
    return graph, node_key


def add_edge(graph, from_key, to_key, rel):
    graph = _ensure_graph(graph)
    for edge in graph["edges"]:
        if edge["from"] == from_key and edge["to"] == to_key and edge["rel"] == rel:
            return graph
    graph["edges"].append({"from": from_key, "to": to_key, "rel": rel})
    return graph


def get_last_node(graph, entity_type):
    graph = _ensure_graph(graph)
    candidates = [
        (key, node) for key, node in graph["nodes"].items()
        if node.get("type") == entity_type
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[1].get("ts", 0), reverse=True)
    key, node = candidates[0]
    return {**node, "_key": key}


def get_node_by_id(graph, entity_type, entity_id):
    graph = _ensure_graph(graph)
    for key, node in graph["nodes"].items():
        if node.get("type") == entity_type and node.get("id") == entity_id:
            return {**node, "_key": key}
    return None


def get_focus(graph):
    graph = _ensure_graph(graph)
    focus_key = graph.get("focus")
    if focus_key and focus_key in graph["nodes"]:
        return {**graph["nodes"][focus_key], "_key": focus_key}
    return None


def get_neighbors(graph, node_key):
    graph = _ensure_graph(graph)
    neighbor_keys = set()
    for edge in graph["edges"]:
        if edge["from"] == node_key:
            neighbor_keys.add(edge["to"])
        elif edge["to"] == node_key:
            neighbor_keys.add(edge["from"])
    return [
        {**graph["nodes"][k], "_key": k}
        for k in neighbor_keys
        if k in graph["nodes"]
    ]


def remove_node(graph, node_key):
    graph = _ensure_graph(graph)
    if node_key in graph["nodes"]:
        del graph["nodes"][node_key]
    graph["edges"] = [
        e for e in graph["edges"]
        if e["from"] != node_key and e["to"] != node_key
    ]
    if graph["focus"] == node_key:
        graph["focus"] = None
    return graph


def find_node_by_name(graph, name_fragment, entity_type=None):
    graph = _ensure_graph(graph)
    fragment_lower = name_fragment.lower()
    candidates = []
    for key, node in graph["nodes"].items():
        if entity_type and node.get("type") != entity_type:
            continue
        node_name = (node.get("name") or "").lower()
        if fragment_lower in node_name or node_name in fragment_lower:
            candidates.append((key, node))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[1].get("ts", 0), reverse=True)
    key, node = candidates[0]
    return {**node, "_key": key}


def to_context_summary(graph):
    graph = _ensure_graph(graph)
    if not graph["nodes"]:
        return ""
    parts = []
    by_type = {}
    for key, node in graph["nodes"].items():
        t = node.get("type", "unknown")
        by_type.setdefault(t, []).append((key, node))
    for entity_type in ["customer", "vendor", "invoice", "bill", "item", "bank_account"]:
        nodes = by_type.get(entity_type, [])
        if not nodes:
            continue
        nodes.sort(key=lambda x: x[1].get("ts", 0), reverse=True)
        names = [n[1].get("name", "?") for n in nodes[:2]]
        count_extra = len(nodes) - 2
        summary = ", ".join(names)
        if count_extra > 0:
            summary += " (+%d)" % count_extra
        type_label = {
            "customer": "Customer", "vendor": "Vendor", "invoice": "Invoice",
            "bill": "Bill", "item": "Item", "bank_account": "Bank",
        }.get(entity_type, entity_type)
        parts.append("%s: %s" % (type_label, summary))
    focus = get_focus(graph)
    if focus:
        parts.append("Fokus: %s (%s)" % (focus.get("name", "?"), focus.get("type", "?")))
    if not parts:
        return ""
    return "## ENTITY GRAPH\n" + " | ".join(parts)


def _prune_oldest(graph):
    nodes_by_ts = sorted(
        graph["nodes"].items(),
        key=lambda x: x[1].get("ts", 0)
    )
    focus_key = graph.get("focus")
    while len(graph["nodes"]) > MAX_NODES:
        if not nodes_by_ts:
            break
        key, _ = nodes_by_ts.pop(0)
        if key == focus_key:
            continue
        remove_node(graph, key)
