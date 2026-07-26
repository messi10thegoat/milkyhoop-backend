# BUG: bank_transactions.running_balance is a third balance cache, wrong under backdating

**Date:** 2026-07-26
**Severity:** MEDIUM (user-visible wrong balances in Kas & Bank when transactions are backdated — common in UMKM bookkeeping)
**Surfaced by:** FASE-4 step 0b (opening balance dated later than a backdated payment).

## Evidence
Two bank_transactions on the DP tenant's operating bank:
```
 transaction_type | amount       | transaction_date | running_balance | inserted
 opening          | +20.000.000  | 2026-07-26       | 20.000.000      | 1st
 payment_made     |  -3.500.000  | 2026-07-06       | 16.500.000      | 2nd
```
running_balance follows **INSERT order**, not **transaction_date order**. In date order the
payment (07-06) precedes the opening (07-26), so the opening row's stored 20.000.000 is not a
correct running balance at its own date, and the payment row's 16.500.000 assumes an opening
that (by date) hasn't happened yet. The column is internally inconsistent the moment any row
is backdated relative to insertion.

## Root cause
`trg_update_bank_balance` BEFORE INSERT → `update_bank_account_balance()`:
```sql
UPDATE bank_accounts SET current_balance = current_balance + NEW.amount ... RETURNING current_balance INTO v_new_balance;
NEW.running_balance := v_new_balance;
```
So running_balance is an **insert-order accumulator** seeded from `bank_accounts.current_balance`.
It is (a) written once at insert, never recomputed when a later insert is backdated, and
(b) **derived from `current_balance` — which Law 21 (BankSync Rule 6) declares DEPRECATED**
(account balance is journal-derived via `lb.ledger_balance`, bank_accounts.py:252/591).

## Why it's a real product bug (readers)
`running_balance` is **displayed**, not just stored:
- `kasbank_v2.py:140` returns `"running_balance": int(row["running_balance"])` to the Kas & Bank
  transaction list.
- `bank_reconciliation.py` uses it in statement matching (multiple sites).
A user who records a payment with an earlier date than the account's opening/other entries
(routine in UMKM catch-up bookkeeping) sees a wrong per-row running balance.

## Classification (answers the three questions)
- (a) Readers: Kas & Bank list + bank reconciliation — user-visible.
- (b) Recompute on backdated insert? **No** — write-once, insert-order, BEFORE-INSERT trigger.
- (c) Deprecated like current_balance? **No** — running_balance is NOT marked deprecated, yet it
  is derived from the deprecated current_balance and is displayed. **It is a third cache.**

## What is NOT affected
The **journal** is correct and date-agnostic; `compute_*`/ledger balances and the harness
BANK_GAP check (ledger net vs signed `SUM(bank_transactions.amount)`) are order-independent and
stay 0. Only the per-row `running_balance` display column is wrong.

## Fix options (backlog, owner-gated)
1. Compute running_balance in **transaction_date order** (and recompute affected rows on
   backdated insert/edit/void) — makes it a correct chronological cache.
2. Or **deprecate running_balance** like current_balance: compute the displayed running balance
   on read from the journal in date order, stop storing it.
3. Interim: the harness enforces correct chronology (explicit date plan, opening balance first)
   so this never masks a real bug during E2E.
