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
