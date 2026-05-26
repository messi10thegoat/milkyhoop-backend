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
from datetime import date, datetime

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
        user_text: str = "",
        session_id: str = "",
    ) -> ResolutionResult:
        modifiers = modifiers or []
        memory_state = memory_state or {}
        system_defaults = system_defaults or {"date": date.today().isoformat()}

        result = ResolutionResult()

        # Step A: Resolve extracted entities (parallel DB queries)
        # Skip entity resolution for create intents where fields are TEXT, not references.
        # e.g. create_vendor: vendor_name/bank_name are the new vendor info, not lookups.
        _skip_vendor_resolve = intent in ("create_vendor",)
        _skip_customer_resolve = intent in ("create_customer",)
        _skip_bank_resolve = intent in ("create_vendor", "create_customer")
        resolve_tasks = []
        if entities.get("customer_name") and not _skip_customer_resolve:
            resolve_tasks.append(self._resolve_customer(entities["customer_name"]))
        if entities.get("vendor_name") and not _skip_vendor_resolve:
            resolve_tasks.append(self._resolve_vendor(entities["vendor_name"]))
        if (
            entities.get("item_name")
            and not intent.startswith("create_item")
            and intent != "create_expense"
        ):
            resolve_tasks.append(self._resolve_item(entities["item_name"]))
        if entities.get("bank_name") and not _skip_bank_resolve:
            resolve_tasks.append(self._resolve_bank_account(entities["bank_name"]))
        if entities.get("warehouse_name"):
            resolve_tasks.append(self._resolve_warehouse(entities["warehouse_name"]))
        if entities.get("invoice_number"):
            resolve_tasks.append(self._resolve_invoice(entities["invoice_number"]))
        if entities.get("bill_number"):
            resolve_tasks.append(self._resolve_bill(entities["bill_number"]))
        if entities.get("account_name"):
            resolve_tasks.append(self._resolve_account(entities["account_name"]))
        if entities.get("work_order_number"):
            resolve_tasks.append(
                self._resolve_work_order(entities["work_order_number"])
            )
        if entities.get("bom_code"):
            resolve_tasks.append(self._resolve_bom(entities["bom_code"]))
        if entities.get("work_center_name"):
            resolve_tasks.append(
                self._resolve_work_center(entities["work_center_name"])
            )

        if resolve_tasks:
            resolved_entities = await asyncio.gather(
                *resolve_tasks, return_exceptions=True
            )
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
            from .entity_graph import (
                get_last_node,
                get_focus,
                get_by_ordinal,
                traverse,
                _ensure_graph,
            )

            graph = _ensure_graph(entity_graph)
            _ltxt = (user_text or "").lower()
            _sid = (session_id or "")[:8]

            # Existing pronoun-style fallback for customer
            if not entities.get("customer_name") and "customer" not in result.resolved:
                focus = get_focus(graph)
                if focus and focus.get("type") == "customer":
                    result.resolved["customer"] = ResolvedEntity(
                        entity_type="customer",
                        entity_id=focus["id"],
                        entity_name=focus["name"],
                        confidence=0.9,
                    )
                else:
                    last_cust = get_last_node(graph, "customer")
                    if last_cust:
                        result.resolved["customer"] = ResolvedEntity(
                            entity_type="customer",
                            entity_id=last_cust["id"],
                            entity_name=last_cust["name"],
                            confidence=0.85,
                        )
            if not entities.get("vendor_name") and "vendor" not in result.resolved:
                last_vendor = (
                    get_last_node(graph, "vendor") if graph.get("nodes") else None
                )
                if last_vendor:
                    result.resolved["vendor"] = ResolvedEntity(
                        entity_type="vendor",
                        entity_id=last_vendor["id"],
                        entity_name=last_vendor["name"],
                        confidence=0.85,
                    )

            # B2 Site 1 — pronoun-triggered traversal: "dia"/"itu"/"tadi"/"tersebut"
            # + verb like "faktur"/"tagihan"/"invoice" -> direct_relation (Site 3).
            # Otherwise plain pronoun stays as Site 1 (no-op here; existing logic
            # above already resolved the entity; traverse is extra accelerator).
            _PRONOUN_TOKENS = (" dia", " itu", " tadi", " tersebut", "nya ")
            _has_pronoun = any(tok in f" {_ltxt} " for tok in _PRONOUN_TOKENS)
            _wants_invoice = any(w in _ltxt for w in ("faktur", "invoice"))
            _wants_bill = any(w in _ltxt for w in ("tagihan", "bill"))

            # Determine "from" node for traversal: prefer already-resolved focus/customer/vendor
            _from_node = get_focus(graph)
            if not _from_node:
                _from_node = get_last_node(graph, "customer") or get_last_node(
                    graph, "vendor"
                )

            # B2 Site 3 — direct_relation: pronoun + document noun
            if _has_pronoun and _from_node and (_wants_invoice or _wants_bill):
                _target_type = "invoice" if _wants_invoice else "bill"
                try:
                    hits = traverse(
                        graph,
                        _from_node["_key"],
                        max_depth=1,
                        edge_type="owns",
                        node_type_filter=_target_type,
                    )
                except (KeyError, TypeError):
                    logger.error(
                        "graph_traverse_failed session=%s from=%s",
                        _sid,
                        _from_node.get("_key"),
                        exc_info=True,
                    )
                    hits = []
                logger.info(
                    "graph_traverse session=%s type=direct_relation depth=1 from=%s edge=owns hits=%d",
                    _sid,
                    _from_node.get("_key"),
                    len(hits),
                )
                if hits:
                    # Sort by ts desc — "terakhir"
                    hits.sort(key=lambda n: n.get("ts", 0), reverse=True)
                    pick = hits[0]
                    _field = "invoice" if _target_type == "invoice" else "bill"
                    if _field not in result.resolved:
                        result.resolved[_field] = ResolvedEntity(
                            entity_type=_field,
                            entity_id=pick["id"],
                            entity_name=pick.get("name", ""),
                            confidence=0.85,
                        )

            # B2 Site 2 — ordinal_relation: "customer pertama hutangnya berapa?"
            _ORDINALS = {
                1: ("pertama", "nomor 1", "no 1", "no. 1"),
                2: ("kedua", "nomor 2", "no 2", "no. 2"),
                3: ("ketiga", "nomor 3", "no 3", "no. 3"),
            }
            _ord_idx = None
            for idx, kws in _ORDINALS.items():
                if any(k in _ltxt for k in kws):
                    _ord_idx = idx
                    break
            if _ord_idx and ("customer" in _ltxt or "pelanggan" in _ltxt):
                ord_node = get_by_ordinal(graph, "customer", _ord_idx)
                if ord_node:
                    if "customer" not in result.resolved:
                        result.resolved["customer"] = ResolvedEntity(
                            entity_type="customer",
                            entity_id=ord_node["id"],
                            entity_name=ord_node.get("name", ""),
                            confidence=0.85,
                        )
                    if any(
                        k in _ltxt for k in ("hutang", "piutang", "tagihan", "faktur")
                    ):
                        try:
                            hits = traverse(
                                graph,
                                ord_node["_key"],
                                max_depth=1,
                                edge_type="owns",
                                node_type_filter="invoice",
                            )
                        except (KeyError, TypeError):
                            logger.error(
                                "graph_traverse_failed session=%s from=%s",
                                _sid,
                                ord_node.get("_key"),
                                exc_info=True,
                            )
                            hits = []
                        logger.info(
                            "graph_traverse session=%s type=ordinal_relation depth=1 from=%s edge=owns hits=%d",
                            _sid,
                            ord_node.get("_key"),
                            len(hits),
                        )
            elif _ord_idx and ("vendor" in _ltxt or "pemasok" in _ltxt):
                ord_node = get_by_ordinal(graph, "vendor", _ord_idx)
                if ord_node and "vendor" not in result.resolved:
                    result.resolved["vendor"] = ResolvedEntity(
                        entity_type="vendor",
                        entity_id=ord_node["id"],
                        entity_name=ord_node.get("name", ""),
                        confidence=0.85,
                    )

        # Step B: Complete from memory + defaults (3-source merge)
        result.payload = self._build_payload(
            intent,
            entities,
            result.resolved,
            memory_state,
            system_defaults,
            action_memory_suggestion=action_memory_suggestion,
        )

        # Step B.5: Auto-resolve account for create_expense (keyword inference)
        if (
            intent == "create_expense"
            and "account" not in result.resolved
            and not result.payload.get("account_id")
        ):
            acct_name = result.payload.get("account_name", "")
            desc = result.payload.get("description", "")
            # Strategy 1: user explicitly said account name
            if acct_name:
                acct_res = await self._resolve_account(acct_name)
                if acct_res and acct_res.confidence >= 0.7:
                    result.resolved["account"] = acct_res
                    result.payload["account_id"] = acct_res.entity_id
                    result.payload["account_name"] = acct_res.entity_name
            # Strategy 2: keyword inference from description
            if not result.payload.get("account_id") and desc:
                _EXPENSE_KW = {
                    "listrik": "Beban Listrik",
                    "air pdam": "Beban Air",
                    "telepon": "Beban Telepon",
                    "internet": "Beban Telepon & Internet",
                    "wifi": "Beban Telepon & Internet",
                    "pulsa": "Beban Telepon & Internet",
                    "telefon": "Beban Telepon & Internet",
                    "telpon": "Beban Telepon & Internet",
                    "sewa": "Beban Sewa",
                    "gaji": "Beban Gaji",
                    "transport": "Beban Transportasi",
                    "bensin": "Beban Transportasi",
                    "parkir": "Beban Transportasi",
                    "tol": "Beban Transportasi",
                    "ojek": "Beban Transportasi",
                    "grab": "Beban Transportasi",
                    "servis": "Beban Pemeliharaan",
                    "service": "Beban Pemeliharaan",
                    "reparasi": "Beban Pemeliharaan",
                    "perbaikan": "Beban Pemeliharaan",
                    "maintenance": "Beban Pemeliharaan",
                    "perawatan": "Beban Pemeliharaan",
                    "makan": "Beban Makan & Minum",
                    "minum": "Beban Makan & Minum",
                    "snack": "Beban Makan & Minum",
                    "catering": "Beban Makan & Minum",
                    "konsumsi": "Beban Makan & Minum",
                    "atk": "Beban Perlengkapan Kantor",
                    "alat tulis": "Beban Perlengkapan Kantor",
                    "kertas": "Beban Perlengkapan Kantor",
                    "printer": "Beban Perlengkapan Kantor",
                    "asuransi": "Beban Asuransi",
                    "pajak": "Beban Pajak",
                    "admin bank": "Biaya Admin Bank",
                    "biaya bank": "Biaya Admin Bank",
                }
                desc_lower = desc.lower()
                matched = None
                for kw, acct in _EXPENSE_KW.items():
                    if kw in desc_lower:
                        matched = acct
                        break
                if not matched:
                    matched = "Beban Lain-lain"
                acct_res = await self._resolve_account(matched)
                if acct_res and acct_res.confidence >= 0.5:
                    result.resolved["account"] = acct_res
                    result.payload["account_id"] = acct_res.entity_id
                    result.payload["account_name"] = acct_res.entity_name

        # Step C: Check required fields
        from .direct_action_registry import (
            get_direct_action,
            validate_payload,
            apply_defaults,
            DIRECT_ACTIONS,
        )

        config = get_direct_action(intent)
        if config:
            # Pre-validate defaults hoist (fixes validate-then-enrich ordering bug).
            # Fills deterministic field defaults BEFORE validate_payload so required date
            # fields + FieldSpec defaults don't spuriously trigger needs_clarification.
            try:
                apply_defaults(intent, result.payload)
            except Exception as _e:
                logger.warning(f"apply_defaults failed for {intent}: {_e}")
            _today = datetime.now().strftime("%Y-%m-%d")
            _cfg_full = DIRECT_ACTIONS.get(intent)
            if _cfg_full:
                for _f in _cfg_full.fields:
                    if (
                        getattr(_f, "field_type", None) == "date"
                        and getattr(_f, "required", False)
                        and not result.payload.get(_f.name)
                    ):
                        result.payload[_f.name] = _today
            is_valid, missing_fields = validate_payload(intent, result.payload)
            if not is_valid:
                result.missing.extend(missing_fields)
                if not result.needs_clarification:
                    # missing_fields contains labels (from validate_payload)
                    labels_str = ", ".join(missing_fields)
                    result.clarifications.append(f"Mohon lengkapi: {labels_str}")
                    result.needs_clarification = True

        return result

    def _build_payload(
        self,
        intent,
        entities,
        resolved,
        memory_state,
        system_defaults,
        action_memory_suggestion=None,
    ):
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
            # Do NOT populate bank_account_id when ambiguous — user must pick via clarification.
            # Also STRIP any Stage-2-hallucinated bank_account_id / paid_through_id so the
            # orchestrator's pills shortcut can fire instead of proceeding with a guess.
            if len(r.candidates) <= 1:
                payload["bank_account_id"] = r.entity_id
                payload["bank_account_name"] = r.entity_name
            else:
                payload.pop("bank_account_id", None)
                payload.pop("bank_account_name", None)
                payload.pop("paid_through_id", None)
                payload.pop("paid_through_name", None)
        if "warehouse" in resolved:
            r = resolved["warehouse"]
            payload["warehouse_id"] = r.entity_id
            payload["warehouse_name"] = r.entity_name
        if "invoice" in resolved:
            r = resolved["invoice"]
            payload["invoice_id"] = r.entity_id
            payload["invoice_number"] = r.entity_name
            # Void/update sales_invoice|sales_order use registry field `id`.
            if (
                intent
                in (
                    "void_sales_invoice",
                    "update_sales_invoice",
                    "void_sales_order",
                    "update_sales_order",
                )
                and r.entity_id
            ):
                payload.setdefault("id", r.entity_id)
        if "bill" in resolved:
            r = resolved["bill"]
            payload["bill_id"] = r.entity_id
            payload["bill_number"] = r.entity_name
            # Void/update/post/delete bill use registry field `id`.
            if (
                intent
                in (
                    "void_bill",
                    "update_bill",
                    "post_bill",
                    "delete_bill",
                )
                and r.entity_id
            ):
                payload.setdefault("id", r.entity_id)
        if "account" in resolved:
            r = resolved["account"]
            payload["account_id"] = r.entity_id
            payload["account_name"] = r.entity_name

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
            "amount",
            "quantity",
            "unit_price",
            "description",
            "date",
            "phone",
            "email",
            "address",
            "reason",
            "name",
            "account_type",
            "payment_method",
            "item_type",
            "base_unit",
        ]
        for field_name in direct_fields:
            if entities.get(field_name) is not None:
                payload[field_name] = entities[field_name]

        # 4C: deterministic payment gate — map `amount` -> `total_amount`
        # for payment intents whose registry FieldSpec is named `total_amount`.
        # Without this, validate_payload misses the required field and the
        # pipeline emits a TEXT clarification instead of DIRECT_ACTION_PREVIEW.
        if intent in ("create_receive_payment", "create_bill_payment"):
            if payload.get("amount") is not None and not payload.get("total_amount"):
                payload["total_amount"] = payload["amount"]

        # Registry-aware field injection (Stage 2 extracts exact registry names)
        from .direct_action_registry import get_direct_action

        _config = get_direct_action(intent)
        if _config:
            _registry_names = {f.name for f in _config.fields}
            for key, value in entities.items():
                # Iron Law 1 hardening: never trust LLM-extracted ID fields.
                # Stage-2 LLM (Gemini Flash Lite) can hallucinate UUID-shaped
                # values for *_id fields that by chance match real DB rows,
                # silently routing transactions to wrong entity. IDs MUST
                # come from the resolver path above (sources 1 / 2 / 2.5).
                # Ticket: 2026-05-07-stage2-llm-uuid-hallucination-audit.
                if key == "id" or key.endswith("_id") or key.endswith("_uuid"):
                    if value:
                        logger.warning(
                            "[INVARIANT_GUARD] Stripped LLM-extracted ID field %r from payload (intent=%s); resolver is single source of truth",
                            key,
                            intent,
                        )
                    continue
                if key in _registry_names and value is not None and key not in payload:
                    payload[key] = value

        # Source 2: Memory state (fill gaps only)
        if memory_state:
            if "customer_id" not in payload and memory_state.get("active_customer_id"):
                payload["customer_id"] = memory_state["active_customer_id"]
                payload.setdefault(
                    "customer_name", memory_state.get("active_customer_name", "")
                )
            if "vendor_id" not in payload and memory_state.get("active_vendor_id"):
                payload["vendor_id"] = memory_state["active_vendor_id"]
                payload.setdefault(
                    "vendor_name", memory_state.get("active_vendor_name", "")
                )
            if "invoice_id" not in payload and memory_state.get("active_invoice_id"):
                payload["invoice_id"] = memory_state["active_invoice_id"]
                payload.setdefault(
                    "invoice_number", memory_state.get("active_invoice_number", "")
                )
                if intent in (
                    "void_sales_invoice",
                    "update_sales_invoice",
                    "void_sales_order",
                    "update_sales_order",
                ):
                    payload.setdefault("id", memory_state["active_invoice_id"])
            if "bill_id" not in payload and memory_state.get("active_bill_id"):
                payload["bill_id"] = memory_state["active_bill_id"]
                payload.setdefault(
                    "bill_number", memory_state.get("active_bill_number", "")
                )
                if intent in (
                    "void_bill",
                    "update_bill",
                    "post_bill",
                    "delete_bill",
                ):
                    payload.setdefault("id", memory_state["active_bill_id"])

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
                payload.setdefault(
                    "bank_account_name", pattern.get("bank_account_name", "")
                )
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
                payload["allocations"] = [
                    {
                        "invoice_id": inv.entity_id,
                        "invoice_number": inv.entity_name,
                        "amount": amount,
                    }
                ]

        # bill_payment: needs allocations array from resolved bill
        elif intent == "create_bill_payment" and "bill" in resolved:
            bill = resolved["bill"]
            amount = entities.get("amount")
            if amount and "allocations" not in payload:
                payload["allocations"] = [
                    {
                        "bill_id": bill.entity_id,
                        "bill_number": bill.entity_name,
                        "amount": amount,
                    }
                ]

        # sales_invoice: needs items array from resolved item
        elif intent == "create_sales_invoice" and "item" in resolved:
            item = resolved["item"]
            if "items" not in payload:
                qty = entities.get("quantity", 1)
                price = entities.get("unit_price", 0)
                payload["items"] = [
                    {
                        "item_id": item.entity_id,
                        "description": item.entity_name,
                        "quantity": qty,
                        "unit_price": price,
                    }
                ]

        # sales_order: needs items array from resolved item (mirror sales_invoice)
        elif intent == "create_sales_order" and "item" in resolved:
            item = resolved["item"]
            if "items" not in payload:
                qty = entities.get("quantity", 1)
                price = entities.get("unit_price", 0)
                payload["items"] = [
                    {
                        "item_id": item.entity_id,
                        "description": item.entity_name,
                        "quantity": qty,
                        "unit_price": price,
                    }
                ]

        # quote: needs items array from resolved item (schema: description required, unit_price int)
        elif intent == "create_quote" and "item" in resolved:
            item = resolved["item"]
            if "items" not in payload:
                qty = entities.get("quantity", 1)
                price = entities.get("unit_price", 0)
                payload["items"] = [
                    {
                        "item_id": item.entity_id,
                        "description": item.entity_name,
                        "quantity": qty,
                        "unit_price": price,
                    }
                ]

        # bill (faktur pembelian): needs items array, field names differ
        elif intent == "create_bill" and "item" in resolved:
            item = resolved["item"]
            if "items" not in payload:
                qty = entities.get("quantity", 1)
                price = entities.get("unit_price", 0)
                payload["items"] = [
                    {
                        "item_id": item.entity_id,
                        "item_name": item.entity_name,
                        "quantity": qty,
                        "unit_price": price,
                    }
                ]

        # expense: map bank_account_id to paid_through_id (only when unambiguous)
        elif intent == "create_expense":
            if "bank_account" in resolved and "paid_through_id" not in payload:
                _ba = resolved["bank_account"]
                if len(_ba.candidates) <= 1:
                    payload["paid_through_id"] = _ba.entity_id
                    payload["paid_through_name"] = _ba.entity_name

        # Intent-specific date mapping
        if "date" not in payload:
            payload["date"] = system_defaults.get("date", "")
        if intent.startswith("create_") and intent not in (
            "create_customer",
            "create_vendor",
            "create_warehouse",
            "create_bank_account",
            "create_item",
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
                self.tenant_id,
                f"%{name_fragment}%",
            )
            if not rows:
                rows = await self.db.fetch(
                    """SELECT id, nama, telepon, email,
                              similarity(nama, $2) AS sim
                       FROM customers
                       WHERE tenant_id = $1 AND is_active = true
                         AND similarity(nama, $2) > 0.15
                       ORDER BY sim DESC LIMIT 5""",
                    self.tenant_id,
                    name_fragment,
                )
            if not rows:
                return ResolvedEntity(
                    entity_type="customer",
                    entity_id="",
                    entity_name=name_fragment,
                    confidence=0.0,
                )
            candidates = [{"id": str(r["id"]), "name": r["nama"]} for r in rows]
            best = candidates[0]
            confidence = 1.0 if len(candidates) == 1 else 0.7
            for c in candidates:
                if c["name"].lower().strip() == name_fragment.lower().strip():
                    best = c
                    confidence = 1.0
                    break
            return ResolvedEntity(
                entity_type="customer",
                entity_id=best["id"],
                entity_name=best["name"],
                confidence=confidence,
                candidates=candidates,
            )
        except Exception as e:
            logger.warning("[RESOLVE] Customer lookup failed: %s", e)
            return None

    async def _resolve_vendor(self, name_fragment: str) -> Optional[ResolvedEntity]:
        try:
            rows = await self.db.fetch(
                """SELECT id, name FROM vendors
                   WHERE tenant_id = $1 AND is_active = true AND name ILIKE $2
                   ORDER BY name LIMIT 5""",
                self.tenant_id,
                f"%{name_fragment}%",
            )
            if not rows:
                rows = await self.db.fetch(
                    """SELECT id, name,
                              similarity(name, $2) AS sim
                       FROM vendors
                       WHERE tenant_id = $1 AND is_active = true
                         AND similarity(name, $2) > 0.15
                       ORDER BY sim DESC LIMIT 5""",
                    self.tenant_id,
                    name_fragment,
                )
            if not rows:
                return ResolvedEntity(
                    entity_type="vendor",
                    entity_id="",
                    entity_name=name_fragment,
                    confidence=0.0,
                )
            candidates = [{"id": str(r["id"]), "name": r["name"]} for r in rows]
            best = candidates[0]
            confidence = 1.0 if len(candidates) == 1 else 0.7
            for c in candidates:
                if c["name"].lower().strip() == name_fragment.lower().strip():
                    best = c
                    confidence = 1.0
                    break
            return ResolvedEntity(
                entity_type="vendor",
                entity_id=best["id"],
                entity_name=best["name"],
                confidence=confidence,
                candidates=candidates,
            )
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
                self.tenant_id,
                f"%{search_term}%",
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
                    self.tenant_id,
                    f"%{search_term}%",
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
                    self.tenant_id,
                    name_fragment.strip(),
                )
            if not rows:
                return ResolvedEntity(
                    entity_type="item",
                    entity_id="",
                    entity_name=name_fragment,
                    confidence=0.0,
                )
            candidates = [{"id": str(r["id"]), "name": r["nama_produk"]} for r in rows]
            best = candidates[0]
            confidence = 1.0 if len(candidates) == 1 else 0.7
            for c in candidates:
                if c["name"].lower().strip() == name_fragment.lower().strip():
                    best = c
                    confidence = 1.0
                    break
            return ResolvedEntity(
                entity_type="item",
                entity_id=best["id"],
                entity_name=best["name"],
                confidence=confidence,
                candidates=candidates,
            )
        except Exception as e:
            logger.warning("[RESOLVE] Item lookup failed: %s", e)
            return None

    async def _resolve_bank_account(
        self, name_fragment: str
    ) -> Optional[ResolvedEntity]:
        try:
            rows = await self.db.fetch(
                """SELECT id, account_name, bank_name, coa_id
                   FROM bank_accounts
                   WHERE tenant_id = $1 AND is_active = true
                     AND (account_name ILIKE $2 OR bank_name ILIKE $2)
                   ORDER BY account_name LIMIT 5""",
                self.tenant_id,
                f"%{name_fragment}%",
            )
            if not rows:
                return ResolvedEntity(
                    entity_type="bank_account",
                    entity_id="",
                    entity_name=name_fragment,
                    confidence=0.0,
                )
            candidates = [{"id": str(r["id"]), "name": r["account_name"]} for r in rows]
            best = candidates[0]
            confidence = 1.0 if len(candidates) == 1 else 0.7
            # Exact match boost: if one candidate matches exactly, pick it
            _matched_exact = False
            for i, r in enumerate(rows):
                if r["account_name"].lower().strip() == name_fragment.lower().strip():
                    best = candidates[i]
                    confidence = 1.0
                    candidates = [best]  # collapse to single match
                    _matched_exact = True
                    break
            # Bank-name ambiguity: when user typed a short identifier (e.g. "BCA")
            # matching multiple accounts, DO NOT silently collapse. Preserve all
            # candidates so orchestrator emits a CLARIFICATION with pills.
            # Collapsing destroys user intent (wrong account picked).
            if not _matched_exact and len(candidates) > 1:
                logger.warning(
                    "[RESOLVE] Bank ambiguity preserved for clarification: fragment=%r matched %d accounts: %s",
                    name_fragment,
                    len(candidates),
                    [c["name"] for c in candidates],
                )
                confidence = 0.7  # force needs_clarification in resolve_and_complete
            return ResolvedEntity(
                entity_type="bank_account",
                entity_id=best["id"],
                entity_name=best["name"],
                confidence=confidence,
                candidates=candidates,
            )
        except Exception as e:
            logger.warning("[RESOLVE] Bank account lookup failed: %s", e)
            return None

    async def _resolve_warehouse(self, name_fragment: str) -> Optional[ResolvedEntity]:
        try:
            rows = await self.db.fetch(
                """SELECT id, name FROM warehouses
                   WHERE tenant_id = $1 AND name ILIKE $2
                   ORDER BY name LIMIT 5""",
                self.tenant_id,
                f"%{name_fragment}%",
            )
            if not rows:
                return ResolvedEntity(
                    entity_type="warehouse",
                    entity_id="",
                    entity_name=name_fragment,
                    confidence=0.0,
                )
            candidates = [{"id": str(r["id"]), "name": r["name"]} for r in rows]
            best = candidates[0]
            confidence = 1.0 if len(candidates) == 1 else 0.7
            return ResolvedEntity(
                entity_type="warehouse",
                entity_id=best["id"],
                entity_name=best["name"],
                confidence=confidence,
                candidates=candidates,
            )
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
                self.tenant_id,
                f"%{invoice_number}%",
            )
            if not rows:
                return ResolvedEntity(
                    entity_type="invoice",
                    entity_id="",
                    entity_name=invoice_number,
                    confidence=0.0,
                )
            candidates = [
                {"id": str(r["id"]), "name": r["invoice_number"]} for r in rows
            ]
            best = candidates[0]
            confidence = 1.0 if len(candidates) == 1 else 0.7
            return ResolvedEntity(
                entity_type="invoice",
                entity_id=best["id"],
                entity_name=best["name"],
                confidence=confidence,
                candidates=candidates,
            )
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
                self.tenant_id,
                f"%{bill_number}%",
            )
            if not rows:
                return ResolvedEntity(
                    entity_type="bill",
                    entity_id="",
                    entity_name=bill_number,
                    confidence=0.0,
                )
            candidates = [
                {"id": str(r["id"]), "name": r["invoice_number"]} for r in rows
            ]
            best = candidates[0]
            confidence = 1.0 if len(candidates) == 1 else 0.7
            return ResolvedEntity(
                entity_type="bill",
                entity_id=best["id"],
                entity_name=best["name"],
                confidence=confidence,
                candidates=candidates,
            )
        except Exception as e:
            logger.warning("[RESOLVE] Bill lookup failed: %s", e)
            return None

    async def _resolve_by_number(
        self,
        search_val: str,
        *,
        table: str,
        number_column: str,
        entity_type: str,
    ) -> Optional[ResolvedEntity]:
        """Generic document number resolver. Works with any table that has a
        number column (expense_number, journal_number, credit_note_number, etc.).

        Args:
            search_val: The document number to search (e.g. "EXP-2604-0016")
            table: DB table name (e.g. "expenses")
            number_column: Column containing the document number
            entity_type: Entity type label for the result
        """
        try:
            rows = await self.db.fetch(
                f"""SELECT id, {number_column}
                   FROM {table}
                   WHERE tenant_id = $1 AND {number_column} ILIKE $2
                   ORDER BY created_at DESC LIMIT 5""",
                self.tenant_id,
                f"%{search_val}%",
            )
            if not rows:
                return ResolvedEntity(
                    entity_type=entity_type,
                    entity_id="",
                    entity_name=search_val,
                    confidence=0.0,
                )
            candidates = [{"id": str(r["id"]), "name": r[number_column]} for r in rows]
            best = candidates[0]
            confidence = 1.0 if len(candidates) == 1 else 0.7
            return ResolvedEntity(
                entity_type=entity_type,
                entity_id=best["id"],
                entity_name=best["name"],
                confidence=confidence,
                candidates=candidates,
            )
        except Exception as e:
            logger.warning("[RESOLVE] %s lookup failed: %s", entity_type, e)
            return None

    # ── Response Entity Context (REC): Session-based resolution ──

    async def _resolve_account(self, name_fragment: str) -> "Optional[ResolvedEntity]":
        """Resolve CoA account by name. Excludes is_header=true (Law 18)."""
        try:
            rows = await self.db.fetch(
                """SELECT id, name, account_code, account_type
                   FROM chart_of_accounts
                   WHERE tenant_id = $1 AND is_header = false
                     AND is_active = true
                     AND name ILIKE $2
                   ORDER BY
                     CASE WHEN LOWER(name) = LOWER($3) THEN 0 ELSE 1 END,
                     name
                   LIMIT 5""",
                self.tenant_id,
                "%" + name_fragment + "%",
                name_fragment.strip(),
            )
            if not rows:
                return ResolvedEntity(
                    entity_type="account",
                    entity_id="",
                    entity_name=name_fragment,
                    confidence=0.0,
                )
            candidates = [
                {"id": str(r["id"]), "name": r["name"] + " (" + r["account_code"] + ")"}
                for r in rows
            ]
            best = candidates[0]
            confidence = 1.0 if len(candidates) == 1 else 0.7
            for i, r in enumerate(rows):
                if r["name"].lower().strip() == name_fragment.lower().strip():
                    best = candidates[i]
                    confidence = 1.0
                    break
            return ResolvedEntity(
                entity_type="account",
                entity_id=best["id"],
                entity_name=best["name"],
                confidence=confidence,
                candidates=candidates,
            )
        except Exception as e:
            logger.warning("[RESOLVE] Account lookup failed: %s", e)
            return None

    @staticmethod
    async def _resolve_work_order(
        self, name_or_number: str
    ) -> "Optional[ResolvedEntity]":
        """Resolve work order by order_number or partial match."""
        try:
            _q = name_or_number.strip()
            rows = await self.db.fetch(
                "SELECT id::text, order_number, status "
                "FROM production_orders WHERE tenant_id = $1 AND order_number ILIKE $2 LIMIT 1",
                self.tenant_id,
                _q,
            )
            if not rows:
                rows = await self.db.fetch(
                    "SELECT id::text, order_number, status "
                    "FROM production_orders WHERE tenant_id = $1 AND order_number ILIKE $2 "
                    "ORDER BY created_at DESC LIMIT 1",
                    self.tenant_id,
                    f"%{_q}%",
                )
            if rows:
                row = rows[0]
                return ResolvedEntity(
                    entity_type="work_order",
                    entity_id=row["id"],
                    entity_name=row["order_number"],
                )
        except Exception as e:
            logger.warning(f"_resolve_work_order error: {e}")
        return None

    async def _resolve_bom(self, name_or_code: str) -> "Optional[ResolvedEntity]":
        """Resolve BOM by bom_code or bom_name."""
        try:
            _q = name_or_code.strip()
            rows = await self.db.fetch(
                "SELECT id::text, bom_code, bom_name, status "
                "FROM bill_of_materials WHERE tenant_id = $1 AND (bom_code ILIKE $2 OR bom_name ILIKE $2) LIMIT 1",
                self.tenant_id,
                _q,
            )
            if not rows:
                rows = await self.db.fetch(
                    "SELECT id::text, bom_code, bom_name, status "
                    "FROM bill_of_materials WHERE tenant_id = $1 AND (bom_code ILIKE $2 OR bom_name ILIKE $2) "
                    "ORDER BY created_at DESC LIMIT 1",
                    self.tenant_id,
                    f"%{_q}%",
                )
            if rows:
                row = rows[0]
                return ResolvedEntity(
                    entity_type="bom",
                    entity_id=row["id"],
                    entity_name=row["bom_code"] or row["bom_name"],
                )
        except Exception as e:
            logger.warning(f"_resolve_bom error: {e}")
        return None

    async def _resolve_work_center(
        self, name_or_code: str
    ) -> "Optional[ResolvedEntity]":
        """Resolve work center by code or name."""
        try:
            _q = name_or_code.strip()
            rows = await self.db.fetch(
                "SELECT id::text, code, name "
                "FROM work_centers WHERE tenant_id = $1 AND (code ILIKE $2 OR name ILIKE $2) AND is_active = true LIMIT 1",
                self.tenant_id,
                _q,
            )
            if not rows:
                rows = await self.db.fetch(
                    "SELECT id::text, code, name "
                    "FROM work_centers WHERE tenant_id = $1 AND (code ILIKE $2 OR name ILIKE $2) AND is_active = true "
                    "ORDER BY created_at DESC LIMIT 1",
                    self.tenant_id,
                    f"%{_q}%",
                )
            if rows:
                row = rows[0]
                return ResolvedEntity(
                    entity_type="work_center",
                    entity_id=row["id"],
                    entity_name=f"{row['code']} - {row['name']}",
                )
        except Exception as e:
            logger.warning(f"_resolve_work_center error: {e}")
        return None

    def resolve_from_session(user_text: str, session_state) -> dict:
        """Resolve pronouns and ordinals from REC session context.
        Returns dict of resolved fields to merge into extraction.entities.
        """
        t = user_text.lower()
        items = getattr(session_state, "last_response_items", None) or []
        entity = getattr(session_state, "active_entity", None)
        resolved = {}

        # Pronoun resolution
        _PRONOUNS = [
            " dia ",
            " mereka ",
            "nya?",
            "nya ",
            "ke mereka",
            "dari mereka",
            "di situ",
            "ke dia",
            "sama dia",
            " dia?",
            " dia,",
            " itu",
            " tersebut",
            " tadi",
        ]
        if entity and any(p in f" {t} " or t.endswith(p.strip()) for p in _PRONOUNS):
            _type = entity.get("type", "")
            _name = entity.get("name", "")
            if _type == "customer" and _name:
                resolved["customer_name"] = _name
            elif _type == "vendor" and _name:
                resolved["vendor_name"] = _name
            elif _type == "item" and _name:
                resolved["item_name"] = _name
            elif _type == "bank_account" and _name:
                resolved["bank_name"] = _name
            if entity.get("id"):
                resolved[f"{_type}_id"] = entity["id"]

        # Ordinal resolution
        if items:
            target = None
            if any(
                w in t for w in ["yang pertama", "pertama", "nomor 1", "no 1", "no. 1"]
            ):
                target = items[0]
            elif any(w in t for w in ["yang terakhir", "terakhir"]):
                target = items[-1]
            elif (
                any(w in t for w in ["yang kedua", "nomor 2", "no 2"])
                and len(items) > 1
            ):
                target = items[1]
            elif (
                any(w in t for w in ["yang ketiga", "nomor 3", "no 3"])
                and len(items) > 2
            ):
                target = items[2]
            elif any(
                w in t
                for w in [
                    "yang terbesar",
                    "terbesar",
                    "paling besar",
                    "paling gede",
                    "paling banyak",
                    "tergede",
                ]
            ):
                _with_amt = [i for i in items if i.get("_amount") is not None]
                if _with_amt:
                    target = max(_with_amt, key=lambda x: x["_amount"])
            elif any(
                w in t
                for w in [
                    "yang terkecil",
                    "terkecil",
                    "paling kecil",
                    "paling sedikit",
                    "paling dikit",
                ]
            ):
                _with_amt = [i for i in items if i.get("_amount") is not None]
                if _with_amt:
                    target = min(_with_amt, key=lambda x: x["_amount"])

            if target:
                resolved["_resolved_item"] = target
                # Set entity_id from the resolved item's document ID (for path param resolution)
                if target.get("_id"):
                    resolved["entity_id"] = target["_id"]
                if target.get("_ref"):
                    resolved["entity_name"] = target["_ref"]
                if target.get("_name") and not any(
                    resolved.get(k)
                    for k in ["customer_name", "vendor_name", "item_name"]
                ):
                    _domain = getattr(session_state, "last_domain", None)
                    if _domain in ("ar", "customer"):
                        resolved["customer_name"] = target["_name"]
                    elif _domain in ("ap", "vendor"):
                        resolved["vendor_name"] = target["_name"]
                    elif _domain == "items":
                        resolved["item_name"] = target["_name"]

        # Document reference matching — "EXP-2604-0016" / "INV-0042" / "PB-0001"
        # Scan last_response_items for _ref match when user mentions a doc number
        import re as _rec_re

        _doc_ref_match = _rec_re.search(
            r"\b(EXP|INV|PB|JE|CN|VC|QT|RP|BP|SA|BT|CD|VD)-[\w-]+\b",
            user_text,
            _rec_re.IGNORECASE,
        )
        if _doc_ref_match and items:
            _search_ref = _doc_ref_match.group(0).upper()
            for _item in items:
                _item_ref = (_item.get("_ref") or "").upper()
                if _item_ref and _search_ref in _item_ref:
                    resolved["_resolved_item"] = _item
                    if _item.get("_id"):
                        resolved["entity_id"] = _item["_id"]
                    if _item.get("_name"):
                        resolved["entity_name"] = _item["_name"]
                    break

        return resolved
