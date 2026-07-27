> **RESOLVED 2026-07-27 (BATCH1 B1).** Fixed: `get_quote_detail` now maps payment_bank_name/_account_number/_account_holder into QuoteDetail (model already declared them). REFRAME: the 'PDF never renders payment_*/dp_*' part was STALE — `quote.html` already renders both the Rekening Pembayaran and Uang Muka blocks (FIX_P2_QUOTEDP), verified via GET /pdf (200 + real PDF + supplying data). Regression-gated by harness step 1 (C2/C3).

# BUG: GET /api/quotes/{id} omits 3 of the 5 V219 quote columns (write-only read gap)

**Date:** 2026-07-26
**Severity:** MEDIUM (silent data loss on display — saved data never shown back)
**Surfaced by:** FASE-4 step 1 read-back (the FE navigates to the quote detail after submit).

## What
`POST /api/quotes` persists all five V219 columns; the DB row is correct:
```
payment_bank_name='Bank BCA' | payment_account_number='1111222233' | payment_account_holder='Kaos Biru Konveksi'
opening_text=... | closing_text=...
```
But `GET /api/quotes/{id}` (200 OK) returns:
```
opening_text ✓  closing_text ✓  dp_amount ✓  dp_percent ✓
payment_bank_name=None  payment_account_number=None  payment_account_holder=None   ← DROPPED
```
The `QuoteResponse` builder (quotes.py detail handler, ~line 420-452) maps opening_text/closing_text
but **never maps payment_bank_name/payment_account_number/payment_account_holder**. They are
write-only from the read path's perspective.

## Why it matters
The FE quote detail page (and PDF/print, which reads the same detail) shows **blank bank-payment
info** even though the user entered and saved it. Worse than a 500: silent — the data is in the DB,
just never returned. V219 added columns the create path writes and the detail path can't display.

## Fix
Add the three `payment_*` fields to the quote detail SELECT (if missing) and to the `QuoteResponse`
mapping, alongside opening_text/closing_text. Verify list + PDF paths too.

## Contrast
This is the OPPOSITE of a schema-drift-missing-column: the columns exist and are written; the read
model is incomplete. Same family as other read-path drift findings in this effort — write path and
read path disagree on the column set.
