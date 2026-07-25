# Step C — compute_ap_outstanding vendor-deposit fix (V218): standalone proof

**DB:** `milkydb_c_test` (clone of LIVE `milkydb` — exact parity, `schema_migrations` 209 pre-existing).
**Migration applied via new pipeline:** `migrate.sh apply` → `newly-applied=1 already-present=209`, tracked `applied_by='runner'` (first real pending migration end-to-end, not backfill).
**Fixtures:** `scripts/e2e/step_c_vendor_deposit_ap/seed.sql` + `verify.sql`.

## The bug
`apply_vendor_deposit` posts `Dr 2-10100 (PAYABLE) / Cr 1-10800`, settling the bill. But `compute_ap_outstanding` built `paid_amount` only from `bill_payment_allocations` + `vendor_credit_applications`. A `DEPOSIT_APPLICATION` journal is in neither → its PAYABLE debit was counted nowhere → **AP overstated by the applied amount** (AR mirror already fixed live: Branch 3 / FIX_P35_ARCANON).

## 5-case regression matrix (before → after V218)

| Case | Bill | Setup | compute_ap BEFORE | compute_ap AFTER | Expected | ✓ |
|------|------|-------|------:|------:|------:|---|
| 1 NULL-handling | BILL-1 | obligation 1.000.000, no deposit | 1.000.000 | 1.000.000 | 1.000.000 (must NOT vanish) | ✓ |
| 2 partial | BILL-2 | oblig 1.000.000, deposit 400.000 | 1.000.000 (bug) | 600.000 | 600.000 | ✓ |
| 3 full | BILL-3 | oblig 1.000.000, deposit 1.000.000 | 1.000.000 (bug) | dropped (0) | 0 → dropped | ✓ |
| 4 all-sources | BILL-4 | oblig 1.000.000, payment 200.000, VC 100.000, deposit 300.000 | 700.000 (bug) | 400.000 | 400.000 | ✓ |
| 5 reversed | BILL-5 | oblig 1.000.000, deposit 500.000 REVERSED (Law-2) | 1.000.000 | 1.000.000 | 1.000.000 | ✓ |

- **Case 1** proves the `LEFT JOIN` + `COALESCE` on every term: the bill with no deposit is neither NULL-ed nor dropped from AP aging (silent-fallback class averted).
- **Case 4** proves **empirically** zero overlap across all four debit sources (obligation/payment/VC/deposit each counted once).
- **Case 5** proves the Law-2 reversal contract: setting `reversed_by_id` on the apply journal removes it from AP.

## Invariant 1 — compute_ap TOTAL vs raw ledger PAYABLE (all POSTED; reversal pairs net)
| | compute_ap total | raw ledger PAYABLE | drift |
|--|------:|------:|------:|
| BEFORE | 4.700.000 | 3.000.000 | **1.700.000** (= 400k+1.000k+300k bug) |
| AFTER  | 3.000.000 | 3.000.000 | **0** |

## Invariant 2 — compute_ap (per bill) vs inline `get_bill_remaining_from_journal`
| Bill | compute_ap | inline | diff |
|------|------:|------:|------:|
| BILL-1 | 1.000.000 | 1.000.000 | 0 |
| BILL-2 | 600.000 | 600.000 | 0 |
| BILL-3 | 0 | 0 | 0 |
| BILL-4 | 400.000 | 400.000 | 0 |
| BILL-5 | 1.000.000 | 500.000 | **500.000** |

**Finding (invariant did its job):** on the reversed case the inline calc `get_bill_remaining_from_journal` (vendor_deposits.py:54-100) disagrees by 500.000 because it filters `je.status='POSTED'` but **omits `je.reversed_by_id IS NULL`** — it keeps counting a reversed settlement. `compute_ap` (with the filter) is correct; the inline calc is wrong on reversal. Dormant today (no vendor-deposit un-apply endpoint) but a landmine once reversal ships. Reinforces the "retire the inline calc, use the canonical function" follow-up.

## UNIQUE precondition (Change 1) — armed
- Pre-check: 0 rows sharing a `journal_id` on either table → safe to ADD.
- `uq_cda_journal_id`, `uq_vda_journal_id` created.
- Negative test: a second `vendor_deposit_applications` row reusing an existing `journal_id` → `ERROR: duplicate key value violates unique constraint "uq_vda_journal_id"`. Fan-out is now structurally impossible, not convention.

## Schema-vs-code drift found while seeding (filed, out of Step-C scope)
- **`apply_vendor_deposit` writes non-existent bill columns**: `UPDATE bills SET paid_amount=..., total_amount=...` — `bills` has `amount_paid` (not `paid_amount`) and **no `total_amount`**. This UPDATE 500s → the endpoint has never successfully run against this schema. Same dead-end family as the customer-side P1 fixes. Vendor-deposit apply needs a column-drift pass before it can be exercised live.

## Provisioning dry-run (FASE-4 preview) — what broke
Seeding a minimal tenant surfaced, in order: (a) accounting tables FK `tenant_id → "Tenant"(id)` for `chart_of_accounts`/`fiscal_periods` (must create Tenant first); (b) `vendor_credits.reason` CHECK enum (`return|pricing_error|discount|damaged|other`); (c) the `bills` column drift above. None block Step C; all are provisioning-path notes for FASE 4.

---

# CORRECTIONS (post-review)

## Ralat verdict (was "active latent")
The AP bug is **NOT "active latent" — it is UNREACHABLE behind a broken write path.**
`apply_vendor_deposit` has never successfully run once (it writes `bills.paid_amount`
/`bills.total_amount`, columns that do not exist → 500). That is *why* the bug was
never exposed in production.

**Explicit decision:** the vendor-deposit feature is **dead end-to-end**. The DP
target flow does not use it → **conscious DEFER**, filed here so it does not hang
undecided. **V218 still ships and is still correct**: the `UNIQUE(journal_id)`
constraints protect the *live* AR side (customer_deposit_applications / Branch 3),
and the AP read path is ready the moment the vendor write path is repaired.

## Refinement of the Invariant-2 (inline) finding — is it active? NO (checked)
Question raised: `get_bill_remaining_from_journal` filters `status=POSTED` without
`reversed_by_id IS NULL`; does that mis-state a LIVE flow (e.g. voided bill payment)?

Checked `void_bill_payment` (bill_payments.py:1587): the reversing journal is created
with **`source_type=BILL_PAYMENT` and `source_id=payment_id`** (same source as the
original), posted POSTED, and `reversed_by_id` set on the original. Consequence:
- inline counts BOTH original (Dr PAYABLE) and reversal (Cr PAYABLE) via its
  `BILL_PAYMENT` branch (which keys on `source_id`) → they **net to 0** → correct.
- compute_ap excludes the original via `reversed_by_id IS NULL`; the reversal is not
  `bpv2.journal_id` → not in payment_debits → also nets to 0 → correct.

So the inline is correct for voided bill payments (and, by the same source-reuse
netting, voided vendor credits). **The divergence is genuinely dormant** — but the
precise reason is sharper than "no reversal endpoint": the inline handles reversals
by *source-reuse netting*, and the **`DEPOSIT_APPLICATION` branch is the one branch
that keys on `je.id IN (vda.journal_id)` instead of `source_id`** — a reversal journal
is never in `vendor_deposit_applications`, so that branch **can never net a reversal**.
The moment vendor-deposit un-apply is implemented (in ANY style) the inline breaks.
Same conclusion, same owner: it lives behind the dead vendor-deposit write path.

## Consumers of get_bill_remaining_from_journal (question b)
NOT a user-facing display. It is **copy-pasted in 3 routers** (bill_payments.py:123,
vendor_deposits.py:54, vendor_credits.py:116 — DRY/drift hazard) and consumed in
WRITE paths to compute `remaining_before` for an allocation: bill_payments.py:1245
(live), vendor_credits.py:1338 (live), vendor_deposits.py:604 (dead). AP/AR *aging*
uses compute_ap/compute_ar, not this function. No user-facing exposure from the
divergence, and the live write-path consumers do not hit the DEPOSIT_APPLICATION-
reversal case.

---

# CORRECTIONS (round 2)

## Case 5 covers convention (ii) ONLY
The fixture set reversed_by_id on the original apply journal by hand — i.e. it exercised
ONLY the reversed_by_id convention, the one this CTE needs. It did NOT exercise the
dominant source-reuse-netting convention (i). Had un-apply been written in style (i) — a
reversal journal with source_type='DEPOSIT_APPLICATION' NOT inserted into
vendor_deposit_applications — the CTE JOIN would never see it, the original would stay
counted, and the deposit would settle the bill forever, silently. V218's REVERSAL
CONTRACT comment now states this (SET reversed_by_id mandatory; do NOT copy
void_bill_payment here). The green is narrower than it looked: correct GIVEN (ii).

## The three copies of get_bill_remaining_from_journal HAVE diverged (item 2)
Not identical — a 4th instance of the ghost/drift class, two on live write paths:
- bill_payments.py:123 (live at :1245) — AP by ROLE: account_roles.role_key='AP_TRADE' (Fase D2.3)
- vendor_credits.py:116 (live at :1338) — same role form, tagged Fase D2.2 (version drift)
- vendor_deposits.py:54 (dead at :604) — STILL hardcoded coa.account_code='2-10100' (Law 27 gap)
On a tenant whose AP_TRADE role != code 2-10100, the copies compute against different
accounts. Retirement collapses all three onto compute_ap/compute_ar — not re-sync 3 copies.

## Reframe of consumer analysis (item 3)
"No user-facing exposure" is true for URGENCY but misleading in kind. This function fills
remaining_before for allocations = audit trail + likely overpayment guard. A wrong value
does not paint a bad number on screen — it can REJECT a legitimate payment or corrupt the
audit trail. Not a reason to escalate; a reason to ACCELERATE retirement onto canonical fns.

---

# ROUND 4 — V219 (quotes columns), oracle-3, CI ratchet, audit-matrix corrections

## FE oracle authority (cond 1)
useQuoteForm.ts + both ItemFormSheet.tsx are UNMODIFIED on Mac (= git master). The recovered
server has NO FE source tree (/root/milkyhoop/frontend/web missing; useQuoteForm found nowhere)
-> only Mac/master exist and are identical for these files; the audit's "72 uncommitted server FE
files" does not apply to this server (FE served as a prebuilt bundle).
RAISED to FASE-5 prerequisite: UI E2E must run against a FE built from a PINNED git commit;
the deployed bundle's provenance cannot be diffed on the server.

## items autocomplete — DO NOT STRIP (cond 2 reversed the earlier plan)
SalesInvoice CreateInvoice/ItemFormSheet.tsx CONSUMES sales_tax_id/sales_tax_name/sales_tax_rate
from /items/autocomplete (types L117-119; prefills invoice-line tax L376-378). Per the FE rule this
is built-but-unmigrated (like quotes), NOT a strip. products has sales_tax(varchar), no sales_tax_id.
DECISION DEFERRED, NOT in V219 scope. Options: (a) add products.sales_tax_id/purchase_tax_id (uuid FK
tax_codes) + populate on create + map; (b) return NULL-shaped tax fields to fix the 500 while keeping
the FE contract (DP flow is non-PKP -> tax null anyway; the broken picker is the real blocker).

## V219 proof (cond 3)
5 columns added; the EXACT quotes.py:502 INSERT (all 27 params + status literal) now succeeds
(INSERT 0 1, valid discount_type) -> column-clean, NO second ghost IN THE STATEMENT. Residual
scanner-invisible surfaces NOT yet tested: quote_items INSERT + GET/response serializer SELECT.
Value-CHECK noted: quotes.discount_type IN {fixed, percentage} only -> quote-create 500s if FE sends
'none'/empty; verify FE default in FASE 4 (value concern, not schema).

## Oracle-3 vs LIVE copy (round-3 condition, now closed)
Re-ran the invariant with the LIVE per-bill code (bill_payments.py role-based AP_TRADE; account_roles
seeded). Per bill: BILL-1..4 == compute_ap (1M/600k/0/400k); BILL-5 500k vs 1M (reversed-case
divergence reproduces). V218 confirmed against the code users actually hit. The earlier caveat
(case-5 oracle used the DEAD copy) is closed.

## CI ratchet (approved) — scripts/schema-contract/
schema_scan.py --signatures + ci_check.sh + baseline_signatures.txt (147 distinct signatures).
Proven: clean -> exit 0; a new ghost -> exit 1 (prints the added signature). Grandfathers the existing
set, fails only on INCREASE, baseline shrinks on cleanup. Prereq: cols.txt refreshed from target DB.
Portability (hardcoded /root paths) = follow-up.

## AUDIT-MATRIX CORRECTIONS (cond 4)
- Step 1 Penawaran: earlier verdict READY is WRONG. Evidence was "[SQL] quotes has N columns" =
  schema-exists used as a proxy for works. Reality: static INSERT, 500 on ANY payload, 0 quotes rows
  in EVERY DB -> the Penawaran module has NEVER created a single quote. (Addressed by V219 + residual
  checks pending.)
- RALAT B4 (2nd time): "required_deposit quote-anchored" does NOT hold — quotes never had rows ->
  dp_amount/dp_percent never populated -> DP was never anchored to anything. Required-deposit-at-quote
  has never existed in practice.
- After V219, step 1 is the RISKIEST E2E step: the only one never executed. Anticipate follow-on
  findings (quote_items, serializer, discount_type default).
