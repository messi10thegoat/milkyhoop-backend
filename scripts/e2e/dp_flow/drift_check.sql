-- =============================================================================
-- drift_check.sql — AR/AP drift gate, called AFTER EVERY step (0..9), not just at close.
-- Drift = (control-account ledger balance) - (journal-derived compute_* total). Iron Law
-- 16/29: these must be identical at all times; any nonzero drift = a phantom AR/AP bug the
-- instant it appears, so we catch it at the step that introduced it.
--
-- Usage: psql -v ten="'kaos-biru-konveksi'" -f drift_check.sql
-- AR control = AR_TRADE role account; AP control = AP_TRADE role account.
-- Both filtered to POSTED + reversed_by_id IS NULL (effective) lines.
-- =============================================================================
\set ON_ERROR_STOP on

WITH ar_ledger AS (
  SELECT COALESCE(SUM(jl.debit - jl.credit),0) AS bal
  FROM account_roles ar
  JOIN journal_lines jl ON jl.account_id = ar.account_id
  JOIN journal_entries je ON je.id = jl.journal_id
       AND je.status='POSTED' AND je.reversed_by_id IS NULL AND je.tenant_id=:ten
  WHERE ar.tenant_id=:ten AND ar.role_key='AR_TRADE'
),
ar_compute AS (SELECT COALESCE(SUM(outstanding),0) AS tot FROM compute_ar_outstanding(:ten)),
ap_ledger AS (
  SELECT COALESCE(SUM(jl.credit - jl.debit),0) AS bal   -- AP is a credit-normal control
  FROM account_roles ar
  JOIN journal_lines jl ON jl.account_id = ar.account_id
  JOIN journal_entries je ON je.id = jl.journal_id
       AND je.status='POSTED' AND je.reversed_by_id IS NULL AND je.tenant_id=:ten
  WHERE ar.tenant_id=:ten AND ar.role_key='AP_TRADE'
),
ap_compute AS (SELECT COALESCE(SUM(outstanding),0) AS tot FROM compute_ap_outstanding(:ten))
SELECT 'AR' AS side, (SELECT bal FROM ar_ledger) AS ledger, (SELECT tot FROM ar_compute) AS compute,
       (SELECT bal FROM ar_ledger)-(SELECT tot FROM ar_compute) AS drift,
       CASE WHEN (SELECT bal FROM ar_ledger)-(SELECT tot FROM ar_compute)=0 THEN 'PASS' ELSE 'FAIL' END AS result
UNION ALL
SELECT 'AP', (SELECT bal FROM ap_ledger), (SELECT tot FROM ap_compute),
       (SELECT bal FROM ap_ledger)-(SELECT tot FROM ap_compute),
       CASE WHEN (SELECT bal FROM ap_ledger)-(SELECT tot FROM ap_compute)=0 THEN 'PASS' ELSE 'FAIL' END;

-- BANK GAP (BankSync Rule 9): for EVERY bank account, the journal net on its coa_id must
-- equal the latest bank_transactions.running_balance. Opening balance is INCLUDED (it creates
-- a bank_transaction of type 'opening'), so no exclusion is needed — the gap must be 0 from
-- the very first step. A bank whose journal moved but has no matching bank_transaction (or a
-- stale running_balance) surfaces here immediately, not at close.
-- NOTE: compare ledger vs SIGNED SUM(bank_transactions.amount), NOT the latest
-- running_balance. running_balance is seeded in insertion order, but entries are BACKDATED
-- (opening dated today, payments dated earlier), so "latest by transaction_date" is wrong.
-- bank_transactions.amount is already signed (payment_made = -3.500.000), so the sum is
-- order-independent and equals the journal net on the bank's coa when the gap is 0.
WITH per_bank AS (
  SELECT ba.id, ba.coa_id,
    COALESCE((SELECT SUM(jl.debit-jl.credit) FROM journal_lines jl
              JOIN journal_entries je ON je.id=jl.journal_id
              WHERE je.tenant_id=:ten AND je.status='POSTED' AND je.reversed_by_id IS NULL
                AND jl.account_id=ba.coa_id),0) AS ledger,
    COALESCE((SELECT SUM(bt.amount) FROM bank_transactions bt
              WHERE bt.bank_account_id=ba.id AND bt.status='POSTED'),0) AS bank_txn_sum
  FROM bank_accounts ba WHERE ba.tenant_id=:ten
)
SELECT 'BANK_GAP' AS side, SUM(ledger) AS ledger, SUM(bank_txn_sum) AS compute,
       SUM(ledger-bank_txn_sum) AS drift,
       CASE WHEN SUM(ledger-bank_txn_sum)=0 THEN 'PASS' ELSE 'FAIL' END AS result
FROM per_bank;

-- rc GATE (PRIMARY detection): with \set ON_ERROR_STOP on, a division-by-zero here makes psql
-- exit non-zero when AR drift, AP drift, or BANK_GAP is nonzero. run_all.sh keys on this exit
-- code; the PASS/FAIL text above is human-facing + safety belt. Denominator depends on a column
-- (failcnt) so Postgres cannot constant-fold the 1/0 at plan time when failcnt=0.
SELECT 1 / (CASE WHEN failcnt = 0 THEN 1 ELSE 0 END) AS drift_rc_gate
FROM (
  SELECT
    (CASE WHEN (
        (SELECT COALESCE(SUM(jl.debit-jl.credit),0) FROM account_roles ar
           JOIN journal_lines jl ON jl.account_id=ar.account_id
           JOIN journal_entries je ON je.id=jl.journal_id AND je.status='POSTED'
                AND je.reversed_by_id IS NULL AND je.tenant_id=:ten
         WHERE ar.tenant_id=:ten AND ar.role_key='AR_TRADE')
      - (SELECT COALESCE(SUM(outstanding),0) FROM compute_ar_outstanding(:ten))
      ) <> 0 THEN 1 ELSE 0 END)
  + (CASE WHEN (
        (SELECT COALESCE(SUM(jl.credit-jl.debit),0) FROM account_roles ar
           JOIN journal_lines jl ON jl.account_id=ar.account_id
           JOIN journal_entries je ON je.id=jl.journal_id AND je.status='POSTED'
                AND je.reversed_by_id IS NULL AND je.tenant_id=:ten
         WHERE ar.tenant_id=:ten AND ar.role_key='AP_TRADE')
      - (SELECT COALESCE(SUM(outstanding),0) FROM compute_ap_outstanding(:ten))
      ) <> 0 THEN 1 ELSE 0 END)
  + (CASE WHEN (
        SELECT COALESCE(SUM(
          COALESCE((SELECT SUM(jl.debit-jl.credit) FROM journal_lines jl
                    JOIN journal_entries je ON je.id=jl.journal_id
                    WHERE je.tenant_id=:ten AND je.status='POSTED' AND je.reversed_by_id IS NULL
                      AND jl.account_id=ba.coa_id),0)
        - COALESCE((SELECT SUM(bt.amount) FROM bank_transactions bt
                    WHERE bt.bank_account_id=ba.id AND bt.status='POSTED'),0)),0)
        FROM bank_accounts ba WHERE ba.tenant_id=:ten
      ) <> 0 THEN 1 ELSE 0 END) AS failcnt
) g;
