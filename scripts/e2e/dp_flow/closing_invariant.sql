-- =============================================================================
-- closing_invariant.sql — DP-flow (Kaos Biru 30s) end-state ledger gate.
-- Written BEFORE step 0 on purpose: forces every ambiguity (bank source, opening
-- balance, PKP/tax) out into the open before data exists to hide a bug behind.
--
-- Usage:  psql -v ten="'kaos-biru-konveksi'" -v bankcoa="'<bank_accounts.coa_id>'" \
--              -v openbal=20000000 -f closing_invariant.sql
--   ten     = tenant_id (== slug)
--   bankcoa = bank_accounts.coa_id of the operating bank (BCA) — NOT the role account.
--   openbal = opening balance posted to that bank (for the DELTA check).
--
-- DESIGN DECISIONS baked in (from pre-step-0 review):
--  * ROLE-BASED for AR/AP/deposit/deferred/revenue/COGS/inventory (account_roles).
--  * BANK is the EXCEPTION: BankSync Rule 2 posts to bank_accounts.coa_id, not to the
--    BANK_OPERATIONAL role account (role -> 1-10200 "Bank"; payments hit 1-10201 "BCA").
--    Querying the role account would read a never-touched account -> ghost zero. So the
--    bank check reads :bankcoa and is a DELTA (end - opening), because opening balance
--    (Dr 1-10201 / Cr 3-50000 EQUITY) makes the absolute balance 21.500.000, not 1.5jt.
--    WRITTEN EXCEPTION (not an anomaly): the opening balance ALSO creates a matching
--    bank_transactions row (type 'opening', running_balance seeded to 20.000.000), so the
--    BankSync Rule 9 gap (ledger vs bank_transactions) = 0 from step 0. Opening balance is
--    INCLUDED in the gap and only SUBTRACTED for the business DELTA check — never excluded
--    from the gap. The per-step gap is enforced in drift_check.sql (BANK_GAP row).
--  * Gross profit == net cash ONLY in delta terms, not absolute.
--  * Non-PKP tenant -> zero VAT lines expected (VAT roles resolve to None).
--
-- Every value is journal-derived (Iron Law 16). POSTED + reversed_by_id IS NULL only.
-- Each SELECT prints a PASS/FAIL so the gate is readable at a glance.
-- =============================================================================
\set ON_ERROR_STOP on

-- Helper expressions repeated below all filter to effective (non-reversed) POSTED lines.

-- 1) ROLE-BASED BALANCES (net debit-credit on the role's account) --------------
WITH rb AS (
  SELECT ar.role_key,
         COALESCE(SUM(jl.debit - jl.credit),0) AS net
  FROM account_roles ar
  JOIN chart_of_accounts c ON c.id = ar.account_id
  LEFT JOIN journal_lines jl ON jl.account_id = ar.account_id
  LEFT JOIN journal_entries je ON je.id = jl.journal_id
       AND je.status='POSTED' AND je.reversed_by_id IS NULL AND je.tenant_id = :ten
  WHERE ar.tenant_id = :ten
    AND ar.role_key IN ('AR_TRADE','AP_TRADE','CUSTOMER_DEPOSIT_LIABILITY',
                        'REVENUE_DEFERRED','INVENTORY_MERCHANDISE',
                        'REVENUE_SALES_GOODS','COGS_SALES')
  GROUP BY ar.role_key
),
expect(role_key, want, kind) AS (VALUES
  ('AR_TRADE',                   0::numeric, 'zero'),
  ('AP_TRADE',                   0,          'zero'),
  ('CUSTOMER_DEPOSIT_LIABILITY', 0,          'zero'),  -- liability nets to 0 (credit then debit on apply)
  ('REVENUE_DEFERRED',           0,          'zero'),  -- liability nets to 0 (credit then debit on delivery)
  ('INVENTORY_MERCHANDISE',      0,          'zero'),  -- all 100 pcs bought were... (see note)
  ('REVENUE_SALES_GOODS',   -5000000,        'revenue'),-- REVENUE normal credit -> net (D-C) = -5,000,000
  ('COGS_SALES',             3500000,        'cogs')    -- COGS normal debit  -> net (D-C) = +3,500,000
)
SELECT e.role_key,
       COALESCE(rb.net,0) AS actual,
       e.want AS expected,
       CASE WHEN COALESCE(rb.net,0) = e.want THEN 'PASS' ELSE 'FAIL' END AS result
FROM expect e LEFT JOIN rb ON rb.role_key = e.role_key
ORDER BY e.role_key;

-- NOTE on INVENTORY_MERCHANDISE=0: the Kaos Biru spec BUYS 100 pcs @ 35.000 and SELLS the
-- whole lot (100 pcs @ 50.000) -> net inventory returns to 0. Revenue 5.000.000, COGS
-- 3.500.000, gross profit 1.500.000, all consistent. If a later step ships <100, this want
-- becomes (100-sold)*WAC — so it is a deliberate decision (sell=100), not an accident.

-- 2) BANK DELTA (bank_accounts.coa_id, NOT role) -------------------------------
SELECT 'BANK_DELTA' AS check,
       COALESCE(SUM(jl.debit - jl.credit),0) AS bank_net_movement,
       COALESCE(SUM(jl.debit - jl.credit),0) - (:openbal)::numeric AS delta_excl_opening,
       CASE WHEN COALESCE(SUM(jl.debit - jl.credit),0) - (:openbal)::numeric = 1500000
            THEN 'PASS' ELSE 'FAIL' END AS result
FROM journal_lines jl
JOIN journal_entries je ON je.id = jl.journal_id
WHERE je.tenant_id = :ten AND je.status='POSTED' AND je.reversed_by_id IS NULL
  AND jl.account_id = :bankcoa;   -- bank_accounts.coa_id (BankSync Rule 2), NOT the role account

-- 3) GROSS PROFIT = revenue - COGS = 1,500,000 --------------------------------
WITH r AS (
  SELECT COALESCE(-SUM(CASE WHEN ar.role_key='REVENUE_SALES_GOODS' THEN jl.debit-jl.credit END),0) AS revenue,
         COALESCE( SUM(CASE WHEN ar.role_key='COGS_SALES'          THEN jl.debit-jl.credit END),0) AS cogs
  FROM account_roles ar
  JOIN journal_lines jl ON jl.account_id = ar.account_id
  JOIN journal_entries je ON je.id = jl.journal_id
       AND je.status='POSTED' AND je.reversed_by_id IS NULL AND je.tenant_id=:ten
  WHERE ar.tenant_id=:ten AND ar.role_key IN ('REVENUE_SALES_GOODS','COGS_SALES')
)
SELECT 'GROSS_PROFIT' AS check, revenue, cogs, revenue-cogs AS gross_profit,
       CASE WHEN revenue-cogs = 1500000 THEN 'PASS' ELSE 'FAIL' END AS result FROM r;

-- 4) TRIAL BALANCE — total debit == total credit (all POSTED effective lines) --
SELECT 'TRIAL_BALANCE' AS check,
       SUM(jl.debit) AS tot_debit, SUM(jl.credit) AS tot_credit,
       CASE WHEN SUM(jl.debit)=SUM(jl.credit) THEN 'PASS' ELSE 'FAIL' END AS result
FROM journal_lines jl JOIN journal_entries je ON je.id=jl.journal_id
WHERE je.tenant_id=:ten AND je.status='POSTED' AND je.reversed_by_id IS NULL;

-- 5) AR / AP DRIFT = 0 (journal-derived compute_* vs the role balance) ----------
--    compute_ar_outstanding / compute_ap_outstanding aggregate to per-doc; here we assert
--    the total outstanding is 0 (fully settled) at close.
SELECT 'AR_OUTSTANDING' AS check, COALESCE(SUM(outstanding),0) AS total,
       CASE WHEN COALESCE(SUM(outstanding),0)=0 THEN 'PASS' ELSE 'FAIL' END AS result
FROM compute_ar_outstanding(:ten);
SELECT 'AP_OUTSTANDING' AS check, COALESCE(SUM(outstanding),0) AS total,
       CASE WHEN COALESCE(SUM(outstanding),0)=0 THEN 'PASS' ELSE 'FAIL' END AS result
FROM compute_ap_outstanding(:ten);

-- 6) VAT LINES = 0 (non-PKP) --------------------------------------------------
SELECT 'VAT_LINES' AS check, COUNT(*) AS vat_line_count,
       CASE WHEN COUNT(*)=0 THEN 'PASS' ELSE 'FAIL' END AS result
FROM journal_lines jl
JOIN journal_entries je ON je.id=jl.journal_id
JOIN account_roles ar ON ar.account_id=jl.account_id AND ar.tenant_id=:ten
WHERE je.tenant_id=:ten AND je.status='POSTED' AND je.reversed_by_id IS NULL
  AND ar.role_key IN ('VAT_INPUT','VAT_OUTPUT');

-- 7) HASH CHAIN INTACT --------------------------------------------------------
-- The per-tenant journal hash chain: every entry's prev_hash must match the prior
-- entry's hash, in chain_sequence order. Assert zero breaks.
WITH ch AS (
  SELECT chain_sequence, content_hash, previous_hash,
         LAG(content_hash) OVER (ORDER BY chain_sequence) AS expected_prev
  FROM journal_entries WHERE tenant_id=:ten
)
SELECT 'HASH_CHAIN' AS check, COUNT(*) FILTER (WHERE chain_sequence>1 AND previous_hash IS DISTINCT FROM expected_prev) AS breaks,
       CASE WHEN COUNT(*) FILTER (WHERE chain_sequence>1 AND previous_hash IS DISTINCT FROM expected_prev)=0
            THEN 'PASS' ELSE 'FAIL' END AS result
FROM ch;
