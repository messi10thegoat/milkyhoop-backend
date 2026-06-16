# TICKET: void-path breaks POSTED hash-chain (pre-existing, non-blocking)

**Filed:** 2026-06-16 (during P1 Quote→DP→Invoice, customer-deposit foundation)
**Severity:** medium · **Blocking P1:** NO · **Owner-acked out-of-scope**

## Symptom
`verify_chain_integrity` reports a chain break after a document is VOIDed.

## Root cause
The existing void endpoints set the original `journal_entries.status=VOID`, which
removes that entry from the POSTED chain that `verify_chain_integrity` walks → the
hash chain has a gap.

## Contrast (the correct pattern — already used by P1 un-apply)
P1 deposit un-apply is chain-safe: it keeps the original journal `status=POSTED`
and marks it reversed via `reversed_by_id` + posts a symmetric reversal with
`reversal_of_id` set. `is_effective_journal()` drops both (reversed + reversal),
so the net is zero WITHOUT removing anything from the POSTED chain. Result:
`verify_chain_integrity` stays valid.

## Proposed fix (separate work)
Migrate the void paths to the same chain-safe model: do NOT flip the original to
`status=VOID`; instead keep it POSTED, set `reversed_by_id`, and post a reversal
journal with `reversal_of_id`. Audit every void endpoint
(sales_invoice, bill, expense, receive_payment, bill_payment, credit_note,
vendor_credit, deposit, stock_adjustment) per the universal state machine.

## Notes
- Discovered while building deposit un-apply (chain-safe by design).
- Does not affect ledger correctness (VOID already excludes the entry); it only
  breaks the hash-chain integrity check.
