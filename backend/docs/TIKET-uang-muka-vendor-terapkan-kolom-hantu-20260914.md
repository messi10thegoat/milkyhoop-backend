# TICKET — Applying a vendor deposit to a bill (`POST /api/vendor-deposits/{id}/apply`) was dead from birth: ghost columns

**Status:** FIXED 14 Sep 2026 (pure fix: ghost column → real column, meaning unchanged).

## Walls (end-to-end trace, ROLLBACK, synthetic deposit)
The ticket queue named `vendor_deposits.py:718` (`paid_amount`). The trace showed it was **not the first wall**:
1. `bill['bill_number']`: the `bills` table has no `bill_number` (it's `invoice_number`) → **KeyError before any write**.
2. `UPDATE bills SET paid_amount …`: the column is `amount_paid`.
3. `… >= total_amount` and `SELECT total_amount - …`: the column is `amount`.

With all three replaced, the path runs to the end. Nothing further down the path broke after these three were fixed.

**Historical data:** `vendor_deposits` = 0 rows, `vendor_deposit_applications` = 0 rows → there is no historical data to touch. The feature has never been used successfully.

## Changes
- The three names above (journal description, line memo, cache, response).
- The bill lookup adds `AND tenant_id = $3`. Before, it matched only `id` + `vendor_id`. This is a safety belt: old code already rejected another tenant's bill because the vendor didn't match.
- The cache follows prior art `bill_payments.py:1366` (`amount_paid`, `amount`, status paid/partial).
- AP attribution already existed in `compute_ap_outstanding` branch 6 (V218, `vendor_deposit_applications` + `DEPOSIT_APPLICATION`). Nothing changed at the ledger level.

## Gate `scripts/gerbang_vd_apply.py` (one transaction, ROLLBACK, 0 left behind, count derived = 11)
- **new 11/11:**
  - apply 1,000 → 200;
  - compute outstanding −1,000 == AP GL −1,000;
  - cache +1,000 partial;
  - response `bill_number` = invoice_number and `bill_remaining` == compute;
  - deposit 1 application, partial;
  - over the remaining balance (on a partial bill) → 400 "exceeds bill balance";
  - pay off the rest → outstanding 0, cache == bill amount, paid.
- **old:** KeyError `bill_number` → 8 checks RED.
- **Sabotage** (apply still 200): cache dropped → cache/response/pay-off checks RED; cache doubled → the same three RED.
- Correction to my first run: "over the remaining balance → 400" passed for the WRONG reason ("Bill must be posted" after the bill was paid off). The check was moved before pay-off and now asserts the reason text.
- "Another tenant's bill → 400" does NOT tell old code from new (both reject). It is labelled that way.

## Seen during the trace (NOT fixed in this unit)
- `post_vendor_deposit` still writes **no bank_transactions mirror** (banksync skill footnote ³, a known forward bug). Posting a deposit through a bank account will create an R9 gap. Today there are 0 deposits, so the gap is 0.
- The bill cache only updates `status`, not `status_v2` (same as prior art bill_payments).


---

## UNIT 1b — bank mirror (R9) for post / refund / void (14 Sep 2026)

Unit 1 made the feature reachable, so the R9 gap is ours to close.
**Prior art** used as-is:
- customer deposit post `customer_deposits.py` FIX_R9_DEP_MIRROR:
  - reverse-lookup `bank_accounts` from the credited/debited CoA;
  - idempotent per journal;
  - helper `create_bank_transaction_for_journal`.
- customer deposit void `:2490`: `create_reversal_bank_transaction` from the ORIGINAL journal's bank_txn.

**Changes:**
- post → withdrawal −X (`VENDOR_DEPOSIT`);
- refund → deposit +R (`VENDOR_DEPOSIT_REFUND`);
- void → negating reversal mirror linked to the reversal journal. Before, it was gated on the `bank_account_id` field + a raw INSERT with running_balance 0 that looked up reference_type.

**The error shifted during the gate (reported, not patched blindly):** refund was dead from birth too, on ghost column `SELECT name FROM bank_accounts` (should be `account_name`). It died AFTER the journal was written, in the response. Same pure class → fixed in this unit, plus `tenant_id`. The other `ba.*` references in the file were checked: all `account_name`.

**Gate `scripts/gerbang_vd_cermin.py`** (savepoint per scenario, ROLLBACK, residue 0, count derived = 8). Measure = Δ `check_bank_sync_health` against the baseline:
- new 8/8:
  - A post via bank: Δgap 0 / Δorphans 0, 1 mirror −7,000;
  - B post→void: Δgap 0, negating mirror +6,000 on the reversal journal;
  - C post→refund: Δgap 0, mirror +3,000;
  - D post without an account (default Kas, not bank-linked): Δgap 0, 0 mirrors.
- old: A gap −7,000 + 1 orphan; B after post −6,000, after void 2 orphan journals; C refund crashes `column "name"` → **R9 GAP PROVEN**.
- sabotage: mirror sign +X → A/B/C RED; void reversal dropped → only B RED (+6,000, 1 orphan).
