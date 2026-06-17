-- V182 — PSAK-72 revenue-timing policy (P4) + deferred-revenue reconciliation guard.
--
-- P4 makes revenue-recognition timing CONFIGURABLE without adding any new journal
-- or source type:
--   tenant_config.revenue_recognition_policy : tenant default ('invoice'|'delivery')
--   sales_invoices.recognize_at              : per-invoice override (nullable)
-- Effective policy = invoice.recognize_at ?? tenant_config ?? 'invoice'.
-- 'invoice'  -> recognize at post (auto-fulfill+recognize) — UNCHANGED legacy path.
-- 'delivery' -> sell-from-stock invoices DEFER (revenue_status='deferred',
--               fulfillment_status='pending'); recognition + COGS happen at
--               /fulfill (existing make-to-order branch, reused verbatim).
-- Global default 'invoice' preserves byte-identical behavior for every existing
-- tenant (NO konveksi backfill).
--
-- Plus a fail-loud DEFERRED-REVENUE reconciliation guard (health-check Check 16),
-- mirroring verify_ar_reconciliation_all (Check 14) and the V181 WAC guard
-- (Check 15). Read-only (STABLE), journal-derived (Iron Law 1/16), NUMERIC(18,2)
-- precision (Law 25). NOT a close-time hard gate — daily health-check only.

-- ---------------------------------------------------------------------------
-- 1. Policy columns
-- ---------------------------------------------------------------------------
ALTER TABLE tenant_config
    ADD COLUMN IF NOT EXISTS revenue_recognition_policy text DEFAULT 'invoice';

ALTER TABLE sales_invoices
    ADD COLUMN IF NOT EXISTS recognize_at varchar NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'chk_si_recognize_at'
    ) THEN
        ALTER TABLE sales_invoices
            ADD CONSTRAINT chk_si_recognize_at
            CHECK (recognize_at IN ('invoice','delivery') OR recognize_at IS NULL);
    END IF;
END$$;

-- ---------------------------------------------------------------------------
-- 2. Deferred-revenue reconciliation guard
-- ---------------------------------------------------------------------------
-- Invariant (per tenant):
--   canonical = Σ over is_effective posted/paid/partial invoices of
--               Σ (sales_invoice_items.allocated_amount - recognized_amount)
--   gl        = GL net (Cr - Dr) of the REVENUE_DEFERRED account (resolved
--               per tenant via account_roles.role_key, NOT hardcoded — 2 tenants
--               map to 2-10500/2-10900), POSTED + is_effective journals only
--               (INVOICE credit, INVOICE_REVENUE / service-recog debit, reversals).
--   drift     = canonical - gl   (validated == 0 on grapgrap = 45.865.000).
--
-- Tolerance: PRINCIPLED 2dp-rounding bound. allocated_amount/recognized_amount are
-- stored at 2dp; per-invoice proportional allocation rounds to 0.01, so each
-- effective invoice contributes at most 0.01 of pure-rounding drift between the
-- canonical 2dp sum and the GL 2dp sum. Per tenant:
--   tolerance = (n_effective_invoices × 0.01) + 0.01 ε.
-- This tolerates exactly the unavoidable rounding and no more — a real deferred-
-- revenue leak (thousands+) stays far outside and is flagged FAIL_NON_EXEMPT.

CREATE TABLE IF NOT EXISTS deferred_revenue_reconciliation_exemptions (
    tenant_id      text PRIMARY KEY,
    baseline_drift numeric(18,2) NOT NULL DEFAULT 0,
    reason         text NOT NULL,
    ticket         text,
    is_permanent   boolean NOT NULL DEFAULT false,
    created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION public.verify_deferred_revenue_reconciliation_all()
RETURNS TABLE(
    tenant_id  text,
    canonical  numeric,
    gl_value   numeric,
    drift      numeric,
    tolerance  numeric,
    is_exempt  boolean,
    verdict    text
)
LANGUAGE plpgsql
STABLE
AS $function$
BEGIN
    RETURN QUERY
    WITH tenants AS (
        -- Any tenant that has a REVENUE_DEFERRED role-mapped account OR has any
        -- non-fully-recognized posted invoice line.
        SELECT DISTINCT ar.tenant_id AS tid
        FROM account_roles ar
        WHERE ar.role_key = 'REVENUE_DEFERRED'
        UNION
        SELECT DISTINCT si.tenant_id
        FROM sales_invoices si
        WHERE si.status IN ('posted','paid','partial')
    ),
    -- Canonical: Σ (allocated - recognized) over effective posted invoices,
    -- plus the per-tenant count of invoices that carry any open deferral
    -- (used for the principled rounding tolerance).
    canon AS (
        SELECT si.tenant_id AS tid,
               COALESCE(SUM(sii.allocated_amount - sii.recognized_amount), 0)::numeric(18,2) AS canonical,
               COUNT(DISTINCT si.id) AS n_inv
        FROM sales_invoices si
        JOIN sales_invoice_items sii ON sii.invoice_id = si.id
        WHERE si.status IN ('posted','paid','partial')
        GROUP BY si.tenant_id
    ),
    -- GL net (Cr - Dr) of the per-tenant REVENUE_DEFERRED account,
    -- POSTED + is_effective journals only (symmetric reversals net out).
    gl AS (
        SELECT je.tenant_id AS tid,
               COALESCE(SUM(jl.credit - jl.debit), 0)::numeric(18,2) AS gl_value
        FROM journal_entries je
        JOIN journal_lines jl ON jl.journal_id = je.id
        JOIN account_roles ar
              ON ar.tenant_id = je.tenant_id
             AND ar.role_key = 'REVENUE_DEFERRED'
             AND ar.account_id = jl.account_id
        WHERE is_effective_journal(je.id)
        GROUP BY je.tenant_id
    ),
    per_tenant AS (
        SELECT t.tid,
               COALESCE(c.canonical, 0)::numeric(18,2) AS canonical,
               COALESCE(g.gl_value, 0)::numeric(18,2)  AS gl_value,
               (COALESCE(c.canonical, 0) - COALESCE(g.gl_value, 0))::numeric(18,2) AS drift,
               (COALESCE(c.n_inv, 0) * 0.01 + 0.01)::numeric AS tolerance
        FROM tenants t
        LEFT JOIN canon c ON c.tid = t.tid
        LEFT JOIN gl g    ON g.tid = t.tid
    )
    SELECT p.tid,
           p.canonical,
           p.gl_value,
           p.drift,
           p.tolerance,
           (e.tenant_id IS NOT NULL) AS is_exempt,
           CASE
               WHEN e.tenant_id IS NULL AND ABS(p.drift) <= p.tolerance THEN 'PASS'
               WHEN e.tenant_id IS NULL AND ABS(p.drift) >  p.tolerance THEN 'FAIL_NON_EXEMPT'
               WHEN e.tenant_id IS NOT NULL
                    AND ABS(p.drift - e.baseline_drift) <= 0.01 THEN 'PASS_EXEMPT'
               ELSE 'FAIL_DRIFT_CHANGED'
           END AS verdict
    FROM per_tenant p
    LEFT JOIN deferred_revenue_reconciliation_exemptions e ON e.tenant_id = p.tid
    ORDER BY (CASE
                WHEN e.tenant_id IS NULL AND ABS(p.drift) > p.tolerance THEN 0
                WHEN e.tenant_id IS NOT NULL
                     AND ABS(p.drift - e.baseline_drift) > 0.01 THEN 0
                ELSE 1 END), p.tid;
END;
$function$;

-- ---------------------------------------------------------------------------
-- 3. Grandfather pre-existing drift (root-caused, NOT dismissed)
-- ---------------------------------------------------------------------------
-- milkytest: 5 legacy invoices (INV-2603-0001..0005) posted BEFORE the 3-event
-- PSAK-72 path existed. They recognized directly (Dr Piutang / Cr Penjualan
-- 4-10100) at post and never touched the deferred account; sales_invoice_items
-- .recognized_amount was never backfilled (still 0). Canonical formula therefore
-- overstates deferred by 7.960.000; GL deferred is correctly 0. Frozen legacy,
-- no real liability. (A canonical backfill UPDATE recognized_amount=allocated_amount
-- on these is the proper cleanup; tracked separately to keep P4 minimal.)
INSERT INTO deferred_revenue_reconciliation_exemptions (tenant_id, baseline_drift, reason, ticket, is_permanent)
VALUES ('milkytest', 7960000.00,
        'Legacy pre-PSAK72 direct-recognition invoices (INV-2603-0001..0005): revenue posted Dr Piutang/Cr 4-10100 at post, recognized_amount never backfilled on items. GL deferred correctly 0; canonical overstates. No real liability.',
        'P4-LEGACY-RECOG-BACKFILL', false)
ON CONFLICT (tenant_id) DO UPDATE
    SET baseline_drift = EXCLUDED.baseline_drift, reason = EXCLUDED.reason, ticket = EXCLUDED.ticket;

-- p3verify: void-path asymmetry (sibling-#23 class). Two service invoices
-- (INV-2606-0002/0003) were posted then voided. On void, the INVOICE billing
-- credit (Cr deferred) is excluded via reversed_by_id and its INVOICE_REVERSAL
-- (Dr deferred) is excluded as a reversal — but the paired INVOICE_REVENUE
-- service-recognition DEBIT (Dr deferred 10M each) is NOT marked reversed and
-- stays is_effective. That leaves an orphan Dr 20M on the deferred account with
-- no canonical counterpart (voided invoices excluded from posted/paid/partial),
-- so GL = -20.000.000 vs canonical 0. This is a REAL void-path defect, but the
-- void path is OUT OF SCOPE for P4 (item 6: void UNTOUCHED). The guard correctly
-- surfaces it; grandfathered here pending the void-path recognition-reversal fix.
INSERT INTO deferred_revenue_reconciliation_exemptions (tenant_id, baseline_drift, reason, ticket, is_permanent)
VALUES ('p3verify', 20000000.00,
        'Void-path asymmetry (sibling #23): voided service invoices INV-2606-0002/0003 left orphan INVOICE_REVENUE recognition DEBITs (Dr deferred 20M) still is_effective while their billing credits were excluded via reversed_by_id. GL=-20M vs canonical 0. REAL defect in void path (out of P4 scope; void UNTOUCHED). Fix = mark INVOICE_REVENUE reversed on invoice void.',
        'VOID-RECOG-REVERSAL-23', false)
ON CONFLICT (tenant_id) DO UPDATE
    SET baseline_drift = EXCLUDED.baseline_drift, reason = EXCLUDED.reason, ticket = EXCLUDED.ticket;
