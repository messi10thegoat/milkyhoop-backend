# Goldset Spike — Capture Notes

**Date:** 2026-06-05
**Spike script:** `/tmp/goldset_spike.py` (deleted post-investigation)
**Probes:** 4 live calls to `POST https://milkyhoop.com/api/v3/chat/message`

---

## 1. Response Shape — Authoritative Field Names

All four probes returned a flat JSON object at the top level (no `data` wrapper for
the main payload; `data` is a separate key used only for DIRECT_ACTION metadata).

| Field | Type | Holds |
|-------|------|-------|
| `message_type` | string | Routing tier signal. Values observed: `TEXT`, `DIRECT_ACTION_PREVIEW` |
| `model_used` | string | Engine that produced the answer. Values observed: `gemini-2.5-flash-lite`, `projection_engine`, `pipeline` |
| `iterations` | int | Number of agent loop iterations (1 = single-shot, >1 = multi-step reasoning) |
| `text` | string | Human-readable response text (markdown) |
| `trace_id` | UUID string | Unique per request; use for correlation |
| `message_id` | UUID string | Persisted message ID |
| `tool_calls` | array\|null | List of `{name, args, success}` dicts. `null` for projection_engine path |
| `thinking_stages` | array\|null | Display-only stage labels. `null` for projection_engine |
| `pending_action_id` | UUID\|null | Set when `message_type == DIRECT_ACTION_PREVIEW` |
| `data` | object\|null | DIRECT_ACTION payload (contains `action_key`, `payload`, `review_card`, etc.) |

### CRITICAL: Intent is NOT in the top-level response

**`intent` is not a top-level field in the API response.** It is only visible inside
`tool_calls[0].args.intent` when the pipeline ran `entity_extractor`.

**Primary capture method:** read `tool_calls[0].args.intent` (works for query/calc/create/update/delete/void paths where `entity_extractor` is called first).

**Fallback (when `tool_calls` is `null` or entity_extractor not first):** query the
`intent_decision_log` table by `(tenant_id, trace_id)` or `(tenant_id, max(ts))`:

```sql
SELECT final_intent
FROM intent_decision_log
WHERE tenant_id = <tenant_id>
  AND trace_id = '<trace_id>'   -- preferred, exact match
ORDER BY ts DESC
LIMIT 1;
```

Or by user text (fuzzy):
```sql
SELECT final_intent FROM intent_decision_log
WHERE tenant_id = <tid>
ORDER BY ts DESC LIMIT 1;
```

---

## 2. Real Examples From the 4 Probes

### Probe 1 — Lookup (`"daftar pelanggan"`)
```
message_type  : TEXT
model_used    : gemini-2.5-flash-lite
iterations    : 1
tool_calls[0] : {name: "entity_extractor", args: {intent: "query_customers_list"}}
intent        : query_customers_list   ← from tool_calls[0].args.intent
trace_id      : 95f6d361-5077-472d-bcbe-64da1b98ddbd
latency_ms    : 1348
```

### Probe 2 — Profit / Bug-I (`"profit bulan ini"`)
```
message_type  : TEXT
model_used    : gemini-2.5-flash-lite
iterations    : 1
tool_calls[0] : {name: "entity_extractor", args: {intent: "query_profit_loss"}}
intent        : query_profit_loss   ← from tool_calls[0].args.intent
trace_id      : 1deed13f-fa4e-40b5-b96a-d33e252521f4
latency_ms    : 1094
```

### Probe 3 — Projection (`"jika omzet penjualan saya naik 100 persen..."`)
```
message_type  : TEXT
model_used    : projection_engine
iterations    : 1
tool_calls    : null              ← NO tool_calls!
intent        : MUST use DB fallback → intent_decision_log → "query_gross_profit_projection"
trace_id      : 3431b14c-455a-4af6-b4df-a391393b35fa
latency_ms    : 209
```

### Probe 4 — CRUD Preview (`"buat pelanggan baru Spike Probe"`)
```
message_type  : DIRECT_ACTION_PREVIEW
model_used    : pipeline
iterations    : 1
tool_calls[0] : {name: "entity_extractor", args: {intent: "create_customer"}}
data.action_key : "create_customer"  ← secondary source
intent        : create_customer   ← from tool_calls[0].args.intent
trace_id      : cccd5159-f60e-446a-a6ee-ad199a25e271
latency_ms    : 904
```

---

## 3. Intent Extraction — Decision Logic for Harness

```python
def extract_intent(response: dict) -> str | None:
    """
    Primary:  tool_calls[0].args.intent  (present for query/calc/create/update/delete/void)
    Secondary: data.action_key            (only when message_type == DIRECT_ACTION_PREVIEW)
    Fallback:  intent_decision_log DB query by trace_id (needed for projection_engine path)
    """
    tool_calls = response.get("tool_calls") or []
    if tool_calls and isinstance(tool_calls[0].get("args"), dict):
        intent = tool_calls[0]["args"].get("intent")
        if intent:
            return intent
    # Secondary: DIRECT_ACTION_PREVIEW with data.action_key
    data = response.get("data") or {}
    if data.get("action_key"):
        return data["action_key"]
    # Fallback: DB query required
    return None   # caller must use DB fallback with trace_id
```

---

## 4. Tier Signal — Locking `derive_tier`

### Observed `model_used` × `message_type` matrix

| Path | model_used | message_type | iterations | Tier |
|------|-----------|--------------|------------|------|
| Gemini query pipeline (read) | `gemini-2.5-flash-lite` | `TEXT` | 1 | **A** |
| Calc engine (aggregate) | `calc_engine` | `TEXT` | 1 | **A** |
| Projection engine | `projection_engine` | `TEXT` | 1 | **B** |
| DIRECT_ACTION_PREVIEW (CRUD) | `pipeline` | `DIRECT_ACTION_PREVIEW` | 1 | **A** |
| Chitchat | `gemini-2.5-flash-lite` (or `gpt-4o-mini`) | `TEXT` | 1 | **A** |
| Agent loop (complex reasoning) | `gpt-4o-mini` | `TEXT` | >1 | **B** |

### Locked `derive_tier` rule

```python
def derive_tier(
    intent: str | None,
    model_used: str,
    message_type: str,
    iterations: int,
) -> str:
    """
    Tier B = projection engine OR multi-iteration agent loop reasoning.
    Tier A = everything else (calc, gemini pipeline, CRUD preview, chitchat).
    """
    if model_used == "projection_engine":
        return "B"
    if model_used == "gpt-4o-mini" and message_type == "TEXT" and iterations > 1:
        return "B"
    return "A"
```

**Key insight:** `projection_engine` is the clearest Tier B signal — zero ambiguity,
`tool_calls` is null, latency is very low (209ms, deterministic math), not LLM.
The `gpt-4o-mini` + `iterations > 1` path covers agent-loop multi-step reasoning.
All other paths (gemini pipeline, calc_engine, pipeline/DIRECT_ACTION_PREVIEW) = Tier A.

---

## 5. `intent_decision_log` — Confirmed Present

- Table exists in `milkydb`, 5 719 rows as of spike date.
- Column `final_intent` holds the authoritative resolved intent string.
- Useful columns: `final_intent`, `trace_id` (join key to API response `trace_id`),
  `tenant_id`, `ts`.
- **`query_gross_profit_projection` is present in the code grep** (entity_extractor.py)
  but NOT yet seen in `intent_decision_log` telemetry — it is dispatched via
  `projection_engine` which may bypass the log writer. Harness should accept it as valid
  even if not in DB telemetry.

---

## 6. Additional Observed Intents (DB-only, not in code grep)

These appeared in `intent_decision_log` but not in the code grep pattern. They are
produced by classifiers / LLM routing, not hard-coded strings:

- `ambiguous`
- `chitchat`
- `contextual_drill_down`
- `drilldown_table`
- `query` (generic fallback)
- `reformat_as_table`
- `query_cash_flow_projection`
- `query_cash_flow_trends`
- `query_daily_transactions`
- `query_hutang_detail`
- `query_items_list`
- `query_period_transactions`
- `query_piutang_detail`
- `query_reconciliation_status`
- `query_sales_daily`
- `query_sales_today`
- `calc_sum_ar`
- `calc_sum_received_this_month`
- `calc_count_bills_outstanding` (DB only variant)

---

## 7. Summary Table for Downstream Tasks

| Artifact | Value |
|----------|-------|
| Response text field | `text` |
| Message type field | `message_type` |
| Model used field | `model_used` |
| Iterations field | `iterations` |
| Intent (primary) | `tool_calls[0]["args"]["intent"]` |
| Intent (secondary) | `data["action_key"]` (DIRECT_ACTION_PREVIEW only) |
| Intent (fallback) | DB `intent_decision_log.final_intent` WHERE `trace_id` matches |
| Tier B signals | `model_used == "projection_engine"` OR (`model_used == "gpt-4o-mini"` AND `iterations > 1`) |
| Tier A | everything else |

---

## 2026-06-05 Baseline

Run:
Dataset: 28 cases, 39 turns total (7 adversarial, 3 crud, 3 followup, 7 lookup, 3 reasoning, 3 whatif, 2 why)

### Headline Numbers

| Metric | Value |
|--------|-------|
| **Overall pass** | **16/28 (57%)** |
| **Routing accuracy** | **28/39 (72%)** |
| **Adversarial pass** | **4/7 (57%)** |
| **I5 trace rate (Tier B)** | **5/6 (83%)** |

### Per-Category Pass Rate

| Category | Pass |
|----------|------|
| adversarial | 4/7 |
| crud | 3/3 |
| followup | 1/3 |
| lookup | 4/7 |
| reasoning | 1/3 |
| whatif | 3/3 |
| why | 0/2 |

### Top Misroutes (expected -> actual)

| Route | Count |
|-------|-------|
|  -> (empty/none) | 2 |
|  -> (empty/none) | 1 |
|  ->  | 1 |
|  -> (empty/none) | 1 |
|  ->  | 1 |

### Key Observations

- **Routing gaps**: 11 misroutes out of 39 turns. AP/AR outstanding queries (5 failures) are the biggest single cluster — the router is not reliably recognising piutang/hutang jatuh tempo phrasing.
- **Adversarial traps**: 3 of 7 failed.  triggered a 500 server error (1 case errored).  over-escalated a genuine lookup.  gave a margin dump instead of contributing facts.
- **Why-questions**: 0/2 — both causal/cashflow questions failed; the engine does not yet engage with open-ended why via contributing facts.
- **Follow-up resolution**: 1/3 — pronoun/domain carry from session state is unreliable.
- **I5 trace rate (83%)**: Higher than expected — most Tier B reasoning responses DO carry a structured trace. The 1 missing trace is the gap to close.
- **CRUD + whatif**: 3/3 and 3/3 — these are solid; no regressions here.

### Error Cases

- : HTTP 500 from the live endpoint (server error, not a routing failure per se). Counted as failed/0-asserts.

### What Phase 3 Must Beat

- Routing accuracy > 72% (target: ≥85%)
- Adversarial pass > 4/7 (target: ≥6/7)
- Why-category pass > 0/2 (target: 2/2)
- Follow-up pass > 1/3 (target: 3/3)

---

## 2026-06-05 Baseline

Run: `python3 -m goldset.run_goldset goldset/baselines/2026-06-05-baseline.json`
Dataset: 28 cases, 39 turns total (7 adversarial, 3 crud, 3 followup, 7 lookup, 3 reasoning, 3 whatif, 2 why)

### Headline Numbers

| Metric | Value |
|--------|-------|
| **Overall pass** | **16/28 (57%)** |
| **Routing accuracy** | **28/39 (72%)** |
| **Adversarial pass** | **4/7 (57%)** |
| **I5 trace rate (Tier B)** | **5/6 (83%)** |

### Per-Category Pass Rate

| Category | Pass |
|----------|------|
| adversarial | 4/7 |
| crud | 3/3 |
| followup | 1/3 |
| lookup | 4/7 |
| reasoning | 1/3 |
| whatif | 3/3 |
| why | 0/2 |

### Top Misroutes (expected -> actual)

| Route | Count |
|-------|-------|
| query_ap_outstanding -> (none) | 2 |
| query_ar_outstanding -> (none) | 1 |
| query_items_list -> query_items_search | 1 |
| (query_ar_invoices, query_ar_outstanding) -> (none) | 1 |
| (calc_top_selling_items, query_items_summary) -> query_items_search | 1 |

### Key Observations

- **Routing gaps**: 11 misroutes out of 39 turns. AP/AR outstanding queries (5 failures) are the biggest single cluster.
- **Adversarial traps**: 3 of 7 failed. adv_margin_keyword_is_projection triggered a 500 server error. adv_terlaris_not_projection over-escalated a genuine lookup. adv_why_without_rule gave a margin dump instead of contributing facts.
- **Why-questions**: 0/2 — both causal/cashflow questions failed; engine does not yet engage with open-ended "why" via contributing facts.
- **Follow-up resolution**: 1/3 — pronoun/domain carry from session state is unreliable.
- **I5 trace rate (83%)**: Higher than expected — most Tier B responses DO carry a structured trace. 1 missing.
- **CRUD + whatif**: 3/3 and 3/3 — solid, no regressions.

### Error Cases

- adv_margin_keyword_is_projection: HTTP 500 from live endpoint. Counted as failed/0-asserts.

### What Phase 3 Must Beat

- Routing accuracy > 72% (target: >=85%)
- Adversarial pass > 4/7 (target: >=6/7)
- Why-category pass > 0/2 (target: 2/2)
- Follow-up pass > 1/3 (target: 3/3)
