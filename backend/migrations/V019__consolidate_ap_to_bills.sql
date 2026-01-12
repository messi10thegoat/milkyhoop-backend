-- =============================================================================
-- V019: Consolidate accounts_payable data into bills table
-- =============================================================================
-- Purpose: Single Source of Truth for purchase invoices
-- Pattern: bills (master) → journal_entries (accounting)
-- =============================================================================

-- Step 1: Migrate accounts_payable → bills
-- Status mapping: OPEN→unpaid, PARTIAL→partial, PAID→paid, VOID→void
-- =============================================================================
INSERT INTO bills (
    id,
    tenant_id,
    invoice_number,
    vendor_id,
    vendor_name,
    amount,
    amount_paid,
    status,
    issue_date,
    due_date,
    notes,
    voided_at,
    voided_reason,
    ap_id,
    created_at,
    updated_at,
    created_by
)
SELECT
    gen_random_uuid() as id,
    ap.tenant_id,
    ap.bill_number as invoice_number,
    ap.supplier_id as vendor_id,
    ap.supplier_name as vendor_name,
    ap.amount::bigint as amount,  -- Convert NUMERIC to BIGINT (IDR no decimals)
    ap.amount_paid::bigint as amount_paid,
    CASE ap.status
        WHEN 'OPEN' THEN 'unpaid'
        WHEN 'PARTIAL' THEN 'partial'
        WHEN 'PAID' THEN 'paid'
        WHEN 'VOID' THEN 'void'
        ELSE 'unpaid'
    END as status,
    ap.bill_date as issue_date,
    ap.due_date,
    ap.description as notes,
    CASE WHEN ap.status = 'VOID' THEN ap.updated_at ELSE NULL END as voided_at,
    CASE WHEN ap.status = 'VOID' THEN 'Migrated from accounts_payable' ELSE NULL END as voided_reason,
    ap.id as ap_id,  -- Keep reference for backward compatibility
    ap.created_at,
    ap.updated_at,
    '00000000-0000-0000-0000-000000000000'::uuid as created_by  -- System migration
FROM accounts_payable ap
WHERE NOT EXISTS (
    -- Skip if already migrated (check by ap_id)
    SELECT 1 FROM bills b WHERE b.ap_id = ap.id
)
ON CONFLICT (tenant_id, invoice_number) DO NOTHING;

-- Step 2: Migrate ap_payment_applications → bill_payments
-- =============================================================================
INSERT INTO bill_payments (
    id,
    tenant_id,
    bill_id,
    amount,
    payment_date,
    payment_method,
    reference,
    notes,
    account_id,
    journal_id,
    created_at,
    created_by
)
SELECT
    gen_random_uuid() as id,
    apa.tenant_id,
    b.id as bill_id,  -- Link to migrated bill
    apa.amount_applied::bigint as amount,
    apa.payment_date,
    apa.payment_method,
    apa.reference_number as reference,
    apa.notes,
    NULL as account_id,  -- Will be set if journal exists
    apa.journal_id,
    apa.created_at,
    '00000000-0000-0000-0000-000000000000'::uuid as created_by
FROM ap_payment_applications apa
JOIN bills b ON b.ap_id = apa.ap_id
WHERE NOT EXISTS (
    -- Skip if payment already exists for this bill and date
    SELECT 1 FROM bill_payments bp
    WHERE bp.bill_id = b.id
    AND bp.payment_date = apa.payment_date
    AND bp.amount = apa.amount_applied::bigint
);

-- Step 3: Create index on ap_id for efficient lookups
-- =============================================================================
CREATE INDEX IF NOT EXISTS idx_bills_ap_id ON bills(ap_id) WHERE ap_id IS NOT NULL;

-- Step 4: Add comment for documentation
-- =============================================================================
COMMENT ON COLUMN bills.ap_id IS 'Legacy reference to accounts_payable.id for backward compatibility';

-- Step 5: Verify migration
-- =============================================================================
DO $$
DECLARE
    ap_count INTEGER;
    bills_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO ap_count FROM accounts_payable;
    SELECT COUNT(*) INTO bills_count FROM bills WHERE ap_id IS NOT NULL;

    RAISE NOTICE 'Migration Summary:';
    RAISE NOTICE '  accounts_payable records: %', ap_count;
    RAISE NOTICE '  bills migrated: %', bills_count;

    IF bills_count < ap_count THEN
        RAISE NOTICE '  Note: Some records may have been skipped due to duplicate invoice numbers';
    END IF;
END $$;
