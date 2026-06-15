-- =============================================================================
-- Backfill: fiscal_years + fiscal_periods for onboarding-completeness orphans
-- Date: 2026-06-15
-- Context: Class-2 onboarding-completeness fix. These tenants were created
--          before create_tenant_and_user() provisioned a fiscal year, so they
--          have POSTED journal_entries but ZERO fiscal_periods.
--
-- SCOPE (intentional):
--   - milkytest        (test tenant, ~15 journals, 0 periods)
--   - anthonius-iwan   (test tenant, ~12 journals, 0 periods)
--
-- DEFERRED (do NOT backfill here):
--   - grapgrap         — complex multi-year history (2020-2026) + on hold.
--                        Requires per-year fiscal_year creation, handled
--                        separately. Intentionally excluded from this file.
--
-- create_fiscal_year_with_periods(p_tenant_id text, p_name text,
--   p_start_month int, p_year int, p_created_by uuid DEFAULT NULL)
--   -> creates 1 fiscal_year + 12 monthly OPEN periods. RAISEs on overlap.
--
-- Run inside a transaction; review the verify output before COMMIT.
-- =============================================================================

BEGIN;

-- Provision Tahun Buku 2026 (Jan start, 12 OPEN periods) for each orphan.
SELECT create_fiscal_year_with_periods('milkytest',      'Tahun Buku 2026', 1, 2026, NULL);
SELECT create_fiscal_year_with_periods('anthonius-iwan', 'Tahun Buku 2026', 1, 2026, NULL);

-- -----------------------------------------------------------------------------
-- VERIFY 1: each backfilled tenant now has exactly 12 fiscal_periods (1 year).
-- Expect 12 periods + 1 year per tenant.
-- -----------------------------------------------------------------------------
SELECT t.tenant_id,
       COALESCE(fy.n_years, 0)   AS fiscal_years,
       COALESCE(fp.n_periods, 0) AS fiscal_periods
FROM (VALUES ('milkytest'), ('anthonius-iwan')) AS t(tenant_id)
LEFT JOIN (
    SELECT tenant_id, COUNT(*) AS n_years
    FROM fiscal_years GROUP BY tenant_id
) fy ON fy.tenant_id = t.tenant_id
LEFT JOIN (
    SELECT tenant_id, COUNT(*) AS n_periods
    FROM fiscal_periods GROUP BY tenant_id
) fp ON fp.tenant_id = t.tenant_id
ORDER BY t.tenant_id;

-- -----------------------------------------------------------------------------
-- VERIFY 2: every POSTED journal for these tenants now falls inside a covering
-- fiscal_period. Expect ZERO rows (no uncovered journals).
-- -----------------------------------------------------------------------------
SELECT je.tenant_id,
       je.id            AS journal_id,
       je.journal_date
FROM journal_entries je
WHERE je.tenant_id IN ('milkytest', 'anthonius-iwan')
  AND je.status = 'POSTED'
  AND NOT EXISTS (
      SELECT 1 FROM fiscal_periods fp
      WHERE fp.tenant_id = je.tenant_id
        AND je.journal_date BETWEEN fp.start_date AND fp.end_date
  )
ORDER BY je.tenant_id, je.journal_date;

-- Review the two SELECT outputs above:
--   VERIFY 1 -> 12 periods + 1 year per tenant
--   VERIFY 2 -> 0 rows
-- If both hold, COMMIT;  otherwise ROLLBACK;
-- COMMIT;
ROLLBACK;
