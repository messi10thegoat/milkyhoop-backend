-- =============================================
-- EVLOGIA SEED: 21_payment_receipts.sql
-- Purpose: Create Payment Receipts (AR Payments) with journal entries
-- Posted payment receipts CREATE journal entries:
--   Dr. Kas/Bank (1-10100/1-10200)
--   Cr. Piutang Usaha (1-10300)
-- =============================================

DO $$
DECLARE
    v_tenant_id TEXT := current_setting('seed.tenant_id', true);
    v_invoice_record RECORD;
    v_payment_id UUID;
    v_payment_count INT := 0;
    v_amount BIGINT;
    v_payment_date DATE;
    v_payment_method TEXT;
    -- Journal Entry
    v_journal_id UUID;
    v_cash_account_id UUID;
    v_bank_account_id UUID;
    v_ar_account_id UUID;
    v_payment_account_id UUID;
    v_payment_number INT := 0;
BEGIN
    IF v_tenant_id IS NULL OR v_tenant_id = '' THEN
        v_tenant_id := 'evlogia';
    END IF;

    RAISE NOTICE 'Creating payment receipts for tenant: %', v_tenant_id;

    -- Get account IDs for journal entries
    SELECT id INTO v_cash_account_id FROM chart_of_accounts
    WHERE tenant_id = v_tenant_id AND account_code = '1-10100';

    SELECT id INTO v_bank_account_id FROM chart_of_accounts
    WHERE tenant_id = v_tenant_id AND account_code = '1-10201';  -- BCA

    SELECT id INTO v_ar_account_id FROM chart_of_accounts
    WHERE tenant_id = v_tenant_id AND account_code = '1-10300';

    -- ==========================================
    -- Create payments for invoices with partial/paid status
    -- This simulates actual payments that resulted in those statuses
    -- ==========================================

    -- Process invoices that have been paid or partially paid
    FOR v_invoice_record IN
        SELECT
            id, invoice_number, invoice_date, customer_id,
            total_amount, amount_paid, status
        FROM sales_invoices
        WHERE tenant_id = v_tenant_id
        AND status IN ('partial', 'paid')
        AND amount_paid > 0
        ORDER BY invoice_date
    LOOP
        v_payment_number := v_payment_number + 1;
        v_payment_id := gen_random_uuid();

        -- Payment date is typically a few days after invoice date
        v_payment_date := v_invoice_record.invoice_date + (random() * 14)::INT;

        -- Determine payment method (mostly transfer for B2B)
        IF random() < 0.6 THEN
            v_payment_method := 'transfer';
        ELSIF random() < 0.85 THEN
            v_payment_method := 'cash';
        ELSE
            v_payment_method := 'check';
        END IF;

        v_amount := v_invoice_record.amount_paid;

        INSERT INTO payment_receipts (
            id, tenant_id, receipt_number, receipt_date,
            customer_id, invoice_id, amount, payment_method,
            payment_reference, bank_account, status, notes,
            created_at, updated_at
        ) VALUES (
            v_payment_id, v_tenant_id,
            'RCV-' || TO_CHAR(v_payment_date, 'YYMM') || '-' || LPAD((v_payment_number)::TEXT, 4, '0'),
            v_payment_date, v_invoice_record.customer_id, v_invoice_record.id,
            v_amount, v_payment_method,
            CASE WHEN v_payment_method = 'transfer' THEN 'TRF-' || v_payment_number
                 WHEN v_payment_method = 'check' THEN 'CHK-' || v_payment_number
                 ELSE NULL END,
            CASE WHEN v_payment_method = 'transfer' THEN 'BCA'
                 WHEN v_payment_method = 'check' THEN 'Mandiri'
                 ELSE NULL END,
            'posted', 'Payment for ' || v_invoice_record.invoice_number,
            v_payment_date, v_payment_date
        );

        -- Create journal entry
        IF v_ar_account_id IS NOT NULL THEN
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
                'JE-RCV-' || TO_CHAR(v_payment_date, 'YYMM') || '-' || LPAD((v_payment_number)::TEXT, 4, '0'),
                v_payment_date, 'Payment Receipt for ' || v_invoice_record.invoice_number,
                'payment_receipt', v_payment_id, 'POSTED', v_payment_date, v_payment_date
            );

            -- Dr. Kas/Bank
            INSERT INTO journal_lines (id, journal_id, account_id, debit, credit, description)
            VALUES (gen_random_uuid(), v_journal_id, v_payment_account_id, v_amount, 0, 'Penerimaan pembayaran piutang');

            -- Cr. Piutang Usaha
            INSERT INTO journal_lines (id, journal_id, account_id, debit, credit, description)
            VALUES (gen_random_uuid(), v_journal_id, v_ar_account_id, 0, v_amount, 'Pelunasan piutang');
        END IF;

        v_payment_count := v_payment_count + 1;
    END LOOP;

    RAISE NOTICE '========================================';
    RAISE NOTICE 'Payment Receipts created: %', v_payment_count;
    RAISE NOTICE '========================================';
END $$;

-- Verify Payment Receipts by payment method
SELECT payment_method, status, COUNT(*) as count, SUM(amount) as total_amount
FROM payment_receipts
WHERE tenant_id = 'evlogia'
GROUP BY payment_method, status
ORDER BY payment_method, status;

-- Verify AR reconciliation
SELECT
    'AR Balance Check' as check_type,
    (SELECT COALESCE(SUM(total_amount - amount_paid), 0)
     FROM sales_invoices
     WHERE tenant_id = 'evlogia' AND status NOT IN ('draft', 'void')) as outstanding_ar,
    (SELECT COALESCE(SUM(amount), 0)
     FROM payment_receipts
     WHERE tenant_id = 'evlogia' AND status = 'posted') as total_payments;
