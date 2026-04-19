"""
EntityContextManager — Convention-driven multi-entity context tracking.

Phase 1 of multi-turn context architecture.
Replaces single-entity, manual-whitelist session state with:
- Convention-based entity extraction (suffix patterns, zero whitelist)
- Multi-entity stack per type (defaultdict, dynamic types)
- Ordinal resolution for list results
- Telemetry for new entity types

Design principles:
1. Convention over configuration — entity types derived from field names
2. Zero whitelist — new tools/modules auto-supported
3. Schema-derived needs — intent→entity mapping from tool params
4. Context-driven pronoun resolution — type inferred from intent needs
"""

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("unified_agent.ecm")

# ── Convention patterns ──
# Entity type derived from field name suffix, not hardcoded mapping.
# "customer_id" → "customer", "work_order_id" → "work_order"
ENTITY_SUFFIX_PATTERNS = ("_id", "_number", "_code")
NAME_KEYS = ("name", "nama", "title", "display_name")

# Keys to skip — these have _id suffix but aren't entity references
SKIP_KEYS = frozenset({
    "tenant_id", "user_id", "created_by_id", "updated_by_id",
    "session_id", "conversation_id", "trace_id", "message_id",
    "device_id", "token_id", "pending_action_id",
})

# Max entities per type in stack
MAX_STACK_SIZE = 10

# Max items stored for ordinal resolution
MAX_LIST_ITEMS = 10


@dataclass
class Entity:
    """Single tracked entity with provenance."""
    type: str                           # "customer", "vendor", "invoice", etc.
    id: Optional[str] = None            # Primary UUID
    number: Optional[str] = None        # Business number (INV-001, WO-001)
    name: Optional[str] = None          # Display name
    source: str = ""                    # Tool that produced this entity
    turn: int = 0                       # Session turn when extracted
    raw: dict = field(default_factory=dict)  # Safe subset of original fields


class EntityContextManager:
    """Convention-driven multi-entity context tracker.

    Shadow mode (Phase 1): ingest after every tool call, log extractions.
    Does NOT output to LLM context yet — old session state remains source of truth.
    """

    def __init__(self):
        # Dynamic entity stacks — types auto-created on first encounter
        self.entity_stack: dict[str, list[Entity]] = defaultdict(list)
        # Last list result for ordinal resolution ("yang pertama", "yang kedua")
        self.last_list_result: list[Entity] = []
        # Domain tracking for context switches
        self.last_domain: Optional[str] = None
        # Turn counter
        self.current_turn: int = 0

    # ── Core: Ingest ──

    def ingest_tool_result(self, tool_name: str, params: dict, result) -> list[Entity]:
        """Auto-extract entities from ANY tool result. Zero whitelist.

        Convention-based: looks for fields ending in _id, _number, _code
        and derives entity type from the prefix.

        Returns list of extracted entities for telemetry.
        """
        from .tool_executor import normalize_api_response

        # Normalize response to list
        items = normalize_api_response(result) if result else []
        if not isinstance(items, list):
            items = [items] if items else []

        extracted: list[Entity] = []

        for item in items[:MAX_LIST_ITEMS]:
            if not isinstance(item, dict):
                continue
            entities = self._extract_entities_from_dict(item, tool_name)
            for entity in entities:
                self.push_entity(entity)
                extracted.append(entity)

        # Store for ordinal resolution if list result
        if len(items) > 1 and extracted:
            # Deduplicate by type — keep first entity per type for ordinal
            seen_ids = set()
            ordinal_list = []
            for e in extracted:
                key = (e.type, e.id or e.name)
                if key not in seen_ids:
                    seen_ids.add(key)
                    ordinal_list.append(e)
            self.last_list_result = ordinal_list[:MAX_LIST_ITEMS]

        # Also extract from params (tool input may reference entities)
        param_entities = self._extract_entities_from_dict(params, f"{tool_name}:params")
        for pe in param_entities:
            self.push_entity(pe)

        if extracted:
            types_summary = {}
            for e in extracted:
                types_summary[e.type] = types_summary.get(e.type, 0) + 1
            logger.warning(
                "[ECM] Ingested %d entities from %s: %s",
                len(extracted), tool_name, types_summary,
            )

        return extracted

    def _extract_entities_from_dict(self, item: dict, source: str) -> list[Entity]:
        """Extract entities via CONVENTION, not whitelist.

        "customer_id" → entity_type = "customer"
        "work_order_id" → entity_type = "work_order" (auto, no registration)
        "invoice_number" → entity_type = "invoice"
        """
        entities = []
        seen_types: set[str] = set()

        for key, value in item.items():
            if not value or key in SKIP_KEYS:
                continue

            for suffix in ENTITY_SUFFIX_PATTERNS:
                if not key.endswith(suffix):
                    continue
                if key == "id":
                    # Bare "id" — skip, handled below
                    continue

                entity_type = key[: -len(suffix)]  # "customer_id" → "customer"
                if entity_type in seen_types:
                    break  # already extracted this type from this item
                seen_types.add(entity_type)

                # Find name in same dict
                name = None
                for name_suffix in ("_name", "_nama"):
                    name_key = f"{entity_type}{name_suffix}"
                    if name_key in item and item[name_key]:
                        name = str(item[name_key])
                        break

                entities.append(Entity(
                    type=entity_type,
                    id=str(value) if suffix == "_id" else item.get("id"),
                    number=str(value) if suffix in ("_number", "_code") else None,
                    name=name,
                    source=source,
                    turn=self.current_turn,
                ))
                break  # one suffix match per key

        return entities

    # ── Stack Management ──

    def push_entity(self, entity: Entity):
        """Push entity to stack. Log new types for observability."""
        is_new_type = entity.type not in self.entity_stack

        stack = self.entity_stack[entity.type]

        # Deduplicate: if same id already in stack, update instead of push
        if entity.id:
            for i, existing in enumerate(stack):
                if existing.id == entity.id:
                    stack[i] = entity
                    return

        stack.append(entity)

        # Cap stack size
        if len(stack) > MAX_STACK_SIZE:
            self.entity_stack[entity.type] = stack[-MAX_STACK_SIZE:]

        if is_new_type:
            logger.warning(
                "[ECM] New entity type discovered: %s (from %s)",
                entity.type, entity.source,
            )

    def get_most_recent(self, entity_type: str) -> Optional[Entity]:
        """Get most recent entity of given type."""
        stack = self.entity_stack.get(entity_type, [])
        return stack[-1] if stack else None

    def get_most_recent_any(self) -> Optional[Entity]:
        """Get most recent entity across all types."""
        best = None
        for stack in self.entity_stack.values():
            if stack:
                candidate = stack[-1]
                if best is None or candidate.turn > best.turn:
                    best = candidate
        return best

    # ── Context Output ──

    def get_context_for_llm(self) -> str:
        """Format active entities for LLM context injection.

        Returns concise string with top 5 most recent entities.
        Phase 1: NOT used yet (shadow mode). Phase 2 will activate.
        """
        # Collect most recent entity per type, sort by turn desc
        recents = []
        for etype, stack in self.entity_stack.items():
            if stack:
                recents.append(stack[-1])
        recents.sort(key=lambda e: e.turn, reverse=True)

        if not recents:
            return ""

        parts = []
        for entity in recents[:5]:
            label = entity.name or entity.number or entity.id or "?"
            parts.append(f"{entity.type}: {label}")

        return "Konteks aktif: " + ", ".join(parts)

    # ── Turn Management ──

    def advance_turn(self):
        """Increment turn counter. Call at start of each user message."""
        self.current_turn += 1

    # ── Diagnostics ──

    def get_stats(self) -> dict:
        """Return diagnostic info for logging/debugging."""
        return {
            "turn": self.current_turn,
            "entity_types": list(self.entity_stack.keys()),
            "stack_sizes": {k: len(v) for k, v in self.entity_stack.items()},
            "last_list_size": len(self.last_list_result),
            "context": self.get_context_for_llm(),
        }
