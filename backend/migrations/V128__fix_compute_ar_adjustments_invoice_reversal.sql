-- V128: Fix compute_ar_adjustments to include INVOICE_REVERSAL source type
--
-- Root cause: voided invoices create INVOICE_REVERSAL journals that credit RECEIVABLE.
-- These were excluded from adjustments, causing ARAP Rule 8 invariant drift.
-- AP side (compute_ap_adjustments) already included REVERSAL — AR was missing it.
--
-- Before: AR drift = -200,000 (for grapgrap tenant with 1 voided invoice)
-- After:  AR drift = 0
--
-- Date: 2026-03-30
-- Aligned with: milkyhoop-arap v1.1, milkyhoop-ironlaws v3.6

CREATE OR REPLACE FUNCTION public.compute_ar_adjustments(p_tenant_id text)
 RETURNS TABLE(journal_id uuid, journal_number text, journal_date date, source_type text, description text, debit numeric, credit numeric, net numeric)
 LANGUAGE sql
 STABLE
AS $$
    SELECT je.id, je.journal_number, je.journal_date::date,
           je.source_type, je.description,
           COALESCE(SUM(jl.debit), 0),
           COALESCE(SUM(jl.credit), 0),
           COALESCE(SUM(jl.debit - jl.credit), 0)
    FROM journal_lines jl
    JOIN journal_entries je ON je.id = jl.journal_id
    JOIN chart_of_accounts coa ON coa.id = jl.account_id
    WHERE coa.account_type = RECEIVABLE
      AND je.status = POSTED
      AND je.reversed_by_id IS NULL
      AND je.tenant_id = p_tenant_id
      AND (
        -- Non-standard source types (orphaned journals)
        je.source_type NOT IN (INVOICE, PAYMENT_RECEIVED, RECEIVE_PAYMENT, SALES_INVOICE_COGS, CASH_SALE, INVOICE_REVERSAL)
        -- Invoices without active obligation (voided/drafted)
        OR (je.source_type = INVOICE AND NOT EXISTS (
            SELECT 1 FROM sales_invoices si
            WHERE si.id = je.source_id::uuid
              AND si.tenant_id = p_tenant_id
              AND si.status NOT IN (draft, void)
        ))
        -- Payments without active obligation link
        OR (je.source_type IN (PAYMENT_RECEIVED, RECEIVE_PAYMENT) AND NOT EXISTS (
            SELECT 1 FROM receive_payment_allocations rpa
            JOIN receive_payments rp ON rp.id = rpa.payment_id
            JOIN sales_invoices si ON si.id = rpa.invoice_id
            WHERE rp.journal_id = je.id
              AND si.tenant_id = p_tenant_id
              AND si.status NOT IN (draft, void)
        ))
        -- Invoice reversals (void credit to RECEIVABLE) — NEW in V128
        OR je.source_type = INVOICE_REVERSAL
      )
    GROUP BY je.id, je.journal_number, je.journal_date, je.source_type, je.description;
$$;
