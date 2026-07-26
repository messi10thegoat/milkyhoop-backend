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
