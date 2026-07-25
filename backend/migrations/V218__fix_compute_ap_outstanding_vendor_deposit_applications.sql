-- V218: compute_ap_outstanding — settle bills paid via applied VENDOR DEPOSITS.
--
-- BUG (AP OVERSTATED): apply_vendor_deposit posts a DEPOSIT_APPLICATION journal
--   Dr 2-10100 Hutang Usaha (PAYABLE)  / Cr 1-10800 Uang Muka Vendor
-- which genuinely settles the bill. But compute_ap_outstanding's paid_amount was
-- built only from bill_payment_allocations (CTE 3) and vendor_credit_applications
-- (CTE 4). A DEPOSIT_APPLICATION journal lives in NEITHER table, so its PAYABLE
-- debit was counted nowhere and the bill kept showing its full obligation.
-- Result: AP overstated by exactly the applied-deposit amount. This is the AP
-- mirror of the AR fix already live in compute_ar_outstanding Branch 3
-- (FIX_P35_ARCANON, customer_deposit_applications).
--
-- FIX: add CTE 6 (vd_applied_debits) attributing each DEPOSIT_APPLICATION journal's
-- PAYABLE debit to its bill via vendor_deposit_applications.bill_id, and fold it
-- into paid_amount / outstanding alongside the existing payment & vendor-credit debits.
--
-- FAN-OUT SAFETY (proven, code-level — NOT a DB constraint): apply_vendor_deposit
-- creates exactly ONE journal per call, with ONE PAYABLE debit line, and inserts
-- exactly ONE vendor_deposit_applications row for it (1 journal : 1 vda : 1 bill :
-- 1 debit line). So vda JOIN journal_lines yields one row per vda and
-- SUM(jl.debit) GROUP BY vda.bill_id cannot double-count. Multiple applications to
-- the same bill = multiple journals => SUM across them is the correct total.
-- WARNING to future maintainers: there is NO unique constraint enforcing this 1:1.
-- A refactor that emits one journal spanning multiple bills (multiple vda sharing a
-- journal_id, or multiple PAYABLE debit lines) would fan this CTE out. Keep apply =
-- one journal per bill, or switch this CTE to SUM(vda.amount).
--
-- REVERSAL CONTRACT (LOCKED by je.reversed_by_id IS NULL): vendor_deposit_applications
-- has NO status/reversed_by_id column (unlike customer_deposit_applications). The ONLY
-- lever that removes an applied vendor deposit from this computation is the journal's
-- reversed_by_id being set by a Law-2 reversing journal. Therefore any future
-- vendor-deposit un-apply MUST post a reversing journal — it must NOT DELETE the vda
-- row and must NOT add a status flag that this function does not read. (Today this is
-- moot: void is blocked while applications exist and no un-apply endpoint exists — the
-- known vendor-deposit dead-end, tracked separately.)
--
-- TENANT SCOPING: vendor_deposit_applications has no tenant_id column (customer side
-- does). Scope is enforced via je.tenant_id = p_tenant_id (the journal is tenant-
-- stamped and vda.journal_id is 1:1 to it), and doubly so because bill_rows LEFT JOINs
-- only tenant-scoped active_bills.
--
-- PRECONDITION ENFORCEMENT (this migration): the CTE's 1:1 fan-out safety was,
-- until now, guaranteed only by application code (apply creates one journal per
-- obligation). The two UNIQUE(journal_id) constraints below promote that from a
-- code convention to a DB invariant: fan-out is possible ONLY if N application
-- rows share one journal_id — UNIQUE(journal_id) forbids exactly that, nothing
-- wider. Added for BOTH sides so the same guarantee backs the live AR Branch 3
-- (customer_deposit_applications) and this new AP CTE (vendor_deposit_applications).
-- If existing data already violates this, the ADD fails and the whole migration
-- rolls back (fail-loud) — do NOT weaken the CTE to SUM(<wrapper>.amount): that
-- reads the figure from the wrapper table instead of the ledger and violates Law 1.
-- Journal-derived SUM(jl.debit) + UNIQUE(journal_id) is the correct pairing.
--
-- Idempotent function (CREATE OR REPLACE). The ALTERs are one-time DDL; the
-- migration runner applies this file exactly once (tracked in schema_migrations).

ALTER TABLE customer_deposit_applications
    ADD CONSTRAINT uq_cda_journal_id UNIQUE (journal_id);

ALTER TABLE vendor_deposit_applications
    ADD CONSTRAINT uq_vda_journal_id UNIQUE (journal_id);

CREATE OR REPLACE FUNCTION public.compute_ap_outstanding(p_tenant_id text)
 RETURNS TABLE(vendor_id uuid, vendor_name text, bill_id uuid, bill_number text, bill_date date, due_date date, bill_status text, bill_total numeric, paid_amount numeric, outstanding numeric)
 LANGUAGE sql
 STABLE
AS $function$
    WITH
    -- 1. Active bills (non-draft, non-void)
    active_bills AS (
        SELECT b.id, b.invoice_number, b.issue_date, b.due_date,
               b.vendor_id, b.vendor_name, b.status_v2, b.amount
        FROM bills b
        WHERE b.tenant_id = p_tenant_id
          AND b.status_v2 NOT IN ('draft', 'void')
    ),
    -- 2. Bill obligation: credits on PAYABLE from BILL journals
    bill_credits AS (
        SELECT je.source_id::uuid AS bill_id,
               COALESCE(SUM(jl.credit), 0) AS total_credit
        FROM journal_lines jl
        JOIN journal_entries je ON je.id = jl.journal_id
        JOIN chart_of_accounts coa ON coa.id = jl.account_id
        WHERE je.tenant_id = p_tenant_id
          AND je.status = 'POSTED'
          AND je.reversed_by_id IS NULL
          AND coa.account_type = 'PAYABLE'
          AND jl.credit > 0
          AND je.source_type = 'BILL'
        GROUP BY je.source_id::uuid
    ),
    -- 3. Bill payments: debits on PAYABLE from bill_payment_allocations
    payment_debits AS (
        SELECT bpa.bill_id,
               COALESCE(SUM(jl.debit), 0) AS total_debit
        FROM bill_payment_allocations bpa
        JOIN bill_payments_v2 bpv2 ON bpv2.id = bpa.payment_id
        JOIN journal_entries je ON je.id = bpv2.journal_id
        JOIN journal_lines jl ON jl.journal_id = je.id
        JOIN chart_of_accounts coa ON coa.id = jl.account_id
        WHERE bpv2.tenant_id = p_tenant_id
          AND je.status = 'POSTED' AND je.reversed_by_id IS NULL
          AND coa.account_type = 'PAYABLE' AND jl.debit > 0
        GROUP BY bpa.bill_id
    ),
    -- 4. Vendor credit applications to specific bills (debits on PAYABLE)
    vc_applied_debits AS (
        SELECT vca.bill_id,
               COALESCE(SUM(jl.debit), 0) AS total_debit
        FROM vendor_credit_applications vca
        JOIN journal_entries je ON je.id = vca.journal_id
        JOIN journal_lines jl ON jl.journal_id = je.id
        JOIN chart_of_accounts coa ON coa.id = jl.account_id
        WHERE vca.tenant_id = p_tenant_id
          AND je.status = 'POSTED' AND je.reversed_by_id IS NULL
          AND coa.account_type = 'PAYABLE' AND jl.debit > 0
        GROUP BY vca.bill_id
    ),
    -- 5. Unapplied vendor credits: VENDOR_CREDIT journals on PAYABLE not tied to bills
    --    These are net debit/credit on PAYABLE per vendor.
    --    Debits = VC creation (reduce AP), Credits = VC void reversals (increase AP).
    vc_unapplied AS (
        SELECT vc.vendor_id,
               vc.vendor_name,
               COALESCE(SUM(jl.credit), 0) AS total_credit,
               COALESCE(SUM(jl.debit), 0)  AS total_debit
        FROM journal_entries je
        JOIN journal_lines jl ON jl.journal_id = je.id
        JOIN chart_of_accounts coa ON coa.id = jl.account_id
        JOIN vendor_credits vc ON vc.id = je.source_id::uuid
        WHERE je.tenant_id = p_tenant_id
          AND je.status = 'POSTED'
          AND je.reversed_by_id IS NULL
          AND je.source_type = 'VENDOR_CREDIT'
          AND coa.account_type = 'PAYABLE'
          -- Exclude applied VCs (they're handled in vc_applied_debits)
          AND NOT EXISTS (
              SELECT 1 FROM vendor_credit_applications vca
              WHERE vca.vendor_credit_id = vc.id
                AND vca.journal_id = je.id
          )
        GROUP BY vc.vendor_id, vc.vendor_name
    ),
    -- 6. Vendor-deposit applications to specific bills (debits on PAYABLE).  [V218]
    --    See header: a DEPOSIT_APPLICATION journal's Dr 2-10100 settles the bill and
    --    is caught by no other CTE. Scoped via je.tenant_id (vda has no tenant_id).
    --    Fan-out-safe under the code-enforced 1 journal : 1 vda : 1 debit-line invariant.
    vd_applied_debits AS (
        SELECT vda.bill_id,
               COALESCE(SUM(jl.debit), 0) AS total_debit
        FROM vendor_deposit_applications vda
        JOIN journal_entries je ON je.id = vda.journal_id
            AND je.source_type = 'DEPOSIT_APPLICATION'
        JOIN journal_lines jl ON jl.journal_id = je.id
        JOIN chart_of_accounts coa ON coa.id = jl.account_id
        WHERE je.tenant_id = p_tenant_id
          AND je.status = 'POSTED' AND je.reversed_by_id IS NULL
          AND coa.account_type = 'PAYABLE' AND jl.debit > 0
        GROUP BY vda.bill_id
    ),
    -- Combine bill rows
    bill_rows AS (
        SELECT ab.vendor_id, ab.vendor_name::TEXT, ab.id AS bill_id,
               ab.invoice_number::TEXT AS bill_number, ab.issue_date AS bill_date,
               ab.due_date, ab.status_v2::TEXT AS bill_status,
               COALESCE(bc.total_credit, 0)::NUMERIC(18,2) AS bill_total,
               (COALESCE(pd.total_debit, 0) + COALESCE(vcd.total_debit, 0) + COALESCE(vdd.total_debit, 0))::NUMERIC(18,2) AS paid_amount,
               (COALESCE(bc.total_credit, 0) - COALESCE(pd.total_debit, 0) - COALESCE(vcd.total_debit, 0) - COALESCE(vdd.total_debit, 0))::NUMERIC(18,2) AS outstanding
        FROM active_bills ab
        LEFT JOIN bill_credits bc ON bc.bill_id = ab.id
        LEFT JOIN payment_debits pd ON pd.bill_id = ab.id
        LEFT JOIN vc_applied_debits vcd ON vcd.bill_id = ab.id
        LEFT JOIN vd_applied_debits vdd ON vdd.bill_id = ab.id
    ),
    -- Unapplied VC rows (synthetic — no bill, vendor-level)
    vc_rows AS (
        SELECT vcu.vendor_id,
               vcu.vendor_name::TEXT,
               NULL::UUID AS bill_id,
               'VENDOR-CREDIT'::TEXT AS bill_number,
               CURRENT_DATE AS bill_date,
               CURRENT_DATE AS due_date,
               'vendor_credit'::TEXT AS bill_status,
               vcu.total_credit::NUMERIC(18,2) AS bill_total,
               vcu.total_debit::NUMERIC(18,2) AS paid_amount,
               (vcu.total_credit - vcu.total_debit)::NUMERIC(18,2) AS outstanding
        FROM vc_unapplied vcu
    )
    SELECT * FROM bill_rows WHERE outstanding != 0
    UNION ALL
    SELECT * FROM vc_rows WHERE outstanding != 0
    ORDER BY vendor_name, due_date;
$function$;
