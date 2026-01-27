-- =============================================
-- EVLOGIA SEED: 22_bill_payments.sql
-- Purpose: Create Bill Payments (AP Payments) with journal entries
-- Posted bill payments CREATE journal entries:
--   Dr. Hutang Usaha (2-10100)
--   Cr. Kas/Bank (1-10100/1-10200)
-- =============================================

DO $$
DECLARE
    v_tenant_id TEXT := current_setting('seed.tenant_id', true);
    v_bill_record RECORD;
    v_payment_id UUID;
    v_payment_count INT := 0;
    v_amount BIGINT;
    v_payment_date DATE;
    v_payment_method TEXT;
    -- Journal Entry
    v_journal_id UUID;
    v_cash_account_id UUID;
    v_bank_account_id UUID;
    v_ap_account_id UUID;
    v_payment_account_id UUID;
    v_payment_number INT := 0;
BEGIN
    IF v_tenant_id IS NULL OR v_tenant_id = '' THEN
        v_tenant_id := 'evlogia';
    END IF;

    RAISE NOTICE 'Creating bill payments for tenant: %', v_tenant_id;

    -- Get account IDs for journal entries
    SELECT id INTO v_cash_account_id FROM chart_of_accounts
    WHERE tenant_id = v_tenant_id AND account_code = '1-10100';

    SELECT id INTO v_bank_account_id FROM chart_of_accounts
    WHERE tenant_id = v_tenant_id AND account_code = '1-10201';  -- BCA

    SELECT id INTO v_ap_account_id FROM chart_of_accounts
    WHERE tenant_id = v_tenant_id AND account_code = '2-10100';

    -- ==========================================
    -- Create payments for bills with partial/paid status
    -- This simulates actual payments that resulted in those statuses
    -- ==========================================

    -- Process bills that have been paid or partially paid
    FOR v_bill_record IN
        SELECT
            id, bill_number, bill_date, vendor_id,
            amount, amount_paid, status
        FROM bills
        WHERE tenant_id = v_tenant_id
        AND status IN ('partial', 'paid')
        AND amount_paid > 0
        ORDER BY bill_date
    LOOP
        v_payment_number := v_payment_number + 1;
        v_payment_id := gen_random_uuid();

        -- Payment date is typically a few days after bill date (within payment terms)
        v_payment_date := v_bill_record.bill_date + (random() * 21)::INT;

        -- Determine payment method (mostly transfer for B2B)
        IF random() < 0.7 THEN
            v_payment_method := 'transfer';
        ELSIF random() < 0.9 THEN
            v_payment_method := 'check';
        ELSE
            v_payment_method := 'cash';
        END IF;

        v_amount := v_bill_record.amount_paid;

        INSERT INTO bill_payments (
            id, tenant_id, payment_number, payment_date,
            vendor_id, bill_id, amount, payment_method,
            payment_reference, bank_account, status, notes,
            created_at, updated_at
        ) VALUES (
            v_payment_id, v_tenant_id,
            'PAY-' || TO_CHAR(v_payment_date, 'YYMM') || '-' || LPAD((v_payment_number)::TEXT, 4, '0'),
            v_payment_date, v_bill_record.vendor_id, v_bill_record.id,
            v_amount, v_payment_method,
            CASE WHEN v_payment_method = 'transfer' THEN 'TRF-OUT-' || v_payment_number
                 WHEN v_payment_method = 'check' THEN 'CHK-OUT-' || v_payment_number
                 ELSE NULL END,
            CASE WHEN v_payment_method = 'transfer' THEN 'BCA'
                 WHEN v_payment_method = 'check' THEN 'Mandiri'
                 ELSE NULL END,
            'posted', 'Payment for ' || v_bill_record.bill_number,
            v_payment_date, v_payment_date
        );

        -- Create journal entry
        IF v_ap_account_id IS NOT NULL THEN
            v_journal_id := gen_random_uuid();

            IF v_payment_method = 'cash' THEN
                v_payment_account_id := v_cash_account_id;
            ELSE
                v_payment_account_id := v_bank_account_id;
            END IF;

            INSERT INTO journal_entries (
                id, tenant_id, entry_number, entry_date, description,
                source_type, source_id, status, created_at, updated_at
            ) VALUES (
                v_journal_id, v_tenant_id,
                'JE-PAY-' || TO_CHAR(v_payment_date, 'YYMM') || '-' || LPAD((v_payment_number)::TEXT, 4, '0'),
                v_payment_date, 'Bill Payment for ' || v_bill_record.bill_number,
                'bill_payment', v_payment_id, 'POSTED', v_payment_date, v_payment_date
            );

            -- Dr. Hutang Usaha
            INSERT INTO journal_lines (id, journal_id, account_id, debit, credit, description)
            VALUES (gen_random_uuid(), v_journal_id, v_ap_account_id, v_amount, 0, 'Pelunasan hutang');

            -- Cr. Kas/Bank
            INSERT INTO journal_lines (id, journal_id, account_id, debit, credit, description)
            VALUES (gen_random_uuid(), v_journal_id, v_payment_account_id, 0, v_amount, 'Pembayaran hutang');
        END IF;

        v_payment_count := v_payment_count + 1;
    END LOOP;

    RAISE NOTICE '========================================';
    RAISE NOTICE 'Bill Payments created: %', v_payment_count;
    RAISE NOTICE '========================================';
END $$;

-- Verify Bill Payments by payment method
SELECT payment_method, status, COUNT(*) as count, SUM(amount) as total_amount
FROM bill_payments
WHERE tenant_id = 'evlogia'
GROUP BY payment_method, status
ORDER BY payment_method, status;

-- Verify AP reconciliation
SELECT
    'AP Balance Check' as check_type,
    (SELECT COALESCE(SUM(amount - amount_paid), 0)
     FROM bills
     WHERE tenant_id = 'evlogia' AND status NOT IN ('draft', 'void')) as outstanding_ap,
    (SELECT COALESCE(SUM(amount), 0)
     FROM bill_payments
     WHERE tenant_id = 'evlogia' AND status = 'posted') as total_payments;
