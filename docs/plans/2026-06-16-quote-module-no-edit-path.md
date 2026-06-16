# Ticket: Quote module has NO edit / duplicate hydration path

- Filed: 2026-06-16
- Severity: backlog (pre-existing, whole-module — NOT DP-specific)
- Origin: discovered during P2 Quote→DP→Invoice (FIX_P2_QUOTEDP)
- Candidate sprint: P3

## Problem
The Quote (Penawaran) module has no edit or duplicate hydration path at all.
`Quote/index.tsx` tracks `editQuote` / `duplicateQuote` in state but NEVER passes
either into `CreateQuote`. As a result:

- "Edit" on an existing quote opens a blank create form (no hydration of header,
  customer, items, payment fields, terms, etc.).
- "Duplicate" similarly does not pre-fill from the source quote.

This is a whole-module gap that pre-dates P2. It is structural (the wiring from
`index.tsx` -> `CreateQuote` for the edit/duplicate cases was never built), not a
data problem on any specific field.

## DP relationship (why this is flagged here but NOT a P2 blocker)
P2 added `dp_amount` (canonical) / `dp_percent` (helper) to the quote and the
backend `UpdateQuoteRequest` + `update_quote` path is already DP-edit-ready:
when an edit path IS eventually built, sending `dp_amount`/`dp_percent` in the
PATCH body will resolve + persist correctly (recomputed against the effective
total). So DP itself does NOT need additional edit code in P2 — it will "just
work" once the generic edit hydration is implemented.

Per owner decision (SP2 P2 #3): do NOT add edit code to P2. File this ticket.

## Scope of fix (when picked up, P3 candidate)
1. `Quote/index.tsx`: pass `editQuote` / `duplicateQuote` into `CreateQuote`.
2. `CreateQuote`: hydrate all header + line-item + payment + DP fields from the
   passed quote (edit = same id PATCH; duplicate = new draft, no id, status reset).
3. On submit:
   - edit  -> PATCH /api/quotes/{id} (UpdateQuoteRequest, incl. dp_amount/dp_percent)
   - dup   -> POST  /api/quotes      (CreateQuoteRequest, incl. dp_amount/dp_percent)
4. Verify DP round-trips through both edit and duplicate.

## Backend readiness (already done in P2)
- `UpdateQuoteRequest.dp_amount` / `.dp_percent` accepted.
- `update_quote` resolves canonical DP against effective total, touches dp_* cols
  only when client sends a dp_* field.
- `CreateQuoteRequest` / `create_quote` likewise persist DP.
