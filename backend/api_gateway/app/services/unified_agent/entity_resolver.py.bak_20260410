"""
Entity Resolver — Compiler Pipeline Stage 2.

Resolves extracted entity names to database IDs.
Code-driven: parallel DB queries, fuzzy matching, clarification generation.
Zero LLM calls.

CRITICAL: Check DB column names before writing queries.
customers.nama (NOT name!), products.nama_produk (NOT name!),
customers.id = varchar (NOT uuid!), bank_accounts.coa_id (NOT chart_of_account_id!).
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Optional
from datetime import date

logger = logging.getLogger("unified_agent.entity_resolver")


@dataclass
class ResolvedEntity:
    """Single resolved entity."""
    entity_type: str
    entity_id: str
    entity_name: str
    confidence: float
    candidates: list = field(default_factory=list)


@dataclass
class ResolutionResult:
    """Result of entity resolution."""
    resolved: dict = field(default_factory=dict)
    missing: list = field(default_factory=list)
    clarifications: list = field(default_factory=list)
    needs_clarification: bool = False
    payload: dict = field(default_factory=dict)


class EntityResolver:
    """Resolve entity names to DB IDs and construct final payload."""

    def __init__(self, db_pool, tenant_id: str):
        self.db = db_pool
        self.tenant_id = tenant_id

    async def resolve_and_complete(
        self,
        intent: str,
        entities: dict,
        modifiers: list = None,
        memory_state: dict = None,
        system_defaults: dict = None,
        entity_graph: dict = None,
        action_memory_suggestion: dict = None,
    ) -> ResolutionResult:
        modifiers = modifiers or []
        memory_state = memory_state or {}
        system_defaults = system_defaults or {"date": date.today().isoformat()}

        result = ResolutionResult()

        # Step A: Resolve extracted entities (parallel DB queries)
        resolve_tasks = []
        if entities.get("customer_name"):
            resolve_tasks.append(self._resolve_customer(entities["customer_name"]))
        if entities.get("vendor_name"):
            resolve_tasks.append(self._resolve_vendor(entities["vendor_name"]))
        if entities.get("item_name") and not intent.startswith("create_item"):
            resolve_tasks.append(self._resolve_item(entities["item_name"]))
        if entities.get("bank_name"):
            resolve_tasks.append(self._resolve_bank_account(entities["bank_name"]))
        if entities.get("warehouse_name"):
            resolve_tasks.append(self._resolve_warehouse(entities["warehouse_name"]))
        if entities.get("invoice_number"):
            resolve_tasks.append(self._resolve_invoice(entities["invoice_number"]))
        if entities.get("bill_number"):
            resolve_tasks.append(self._resolve_bill(entities["bill_number"]))

        if resolve_tasks:
            resolved_entities = await asyncio.gather(*resolve_tasks, return_exceptions=True)
            for res in resolved_entities:
                if isinstance(res, Exception):
                    logger.warning("[RESOLVE] Entity resolution failed: %s", res)
                    continue
                if res is None:
                    continue
                if res.confidence >= 0.8 and len(res.candidates) <= 1:
                    result.resolved[res.entity_type] = res
                elif len(res.candidates) > 1:
                    result.resolved[res.entity_type] = res
                    candidates_str = ", ".join(
                        f"{i+1}) {c['name']}" for i, c in enumerate(res.candidates[:5])
                    )
                    result.clarifications.append(
                        f"Saya temukan {len(res.candidates)} {res.entity_type}: {candidates_str}. Yang mana?"
                    )
                    result.needs_clarification = True
                elif res.confidence < 0.5:
                    result.missing.append(res.entity_type)

        # Step A.5: Graph-based resolution for implicit references
        if entity_graph:
            from .entity_graph import get_last_node, get_focus, _ensure_graph
            graph = _ensure_graph(entity_graph)
            if not entities.get("customer_name") and "customer" not in result.resolved:
                focus = get_focus(graph)
                if focus and focus.get("type") == "customer":
                    result.resolved["customer"] = ResolvedEntity(
                        entity_type="customer", entity_id=focus["id"],
                        entity_name=focus["name"], confidence=0.9,
                    )
                else:
                    last_cust = get_last_node(graph, "customer")
                    if last_cust:
                        result.resolved["customer"] = ResolvedEntity(
                            entity_type="customer", entity_id=last_cust["id"],
                            entity_name=last_cust["name"], confidence=0.85,
                        )
            if not entities.get("vendor_name") and "vendor" not in result.resolved:
                last_vendor = get_last_node(graph, "vendor") if graph.get("nodes") else None
                if last_vendor:
                    result.resolved["vendor"] = ResolvedEntity(
                        entity_type="vendor", entity_id=last_vendor["id"],
                        entity_name=last_vendor["name"], confidence=0.85,
                    )

        # Step B: Complete from memory + defaults (3-source merge)
        result.payload = self._build_payload(
            intent, entities, result.resolved, memory_state, system_defaults,
            action_memory_suggestion=action_memory_suggestion,
        )

        # Step C: Check required fields
        from .direct_action_registry import get_direct_action, validate_payload
        config = get_direct_action(intent)
        if config:
            is_valid, missing_fields = validate_payload(intent, result.payload)
            if not is_valid:
                result.missing.extend(missing_fields)
                if not result.needs_clarification:
                    field_labels = []
                    for f in config.fields:
                        if f.name in missing_fields:
                            field_labels.append(f.label)
                    if field_labels:
                        labels_str = ", ".join(field_labels)
                        result.clarifications.append(
                            f"Mohon lengkapi: {labels_str}"
                        )
                        result.needs_clarification = True

        return result

    def _build_payload(self, intent, entities, resolved, memory_state, system_defaults, action_memory_suggestion=None):
        payload = {}

        # Source 1: Resolved entities -> inject IDs + display names
        if "customer" in resolved:
            r = resolved["customer"]
            payload["customer_id"] = r.entity_id
            payload["customer_name"] = r.entity_name
        if "vendor" in resolved:
            r = resolved["vendor"]
            payload["vendor_id"] = r.entity_id
            payload["vendor_name"] = r.entity_name
        if "item" in resolved:
            r = resolved["item"]
            payload["item_id"] = r.entity_id
            payload["item_name"] = r.entity_name
        if "bank_account" in resolved:
            r = resolved["bank_account"]
            payload["bank_account_id"] = r.entity_id
            payload["bank_account_name"] = r.entity_name
        if "warehouse" in resolved:
            r = resolved["warehouse"]
            payload["warehouse_id"] = r.entity_id
            payload["warehouse_name"] = r.entity_name
        if "invoice" in resolved:
            r = resolved["invoice"]
            payload["invoice_id"] = r.entity_id
            payload["invoice_number"] = r.entity_name
        if "bill" in resolved:
            r = resolved["bill"]
            payload["bill_id"] = r.entity_id
            payload["bill_number"] = r.entity_name

        # Intent-specific: map resolved names to registry field names
        if intent == "create_customer" and "customer" in resolved:
            payload.setdefault("name", resolved["customer"].entity_name)
        elif intent == "create_customer" and entities.get("customer_name"):
            payload.setdefault("name", entities["customer_name"])
        if intent == "create_vendor" and "vendor" in resolved:
            payload.setdefault("name", resolved["vendor"].entity_name)
        elif intent == "create_vendor" and entities.get("vendor_name"):
            payload.setdefault("name", entities["vendor_name"])
        if intent == "create_item" and entities.get("item_name"):
            payload.setdefault("name", entities["item_name"])
        if intent == "create_warehouse" and "warehouse" in resolved:
            payload.setdefault("name", resolved["warehouse"].entity_name)
        elif intent == "create_warehouse" and entities.get("warehouse_name"):
            payload.setdefault("name", entities["warehouse_name"])
        if intent == "create_bank_account" and entities.get("bank_name"):
            payload.setdefault("account_name", entities["bank_name"])

        # Source 1: Direct entity values (non-relational)
        direct_fields = [
            "amount", "quantity", "unit_price", "description", "date",
            "phone", "email", "address", "reason", "name",
            "account_type", "payment_method", "item_type", "base_unit",
        ]
        for field_name in direct_fields:
            if entities.get(field_name) is not None:
                payload[field_name] = entities[field_name]

        # Registry-aware field injection (Stage 2 extracts exact registry names)
        from .direct_action_registry import get_direct_action
        _config = get_direct_action(intent)
        if _config:
            _registry_names = {f.name for f in _config.fields}
            for key, value in entities.items():
                if key in _registry_names and value is not None and key not in payload:
                    payload[key] = value

        # Source 2: Memory state (fill gaps only)
        if memory_state:
            if "customer_id" not in payload and memory_state.get("active_customer_id"):
                payload["customer_id"] = memory_state["active_customer_id"]
                payload.setdefault("customer_name", memory_state.get("active_customer_name", ""))
            if "vendor_id" not in payload and memory_state.get("active_vendor_id"):
                payload["vendor_id"] = memory_state["active_vendor_id"]
                payload.setdefault("vendor_name", memory_state.get("active_vendor_name", ""))
            if "invoice_id" not in payload and memory_state.get("active_invoice_id"):
                payload["invoice_id"] = memory_state["active_invoice_id"]
                payload.setdefault("invoice_number", memory_state.get("active_invoice_number", ""))
            if "bill_id" not in payload and memory_state.get("active_bill_id"):
                payload["bill_id"] = memory_state["active_bill_id"]
                payload.setdefault("bill_number", memory_state.get("active_bill_number", ""))

        # Source 2.5: Action Memory pattern (fill items/tax from learned patterns)
        if action_memory_suggestion and action_memory_suggestion.get("pattern"):
            pattern = action_memory_suggestion["pattern"]
            if "items" not in payload and pattern.get("items"):
                payload["items"] = [
                    {
                        "item_id": pi.get("item_id", ""),
                        "description": pi.get("name", ""),
                        "quantity": pi.get("last_qty", 1),
                        "unit_price": pi.get("last_price", 0),
                    }
                    for pi in pattern["items"]
                ]
            if "tax_rate" not in payload and pattern.get("tax_rate") is not None:
                payload["tax_rate"] = pattern["tax_rate"]
            if "bank_account_id" not in payload and pattern.get("bank_account_id"):
                payload["bank_account_id"] = pattern["bank_account_id"]
                payload.setdefault("bank_account_name", pattern.get("bank_account_name", ""))
            if "account_id" not in payload and pattern.get("account_id"):
                payload["account_id"] = pattern["account_id"]
                payload.setdefault("account_name", pattern.get("account_name", ""))

        # Source 3: System defaults
        for key, value in system_defaults.items():
            payload.setdefault(key, value)

        # ── Intent-specific payload construction (Tahap 2b) ──────────

        # receive_payment: needs allocations array from resolved invoice
        if intent == "create_receive_payment" and "invoice" in resolved:
            inv = resolved["invoice"]
            amount = entities.get("amount")
            if amount and "allocations" not in payload:
                payload["allocations"] = [{
                    "invoice_id": inv.entity_id,
                    "invoice_number": inv.entity_name,
                    "amount": amount,
                }]

        # bill_payment: needs allocations array from resolved bill
        elif intent == "create_bill_payment" and "bill" in resolved:
            bill = resolved["bill"]
            amount = entities.get("amount")
            if amount and "allocations" not in payload:
                payload["allocations"] = [{
                    "bill_id": bill.entity_id,
                    "bill_number": bill.entity_name,
                    "amount": amount,
                }]

        # sales_invoice: needs items array from resolved item
        elif intent == "create_sales_invoice" and "item" in resolved:
            item = resolved["item"]
            if "items" not in payload:
                qty = entities.get("quantity", 1)
                price = entities.get("unit_price", 0)
                payload["items"] = [{
                    "item_id": item.entity_id,
                    "description": item.entity_name,
                    "quantity": qty,
                    "unit_price": price,
                }]

        # bill (faktur pembelian): needs items array, field names differ
        elif intent == "create_bill" and "item" in resolved:
            item = resolved["item"]
            if "items" not in payload:
                qty = entities.get("quantity", 1)
                price = entities.get("unit_price", 0)
                payload["items"] = [{
                    "item_id": item.entity_id,
                    "item_name": item.entity_name,
                    "quantity": qty,
                    "unit_price": price,
                }]

        # expense: map bank_account_id to paid_through_id
        elif intent == "create_expense":
            if "bank_account" in resolved and "paid_through_id" not in payload:
                payload["paid_through_id"] = resolved["bank_account"].entity_id
                payload["paid_through_name"] = resolved["bank_account"].entity_name

        # Intent-specific date mapping
        if "date" not in payload:
            payload["date"] = system_defaults.get("date", "")
        if intent.startswith("create_") and intent not in (
            "create_customer", "create_vendor", "create_warehouse",
            "create_bank_account", "create_item"
        ):
            date_val = payload.pop("date", system_defaults.get("date", ""))
            if date_val:
                if "payment" in intent:
                    payload.setdefault("payment_date", date_val)
                elif "invoice" in intent or "bill" in intent:
                    payload.setdefault("invoice_date", date_val)
                elif "expense" in intent:
                    payload.setdefault("expense_date", date_val)
                elif "journal" in intent:
                    payload.setdefault("entry_date", date_val)

        return payload

    # Individual Entity Resolvers

    async def _resolve_customer(self, name_fragment: str) -> Optional[ResolvedEntity]:
        """customers.id = VARCHAR, column = nama (Bahasa!)"""
        try:
            rows = await self.db.fetch(
                """SELECT id, nama, telepon, email
                   FROM customers
                   WHERE tenant_id = $1 AND is_active = true
                     AND nama ILIKE $2
                   ORDER BY total_transaksi DESC NULLS LAST
                   LIMIT 5""",
                self.tenant_id, f"%{name_fragment}%"
            )
            if not rows:
                return ResolvedEntity(entity_type="customer", entity_id="", entity_name=name_fragment, confidence=0.0)
            candidates = [{"id": str(r["id"]), "name": r["nama"]} for r in rows]
            best = candidates[0]
            confidence = 1.0 if len(candidates) == 1 else 0.7
            for c in candidates:
                if c["name"].lower().strip() == name_fragment.lower().strip():
                    best = c
                    confidence = 1.0
                    break
            return ResolvedEntity(entity_type="customer", entity_id=best["id"], entity_name=best["name"], confidence=confidence, candidates=candidates)
        except Exception as e:
            logger.warning("[RESOLVE] Customer lookup failed: %s", e)
            return None

    async def _resolve_vendor(self, name_fragment: str) -> Optional[ResolvedEntity]:
        try:
            rows = await self.db.fetch(
                """SELECT id, name FROM vendors
                   WHERE tenant_id = $1 AND is_active = true AND name ILIKE $2
                   ORDER BY name LIMIT 5""",
                self.tenant_id, f"%{name_fragment}%"
            )
            if not rows:
                return ResolvedEntity(entity_type="vendor", entity_id="", entity_name=name_fragment, confidence=0.0)
            candidates = [{"id": str(r["id"]), "name": r["name"]} for r in rows]
            best = candidates[0]
            confidence = 1.0 if len(candidates) == 1 else 0.7
            for c in candidates:
                if c["name"].lower().strip() == name_fragment.lower().strip():
                    best = c
                    confidence = 1.0
                    break
            return ResolvedEntity(entity_type="vendor", entity_id=best["id"], entity_name=best["name"], confidence=confidence, candidates=candidates)
        except Exception as e:
            logger.warning("[RESOLVE] Vendor lookup failed: %s", e)
            return None

    async def _resolve_item(self, name_fragment: str) -> Optional[ResolvedEntity]:
        """products.nama_produk (Bahasa!) — with fuzzy fallback for typos."""
        try:
            search_term = name_fragment.strip()

            # Step 1: Exact ILIKE match (full name)
            rows = await self.db.fetch(
                """SELECT id, nama_produk, sales_price_amount, purchase_price_amount, item_type
                   FROM products
                   WHERE tenant_id = $1 AND status = 'active'
                     AND (nama_produk ILIKE $2 OR item_code ILIKE $2 OR sku ILIKE $2)
                   ORDER BY nama_produk LIMIT 5""",
                self.tenant_id, f"%{search_term}%"
            )

            # Step 2: Fallback first word ILIKE
            if not rows and len(name_fragment.split()) > 1:
                search_term = name_fragment.split()[0]
                rows = await self.db.fetch(
                    """SELECT id, nama_produk, sales_price_amount, purchase_price_amount, item_type
                       FROM products
                       WHERE tenant_id = $1 AND status = 'active'
                         AND (nama_produk ILIKE $2 OR item_code ILIKE $2 OR sku ILIKE $2)
                       ORDER BY nama_produk LIMIT 5""",
                    self.tenant_id, f"%{search_term}%"
                )

            # Step 3: Fuzzy match via pg_trgm (handles typos like "obyat" -> "obat")
            if not rows:
                rows = await self.db.fetch(
                    """SELECT id, nama_produk, sales_price_amount, purchase_price_amount, item_type,
                              similarity(nama_produk, $2) AS sim
                       FROM products
                       WHERE tenant_id = $1 AND status = 'active'
                         AND similarity(nama_produk, $2) > 0.15
                       ORDER BY sim DESC LIMIT 5""",
                    self.tenant_id, name_fragment.strip()
                )
            if not rows:
                return ResolvedEntity(entity_type="item", entity_id="", entity_name=name_fragment, confidence=0.0)
            candidates = [{"id": str(r["id"]), "name": r["nama_produk"]} for r in rows]
            best = candidates[0]
            confidence = 1.0 if len(candidates) == 1 else 0.7
            for c in candidates:
                if c["name"].lower().strip() == name_fragment.lower().strip():
                    best = c
                    confidence = 1.0
                    break
            return ResolvedEntity(entity_type="item", entity_id=best["id"], entity_name=best["name"], confidence=confidence, candidates=candidates)
        except Exception as e:
            logger.warning("[RESOLVE] Item lookup failed: %s", e)
            return None

    async def _resolve_bank_account(self, name_fragment: str) -> Optional[ResolvedEntity]:
        try:
            rows = await self.db.fetch(
                """SELECT id, account_name, bank_name, coa_id
                   FROM bank_accounts
                   WHERE tenant_id = $1 AND is_active = true
                     AND (account_name ILIKE $2 OR bank_name ILIKE $2)
                   ORDER BY account_name LIMIT 5""",
                self.tenant_id, f"%{name_fragment}%"
            )
            if not rows:
                return ResolvedEntity(entity_type="bank_account", entity_id="", entity_name=name_fragment, confidence=0.0)
            candidates = [{"id": str(r["id"]), "name": r["account_name"]} for r in rows]
            best = candidates[0]
            confidence = 1.0 if len(candidates) == 1 else 0.7
            return ResolvedEntity(entity_type="bank_account", entity_id=best["id"], entity_name=best["name"], confidence=confidence, candidates=candidates)
        except Exception as e:
            logger.warning("[RESOLVE] Bank account lookup failed: %s", e)
            return None

    async def _resolve_warehouse(self, name_fragment: str) -> Optional[ResolvedEntity]:
        try:
            rows = await self.db.fetch(
                """SELECT id, name FROM warehouses
                   WHERE tenant_id = $1 AND name ILIKE $2
                   ORDER BY name LIMIT 5""",
                self.tenant_id, f"%{name_fragment}%"
            )
            if not rows:
                return ResolvedEntity(entity_type="warehouse", entity_id="", entity_name=name_fragment, confidence=0.0)
            candidates = [{"id": str(r["id"]), "name": r["name"]} for r in rows]
            best = candidates[0]
            confidence = 1.0 if len(candidates) == 1 else 0.7
            return ResolvedEntity(entity_type="warehouse", entity_id=best["id"], entity_name=best["name"], confidence=confidence, candidates=candidates)
        except Exception as e:
            logger.warning("[RESOLVE] Warehouse lookup failed: %s", e)
            return None

    async def _resolve_invoice(self, invoice_number: str) -> Optional[ResolvedEntity]:
        try:
            rows = await self.db.fetch(
                """SELECT id, invoice_number, customer_id, status
                   FROM sales_invoices
                   WHERE tenant_id = $1 AND invoice_number ILIKE $2
                   ORDER BY created_at DESC LIMIT 5""",
                self.tenant_id, f"%{invoice_number}%"
            )
            if not rows:
                return ResolvedEntity(entity_type="invoice", entity_id="", entity_name=invoice_number, confidence=0.0)
            candidates = [{"id": str(r["id"]), "name": r["invoice_number"]} for r in rows]
            best = candidates[0]
            confidence = 1.0 if len(candidates) == 1 else 0.7
            return ResolvedEntity(entity_type="invoice", entity_id=best["id"], entity_name=best["name"], confidence=confidence, candidates=candidates)
        except Exception as e:
            logger.warning("[RESOLVE] Invoice lookup failed: %s", e)
            return None

    async def _resolve_bill(self, bill_number: str) -> Optional[ResolvedEntity]:
        """Column = invoice_number (legacy naming)."""
        try:
            rows = await self.db.fetch(
                """SELECT id, invoice_number, vendor_id, vendor_name, status
                   FROM bills
                   WHERE tenant_id = $1 AND invoice_number ILIKE $2
                   ORDER BY created_at DESC LIMIT 5""",
                self.tenant_id, f"%{bill_number}%"
            )
            if not rows:
                return ResolvedEntity(entity_type="bill", entity_id="", entity_name=bill_number, confidence=0.0)
            candidates = [{"id": str(r["id"]), "name": r["invoice_number"]} for r in rows]
            best = candidates[0]
            confidence = 1.0 if len(candidates) == 1 else 0.7
            return ResolvedEntity(entity_type="bill", entity_id=best["id"], entity_name=best["name"], confidence=confidence, candidates=candidates)
        except Exception as e:
            logger.warning("[RESOLVE] Bill lookup failed: %s", e)
            return None
