# BUG: POST /api/customers silently drops the `code` field (vendors/items keep it)

**Date:** 2026-07-26
**Severity:** LOW-MEDIUM (data-integrity / integration surprise; no financial mis-post)
**Surfaced by:** FASE-4 step -1 idempotency test (re-running provisioning).

## What
`POST /api/customers` with a body containing `"code":"CUS-KB-01"` creates the customer but
**persists `code = NULL`** (and `name = NULL`); it stores the label in `nama` (the Bahasa
Indonesia column). The `code` column exists on `customers` and is populated correctly for
**vendors** and **items** (English-schema tables) — customers are the exception.

Observed row after create:
```
 id=f900e5b4... | code=(null) | name=(null) | nama='Toko Merdeka' | email set
```

## Why it matters
- **Idempotency / integration breaks silently:** any client that looks a customer up by the
  `code` it just sent will never find it, then re-create → hits the unique-email constraint →
  opaque failure. (This is exactly how the harness re-run failed before we re-keyed on email.)
- **Inconsistent contract:** vendors/items honor `code`; customers accept it and discard it.
  A caller cannot rely on `code` round-tripping per-entity.

## Root cause (to confirm in the handler)
`customers.py` INSERT column list does not map the request `code` (and `name`) into the
persisted columns; it maps the display value into `nama` only. The Indonesian/English schema
split (`nama` vs `name`, `code` present-but-unwritten) is the underlying drift.

## Fix options
1. Map request `code` → `customers.code` on create/update (and `name`/`nama` consistently),
   matching vendors/items. Preferred — makes the contract uniform.
2. If `code` is intentionally unsupported for customers, **reject it** (422) instead of
   silently dropping, and document customers as code-less.

## Harness workaround (in place)
`step_-1_provision.sh` keys customer idempotency on the persisted `email`, not `code`, with a
comment pointing here. The product bug remains live.

---
## CORRECTION (2026-07-26): code is not DROPPED — it is mapped to `nomor_member`
Re-inspected the persisted row: request `"code":"CUS-KB-01"` landed in **`customers.nomor_member`**
('CUS-KB-01'), while `customers.code` and `customers.name` are NULL (label stored in `nama`). So the
endpoint does not silently discard `code` — it maps it to the wrong column (`nomor_member`) relative
to vendors/items (which use `code`). Net effect for a caller keying on `code` is the same (code stays
NULL → lookup misses), so the email-keyed idempotency workaround stands, but the accurate defect is a
**field-mapping inconsistency** (code→nomor_member), not a dropped field. Fix should map request
`code`→`customers.code` for parity with vendors/items.
