-- =============================================
-- EVLOGIA SEED: 10_bills.sql
-- Purpose: Create 120+ Bills with all status variants
-- Bills create journal entries when posted
-- Timeline: Nov 2025 - Jan 2026
-- =============================================

DO $$
DECLARE
    v_tenant_id TEXT := current_setting('seed.tenant_id', true);
    v_vendor_ids UUID[];
    v_product_ids UUID[];
    v_wh_atput_id UUID;
    v_bill_id UUID;
    v_journal_id UUID;
    v_line_num INT;
    v_bill_count INT := 0;
    v_random_vendor UUID;
    v_vendor_name TEXT;
    v_random_product UUID;
    v_qty INT;
    v_price BIGINT;
    v_subtotal BIGINT;
    v_tax_amount BIGINT;
    v_total_amount BIGINT;
    v_bill_date DATE;
    v_due_date DATE;
    v_status TEXT;
    v_coa_inventory UUID;
    v_coa_ap UUID;
    v_coa_tax_input UUID;
BEGIN
    IF v_tenant_id IS NULL OR v_tenant_id = '' THEN
        v_tenant_id := 'evlogia';
    END IF;

    RAISE NOTICE 'Creating bills for tenant: %', v_tenant_id;

    -- Get warehouse and CoA IDs
    SELECT id INTO v_wh_atput_id FROM warehouses WHERE tenant_id = v_tenant_id AND code = 'WH-ATPUT';
    SELECT id INTO v_coa_inventory FROM chart_of_accounts WHERE tenant_id = v_tenant_id AND account_code = '1-10400';
    SELECT id INTO v_coa_ap FROM chart_of_accounts WHERE tenant_id = v_tenant_id AND account_code = '2-10100';
    SELECT id INTO v_coa_tax_input FROM chart_of_accounts WHERE tenant_id = v_tenant_id AND account_code = '1-10700';

    -- Get vendor IDs
    SELECT array_agg(id) INTO v_vendor_ids FROM vendors WHERE tenant_id = v_tenant_id;

    -- Get product IDs for inventory items
    SELECT array_agg(id) INTO v_product_ids
    FROM products
    WHERE tenant_id = v_tenant_id AND item_type = 'goods';

    -- ==========================================
    -- Generate 120+ Bills
    -- Distribution: Nov (35), Dec (45), Jan (40)
    -- Status: draft(10), received(25), partial(15), paid(50), overdue(10), void(10)
    -- ==========================================

    -- NOVEMBER 2025 - 35 Bills
    FOR i IN 1..35 LOOP
        v_bill_date := '2025-11-01'::DATE + (random() * 29)::INT;
        v_due_date := v_bill_date + 30;  -- NET 30
        v_bill_id := gen_random_uuid();
        v_random_vendor := v_vendor_ids[1 + (random() * (array_length(v_vendor_ids, 1) - 1))::INT];

        SELECT name INTO v_vendor_name FROM vendors WHERE id = v_random_vendor;

        -- Determine status
        IF i <= 3 THEN v_status := 'draft';
        ELSIF i <= 10 THEN v_status := 'received';
        ELSIF i <= 15 THEN v_status := 'partial';
        ELSIF i <= 28 THEN v_status := 'paid';
        ELSIF i <= 32 THEN v_status := 'overdue';
        ELSE v_status := 'void';
        END IF;

        v_subtotal := 0;

        -- Insert Bill header
        INSERT INTO bills (
            id, tenant_id, invoice_number, vendor_id, vendor_name,
            amount, amount_paid, status, issue_date, due_date,
            warehouse_id, created_at, updated_at
        ) VALUES (
            v_bill_id, v_tenant_id,
            'BILL-2511-' || LPAD((i)::TEXT, 4, '0'),
            v_random_vendor, v_vendor_name,
            0, 0, v_status, v_bill_date, v_due_date,
            v_wh_atput_id, v_bill_date, v_bill_date
        );

        -- Insert 2-5 line items
        FOR j IN 1..(2 + (random() * 3)::INT) LOOP
            v_line_num := j;
            v_random_product := v_product_ids[1 + (random() * (array_length(v_product_ids, 1) - 1))::INT];
            v_qty := 5 + (random() * 45)::INT;

            SELECT purchase_price INTO v_price FROM products WHERE id = v_random_product;
            v_price := COALESCE(v_price, 50000);

            INSERT INTO bill_items (
                id, bill_id, product_id, description,
                quantity, unit, unit_price, subtotal, line_number
            )
            SELECT
                gen_random_uuid(), v_bill_id, v_random_product, p.nama_produk,
                v_qty, p.satuan, v_price, v_qty * v_price, v_line_num
            FROM products p WHERE p.id = v_random_product;

            v_subtotal := v_subtotal + (v_qty * v_price);
        END LOOP;

        v_tax_amount := (v_subtotal * 11 / 100);
        v_total_amount := v_subtotal + v_tax_amount;

        -- Update bill amounts
        UPDATE bills
        SET amount = v_total_amount,
            amount_paid = CASE
                WHEN v_status = 'paid' THEN v_total_amount
                WHEN v_status = 'partial' THEN (v_total_amount * 0.5)::BIGINT
                ELSE 0
            END
        WHERE id = v_bill_id;

        -- Create journal entry for non-draft bills
        IF v_status NOT IN ('draft', 'void') THEN
            v_journal_id := gen_random_uuid();

            INSERT INTO journal_entries (
                id, tenant_id, journal_number, entry_date, journal_type,
                source_type, source_id, description,
                total_debit, total_credit, status,
                created_at, updated_at, posted_at
            ) VALUES (
                v_journal_id, v_tenant_id,
                'JV-BILL-2511-' || LPAD((i)::TEXT, 4, '0'),
                v_bill_date, 'PURCHASE',
                'bills', v_bill_id,
                'Bill from ' || v_vendor_name,
                v_total_amount, v_total_amount, 'POSTED',
                v_bill_date, v_bill_date, v_bill_date
            );

            -- Dr. Inventory
            INSERT INTO journal_lines (id, tenant_id, journal_id, line_number, account_id, debit, credit, description)
            VALUES (gen_random_uuid(), v_tenant_id, v_journal_id, 1, v_coa_inventory, v_subtotal, 0, 'Pembelian Persediaan');

            -- Dr. PPN Masukan
            INSERT INTO journal_lines (id, tenant_id, journal_id, line_number, account_id, debit, credit, description)
            VALUES (gen_random_uuid(), v_tenant_id, v_journal_id, 2, v_coa_tax_input, v_tax_amount, 0, 'PPN Masukan 11%');

            -- Cr. Hutang Usaha
            INSERT INTO journal_lines (id, tenant_id, journal_id, line_number, account_id, debit, credit, description)
            VALUES (gen_random_uuid(), v_tenant_id, v_journal_id, 3, v_coa_ap, 0, v_total_amount, 'Hutang ke ' || v_vendor_name);

            -- Update bill with journal reference
            UPDATE bills SET journal_id = v_journal_id WHERE id = v_bill_id;
        END IF;

        v_bill_count := v_bill_count + 1;
    END LOOP;

    -- DECEMBER 2025 - 45 Bills
    FOR i IN 1..45 LOOP
        v_bill_date := '2025-12-01'::DATE + (random() * 30)::INT;
        v_due_date := v_bill_date + 30;
        v_bill_id := gen_random_uuid();
        v_random_vendor := v_vendor_ids[1 + (random() * (array_length(v_vendor_ids, 1) - 1))::INT];

        SELECT name INTO v_vendor_name FROM vendors WHERE id = v_random_vendor;

        IF i <= 4 THEN v_status := 'draft';
        ELSIF i <= 12 THEN v_status := 'received';
        ELSIF i <= 20 THEN v_status := 'partial';
        ELSIF i <= 36 THEN v_status := 'paid';
        ELSIF i <= 42 THEN v_status := 'overdue';
        ELSE v_status := 'void';
        END IF;

        v_subtotal := 0;

        INSERT INTO bills (
            id, tenant_id, invoice_number, vendor_id, vendor_name,
            amount, amount_paid, status, issue_date, due_date,
            warehouse_id, created_at, updated_at
        ) VALUES (
            v_bill_id, v_tenant_id,
            'BILL-2512-' || LPAD((i)::TEXT, 4, '0'),
            v_random_vendor, v_vendor_name,
            0, 0, v_status, v_bill_date, v_due_date,
            v_wh_atput_id, v_bill_date, v_bill_date
        );

        FOR j IN 1..(2 + (random() * 4)::INT) LOOP
            v_line_num := j;
            v_random_product := v_product_ids[1 + (random() * (array_length(v_product_ids, 1) - 1))::INT];
            v_qty := 10 + (random() * 50)::INT;

            SELECT purchase_price INTO v_price FROM products WHERE id = v_random_product;
            v_price := COALESCE(v_price, 50000);

            INSERT INTO bill_items (
                id, bill_id, product_id, description,
                quantity, unit, unit_price, subtotal, line_number
            )
            SELECT
                gen_random_uuid(), v_bill_id, v_random_product, p.nama_produk,
                v_qty, p.satuan, v_price, v_qty * v_price, v_line_num
            FROM products p WHERE p.id = v_random_product;

            v_subtotal := v_subtotal + (v_qty * v_price);
        END LOOP;

        v_tax_amount := (v_subtotal * 11 / 100);
        v_total_amount := v_subtotal + v_tax_amount;

        UPDATE bills
        SET amount = v_total_amount,
            amount_paid = CASE
                WHEN v_status = 'paid' THEN v_total_amount
                WHEN v_status = 'partial' THEN (v_total_amount * 0.6)::BIGINT
                ELSE 0
            END
        WHERE id = v_bill_id;

        IF v_status NOT IN ('draft', 'void') THEN
            v_journal_id := gen_random_uuid();

            INSERT INTO journal_entries (
                id, tenant_id, journal_number, entry_date, journal_type,
                source_type, source_id, description,
                total_debit, total_credit, status,
                created_at, updated_at, posted_at
            ) VALUES (
                v_journal_id, v_tenant_id,
                'JV-BILL-2512-' || LPAD((i)::TEXT, 4, '0'),
                v_bill_date, 'PURCHASE',
                'bills', v_bill_id,
                'Bill from ' || v_vendor_name,
                v_total_amount, v_total_amount, 'POSTED',
                v_bill_date, v_bill_date, v_bill_date
            );

            INSERT INTO journal_lines (id, tenant_id, journal_id, line_number, account_id, debit, credit, description)
            VALUES (gen_random_uuid(), v_tenant_id, v_journal_id, 1, v_coa_inventory, v_subtotal, 0, 'Pembelian Persediaan');

            INSERT INTO journal_lines (id, tenant_id, journal_id, line_number, account_id, debit, credit, description)
            VALUES (gen_random_uuid(), v_tenant_id, v_journal_id, 2, v_coa_tax_input, v_tax_amount, 0, 'PPN Masukan 11%');

            INSERT INTO journal_lines (id, tenant_id, journal_id, line_number, account_id, debit, credit, description)
            VALUES (gen_random_uuid(), v_tenant_id, v_journal_id, 3, v_coa_ap, 0, v_total_amount, 'Hutang ke ' || v_vendor_name);

            UPDATE bills SET journal_id = v_journal_id WHERE id = v_bill_id;
        END IF;

        v_bill_count := v_bill_count + 1;
    END LOOP;

    -- JANUARY 2026 - 40 Bills
    FOR i IN 1..40 LOOP
        v_bill_date := '2026-01-01'::DATE + (random() * 15)::INT;
        v_due_date := v_bill_date + 30;
        v_bill_id := gen_random_uuid();
        v_random_vendor := v_vendor_ids[1 + (random() * (array_length(v_vendor_ids, 1) - 1))::INT];

        SELECT name INTO v_vendor_name FROM vendors WHERE id = v_random_vendor;

        IF i <= 5 THEN v_status := 'draft';
        ELSIF i <= 15 THEN v_status := 'received';
        ELSIF i <= 22 THEN v_status := 'partial';
        ELSIF i <= 32 THEN v_status := 'paid';
        ELSIF i <= 37 THEN v_status := 'overdue';
        ELSE v_status := 'void';
        END IF;

        v_subtotal := 0;

        INSERT INTO bills (
            id, tenant_id, invoice_number, vendor_id, vendor_name,
            amount, amount_paid, status, issue_date, due_date,
            warehouse_id, created_at, updated_at
        ) VALUES (
            v_bill_id, v_tenant_id,
            'BILL-2601-' || LPAD((i)::TEXT, 4, '0'),
            v_random_vendor, v_vendor_name,
            0, 0, v_status, v_bill_date, v_due_date,
            v_wh_atput_id, v_bill_date, v_bill_date
        );

        FOR j IN 1..(2 + (random() * 3)::INT) LOOP
            v_line_num := j;
            v_random_product := v_product_ids[1 + (random() * (array_length(v_product_ids, 1) - 1))::INT];
            v_qty := 5 + (random() * 35)::INT;

            SELECT purchase_price INTO v_price FROM products WHERE id = v_random_product;
            v_price := COALESCE(v_price, 50000);

            INSERT INTO bill_items (
                id, bill_id, product_id, description,
                quantity, unit, unit_price, subtotal, line_number
            )
            SELECT
                gen_random_uuid(), v_bill_id, v_random_product, p.nama_produk,
                v_qty, p.satuan, v_price, v_qty * v_price, v_line_num
            FROM products p WHERE p.id = v_random_product;

            v_subtotal := v_subtotal + (v_qty * v_price);
        END LOOP;

        v_tax_amount := (v_subtotal * 11 / 100);
        v_total_amount := v_subtotal + v_tax_amount;

        UPDATE bills
        SET amount = v_total_amount,
            amount_paid = CASE
                WHEN v_status = 'paid' THEN v_total_amount
                WHEN v_status = 'partial' THEN (v_total_amount * 0.4)::BIGINT
                ELSE 0
            END
        WHERE id = v_bill_id;

        IF v_status NOT IN ('draft', 'void') THEN
            v_journal_id := gen_random_uuid();

            INSERT INTO journal_entries (
                id, tenant_id, journal_number, entry_date, journal_type,
                source_type, source_id, description,
                total_debit, total_credit, status,
                created_at, updated_at, posted_at
            ) VALUES (
                v_journal_id, v_tenant_id,
                'JV-BILL-2601-' || LPAD((i)::TEXT, 4, '0'),
                v_bill_date, 'PURCHASE',
                'bills', v_bill_id,
                'Bill from ' || v_vendor_name,
                v_total_amount, v_total_amount, 'POSTED',
                v_bill_date, v_bill_date, v_bill_date
            );

            INSERT INTO journal_lines (id, tenant_id, journal_id, line_number, account_id, debit, credit, description)
            VALUES (gen_random_uuid(), v_tenant_id, v_journal_id, 1, v_coa_inventory, v_subtotal, 0, 'Pembelian Persediaan');

            INSERT INTO journal_lines (id, tenant_id, journal_id, line_number, account_id, debit, credit, description)
            VALUES (gen_random_uuid(), v_tenant_id, v_journal_id, 2, v_coa_tax_input, v_tax_amount, 0, 'PPN Masukan 11%');

            INSERT INTO journal_lines (id, tenant_id, journal_id, line_number, account_id, debit, credit, description)
            VALUES (gen_random_uuid(), v_tenant_id, v_journal_id, 3, v_coa_ap, 0, v_total_amount, 'Hutang ke ' || v_vendor_name);

            UPDATE bills SET journal_id = v_journal_id WHERE id = v_bill_id;
        END IF;

        v_bill_count := v_bill_count + 1;
    END LOOP;

    RAISE NOTICE '========================================';
    RAISE NOTICE 'Bills created: %', v_bill_count;
    RAISE NOTICE '========================================';
END $$;

-- Verify Bills by status
SELECT status, COUNT(*) as count, SUM(amount) as total_amount, SUM(amount_paid) as total_paid
FROM bills
WHERE tenant_id = 'evlogia'
GROUP BY status
ORDER BY status;

-- Verify journal entries created
SELECT
    'bills' as source,
    COUNT(DISTINCT je.id) as journal_count,
    SUM(je.total_debit) as total_debit
FROM journal_entries je
WHERE je.tenant_id = 'evlogia' AND je.source_type = 'bills';
