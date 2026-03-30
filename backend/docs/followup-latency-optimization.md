# Follow-Up Latency Optimization Plan

## Current State
- Follow-up queries routed to agent loop: 5-15s
- Pipeline queries: 1-2s
- Gap: 5-10x slower for follow-up

## Why It Is Slow
Agent loop (GPT-4o-mini) receives:
- System prompt (~3K tokens)
- 36-45 tools (~9K tokens, domain-injected)
- Chat history (~2K tokens)
- Tool results from data fetch (~3K tokens)
Total: ~18K tokens input, 2-3 iterations

## Optimization Options (ranked by impact/effort)

### Option A: Context-Hint Injection to Gemini Extraction (RECOMMENDED)
Instead of routing follow-up to agent loop, inject session context hint
into Gemini extraction prompt:
  "Previous topic: piutang (query_ar_outstanding). User may be asking follow-up."
This gives Gemini enough context to extract correct intent
→ stays in pipeline → 1-2s instead of 5-15s.

Effort: Small — add 1 line to extraction prompt when session context exists.
Risk: Medium — Gemini might still misclassify, need fallback to guard.
Pattern: extraction prompt = base + session_hint (if last_action_type exists).

### Option B: Lightweight Follow-Up Pipeline
For common follow-up patterns ("dari siapa", "yang mana", "tersebut"),
create dedicated pipeline handler that:
1. Read last_action_result from session
2. Apply follow-up logic (e.g., extract customer names from AR data)
3. Polish via Gemini
No agent loop needed.

Effort: Medium — need to map follow-up patterns to data extraction logic.
Risk: Low — deterministic, no LLM reasoning needed.
Limitation: Only works for pre-mapped patterns.

### Option C: Reduced Tool Set for Follow-Up Agent Loop
When follow-up guard triggers, load ONLY tools from the relevant domain
(e.g., AR tools only, not all 45). This reduces token count and iterations.

Effort: Small — filter tools by domain before agent loop.
Risk: Low — agent still has reasoning, just fewer tools.
Impact: Maybe 3-8s instead of 5-15s (modest improvement).
NOTE: Domain injection already does this partially (36 vs 90 tools).

### Option D: Cache Agent Loop Results for Common Follow-Ups
If user asks "dari siapa?" after piutang, the data is already in
last_action_result. Agent loop re-fetches it unnecessarily.
Inject last_action_result into agent loop context as pre-loaded data.

Effort: Medium — need to inject last_action_result into agent loop system prompt.
Risk: Low.
Impact: Saves 1 iteration (tool call + response), maybe 3-5s total.

## Recommendation
Start with Option A (lowest effort, biggest impact).
If A does not reliably fix extraction, add Option C as complement.
Option B for long-term coverage of common patterns.

## Priority
P3 — functional correctness is done, this is UX polish.
Can be batched with Batch 1 intent expansion.

---
*Created: 2026-03-30*
*Related: commit 62f68e19 (follow-up guard), milkyhoop-conversational v4.5*
