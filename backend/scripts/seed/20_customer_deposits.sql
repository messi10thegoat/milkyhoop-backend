-- =============================================
-- EVLOGIA SEED: 20_customer_deposits.sql
-- Purpose: Create 20+ Customer Deposits (Uang Muka) with journal entries
-- Posted deposits CREATE journal entries:
--   Dr. Kas/Bank (1-10100/1-10200)
--   Cr. Uang Muka Pelanggan (2-10200)
-- =============================================

DO $$
DECLARE
    v_tenant_id TEXT := current_setting('seed.tenant_id', true);
    v_customer_ids UUID[];
    v_so_ids UUID[];
    v_deposit_id UUID;
    v_deposit_count INT := 0;
    v_random_customer UUID;
    v_random_so UUID;
    v_amount BIGINT;
    v_deposit_date DATE;
    v_status TEXT;
    v_amount_applied BIGINT;
    v_payment_method TEXT;
    -- Journal Entry
    v_journal_id UUID;
    v_cash_account_id UUID;
    v_bank_account_id UUID;
    v_deposit_liability_account_id UUID;
    v_payment_account_id UUID;
BEGIN
    IF v_tenant_id IS NULL OR v_tenant_id = '' THEN
        v_tenant_id := 'evlogia';
    END IF;

    RAISE NOTICE 'Creating customer deposits for tenant: %', v_tenant_id;

    -- Get account IDs for journal entries
    SELECT id INTO v_cash_account_id FROM chart_of_accounts
    WHERE tenant_id = v_tenant_id AND account_code = '1-10100';

    SELECT id INTO v_bank_account_id FROM chart_of_accounts
    WHERE tenant_id = v_tenant_id AND account_code = '1-10201';  -- BCA

    SELECT id INTO v_deposit_liability_account_id FROM chart_of_accounts
    WHERE tenant_id = v_tenant_id AND account_code = '2-10200';

    -- Get customer IDs (credit customers only)
    SELECT array_agg(id) INTO v_customer_ids
    FROM customers
    WHERE tenant_id = v_tenant_id AND credit_limit > 0;

    -- Get confirmed/shipped sales order IDs for reference
    SELECT array_agg(id) INTO v_so_ids
    FROM sales_orders
    WHERE tenant_id = v_tenant_id
    AND status IN ('confirmed', 'partial_shipped', 'shipped');

    -- ==========================================
    -- Generate 20+ Customer Deposits
    -- Distribution: Nov (6), Dec (8), Jan (6)
    -- Status: draft(4), posted(6), partial(4), applied(5), void(1)
    -- ==========================================

    -- NOVEMBER 2025 - 6 Deposits
    FOR i IN 1..6 LOOP
        v_deposit_date := '2025-11-01'::DATE + (random() * 29)::INT;
        v_deposit_id := gen_random_uuid();
        v_random_customer := v_customer_ids[1 + (random() * (array_length(v_customer_ids, 1) - 1))::INT];

        IF v_so_ids IS NOT NULL AND array_length(v_so_ids, 1) > 0 THEN
            v_random_so := v_so_ids[1 + (random() * (array_length(v_so_ids, 1) - 1))::INT];
        ELSE
            v_random_so := NULL;
        END IF;

        IF i <= 1 THEN v_status := 'draft';
        ELSIF i <= 3 THEN v_status := 'posted';
        ELSIF i <= 4 THEN v_status := 'partial';
        ELSE v_status := 'applied';
        END IF;

        -- Payment method distribution
        IF i <= 3 THEN v_payment_method := 'transfer';
        ELSIF i <= 5 THEN v_payment_method := 'cash';
        ELSE v_payment_method := 'check';
        END IF;

        -- Deposit amount (10-50 juta)
        v_amount := 10000000 + (random() * 40000000)::BIGINT;

        -- Calculate amount_applied based on status
        IF v_status = 'applied' THEN
            v_amount_applied := v_amount;
        ELSIF v_status = 'partial' THEN
            v_amount_applied := (v_amount * (30 + random() * 50)::INT / 100);
        ELSE
            v_amount_applied := 0;
        END IF;

        INSERT INTO customer_deposits (
            id, tenant_id, deposit_number, deposit_date,
            customer_id, sales_order_id, amount, amount_applied,
            payment_method, payment_reference, status, notes,
            created_at, updated_at
        ) VALUES (
            v_deposit_id, v_tenant_id,
            'DP-2511-' || LPAD((i)::TEXT, 4, '0'),
            v_deposit_date, v_random_customer, v_random_so,
            v_amount, v_amount_applied, v_payment_method,
            CASE WHEN v_payment_method = 'transfer' THEN 'TRF-' || i
                 WHEN v_payment_method = 'check' THEN 'CHK-' || i
                 ELSE NULL END,
            v_status, 'Customer Deposit November ' || i,
            v_deposit_date, v_deposit_date
        );

        -- Create journal entry for posted/partial/applied deposits
        IF v_status IN ('posted', 'partial', 'applied') AND v_cash_account_id IS NOT NULL THEN
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
                'JE-DP-2511-' || LPAD((i)::TEXT, 4, '0'),
                v_deposit_date, 'Customer Deposit DP-2511-' || LPAD((i)::TEXT, 4, '0'),
                'customer_deposit', v_deposit_id, 'POSTED', v_deposit_date, v_deposit_date
            );

            -- Dr. Kas/Bank
            INSERT INTO journal_lines (id, journal_id, account_id, debit, credit, description)
            VALUES (gen_random_uuid(), v_journal_id, v_payment_account_id, v_amount, 0, 'Penerimaan uang muka');

            -- Cr. Uang Muka Pelanggan
            INSERT INTO journal_lines (id, journal_id, account_id, debit, credit, description)
            VALUES (gen_random_uuid(), v_journal_id, v_deposit_liability_account_id, 0, v_amount, 'Hutang uang muka pelanggan');
        END IF;

        v_deposit_count := v_deposit_count + 1;
    END LOOP;

    -- DECEMBER 2025 - 8 Deposits
    FOR i IN 1..8 LOOP
        v_deposit_date := '2025-12-01'::DATE + (random() * 30)::INT;
        v_deposit_id := gen_random_uuid();
        v_random_customer := v_customer_ids[1 + (random() * (array_length(v_customer_ids, 1) - 1))::INT];

        IF v_so_ids IS NOT NULL AND array_length(v_so_ids, 1) > 0 THEN
            v_random_so := v_so_ids[1 + (random() * (array_length(v_so_ids, 1) - 1))::INT];
        ELSE
            v_random_so := NULL;
        END IF;

        IF i <= 2 THEN v_status := 'draft';
        ELSIF i <= 4 THEN v_status := 'posted';
        ELSIF i <= 6 THEN v_status := 'partial';
        ELSIF i <= 7 THEN v_status := 'applied';
        ELSE v_status := 'void';
        END IF;

        IF i <= 4 THEN v_payment_method := 'transfer';
        ELSIF i <= 6 THEN v_payment_method := 'cash';
        ELSE v_payment_method := 'check';
        END IF;

        v_amount := 15000000 + (random() * 60000000)::BIGINT;

        IF v_status = 'applied' THEN
            v_amount_applied := v_amount;
        ELSIF v_status = 'partial' THEN
            v_amount_applied := (v_amount * (30 + random() * 50)::INT / 100);
        ELSE
            v_amount_applied := 0;
        END IF;

        INSERT INTO customer_deposits (
            id, tenant_id, deposit_number, deposit_date,
            customer_id, sales_order_id, amount, amount_applied,
            payment_method, payment_reference, status, notes,
            created_at, updated_at
        ) VALUES (
            v_deposit_id, v_tenant_id,
            'DP-2512-' || LPAD((i)::TEXT, 4, '0'),
            v_deposit_date, v_random_customer, v_random_so,
            v_amount, v_amount_applied, v_payment_method,
            CASE WHEN v_payment_method = 'transfer' THEN 'TRF-' || i
                 WHEN v_payment_method = 'check' THEN 'CHK-' || i
                 ELSE NULL END,
            v_status, 'Customer Deposit December ' || i,
            v_deposit_date, v_deposit_date
        );

        IF v_status IN ('posted', 'partial', 'applied') AND v_cash_account_id IS NOT NULL THEN
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
                'JE-DP-2512-' || LPAD((i)::TEXT, 4, '0'),
                v_deposit_date, 'Customer Deposit DP-2512-' || LPAD((i)::TEXT, 4, '0'),
                'customer_deposit', v_deposit_id, 'POSTED', v_deposit_date, v_deposit_date
            );

            INSERT INTO journal_lines (id, journal_id, account_id, debit, credit, description)
            VALUES (gen_random_uuid(), v_journal_id, v_payment_account_id, v_amount, 0, 'Penerimaan uang muka');

            INSERT INTO journal_lines (id, journal_id, account_id, debit, credit, description)
            VALUES (gen_random_uuid(), v_journal_id, v_deposit_liability_account_id, 0, v_amount, 'Hutang uang muka pelanggan');
        END IF;

        v_deposit_count := v_deposit_count + 1;
    END LOOP;

    -- JANUARY 2026 - 6 Deposits
    FOR i IN 1..6 LOOP
        v_deposit_date := '2026-01-01'::DATE + (random() * 15)::INT;
        v_deposit_id := gen_random_uuid();
        v_random_customer := v_customer_ids[1 + (random() * (array_length(v_customer_ids, 1) - 1))::INT];

        IF v_so_ids IS NOT NULL AND array_length(v_so_ids, 1) > 0 THEN
            v_random_so := v_so_ids[1 + (random() * (array_length(v_so_ids, 1) - 1))::INT];
        ELSE
            v_random_so := NULL;
        END IF;

        IF i <= 1 THEN v_status := 'draft';
        ELSIF i <= 3 THEN v_status := 'posted';
        ELSIF i <= 4 THEN v_status := 'partial';
        ELSE v_status := 'applied';
        END IF;

        IF i <= 3 THEN v_payment_method := 'transfer';
        ELSIF i <= 5 THEN v_payment_method := 'cash';
        ELSE v_payment_method := 'check';
        END IF;

        v_amount := 10000000 + (random() * 50000000)::BIGINT;

        IF v_status = 'applied' THEN
            v_amount_applied := v_amount;
        ELSIF v_status = 'partial' THEN
            v_amount_applied := (v_amount * (30 + random() * 50)::INT / 100);
        ELSE
            v_amount_applied := 0;
        END IF;

        INSERT INTO customer_deposits (
            id, tenant_id, deposit_number, deposit_date,
            customer_id, sales_order_id, amount, amount_applied,
            payment_method, payment_reference, status, notes,
            created_at, updated_at
        ) VALUES (
            v_deposit_id, v_tenant_id,
            'DP-2601-' || LPAD((i)::TEXT, 4, '0'),
            v_deposit_date, v_random_customer, v_random_so,
            v_amount, v_amount_applied, v_payment_method,
            CASE WHEN v_payment_method = 'transfer' THEN 'TRF-' || i
                 WHEN v_payment_method = 'check' THEN 'CHK-' || i
                 ELSE NULL END,
            v_status, 'Customer Deposit January ' || i,
            v_deposit_date, v_deposit_date
        );

        IF v_status IN ('posted', 'partial', 'applied') AND v_cash_account_id IS NOT NULL THEN
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
                'JE-DP-2601-' || LPAD((i)::TEXT, 4, '0'),
                v_deposit_date, 'Customer Deposit DP-2601-' || LPAD((i)::TEXT, 4, '0'),
                'customer_deposit', v_deposit_id, 'POSTED', v_deposit_date, v_deposit_date
            );

            INSERT INTO journal_lines (id, journal_id, account_id, debit, credit, description)
            VALUES (gen_random_uuid(), v_journal_id, v_payment_account_id, v_amount, 0, 'Penerimaan uang muka');

            INSERT INTO journal_lines (id, journal_id, account_id, debit, credit, description)
            VALUES (gen_random_uuid(), v_journal_id, v_deposit_liability_account_id, 0, v_amount, 'Hutang uang muka pelanggan');
        END IF;

        v_deposit_count := v_deposit_count + 1;
    END LOOP;

    RAISE NOTICE '========================================';
    RAISE NOTICE 'Customer Deposits created: %', v_deposit_count;
    RAISE NOTICE '========================================';
END $$;

-- Verify Customer Deposits by status and payment method
SELECT status, payment_method, COUNT(*) as count, SUM(amount) as total_amount, SUM(amount_applied) as total_applied
FROM customer_deposits
WHERE tenant_id = 'evlogia'
GROUP BY status, payment_method
ORDER BY status, payment_method;
