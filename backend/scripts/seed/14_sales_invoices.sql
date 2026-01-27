-- =============================================
-- EVLOGIA SEED: 14_sales_invoices.sql
-- Purpose: Create 150+ Sales Invoices with journal entries
-- Posted invoices CREATE journal entries:
--   Dr. Piutang Usaha (1-10300)
--   Cr. Penjualan (4-10100)
--   Cr. PPN Keluaran (2-10400)
--   Dr. HPP (5-10100)
--   Cr. Persediaan (1-10400)
-- =============================================

DO $$
DECLARE
    v_tenant_id TEXT := current_setting('seed.tenant_id', true);
    v_customer_ids UUID[];
    v_product_ids UUID[];
    v_warehouse_id UUID;
    v_inv_id UUID;
    v_inv_count INT := 0;
    v_random_customer UUID;
    v_random_product UUID;
    v_qty INT;
    v_price BIGINT;
    v_cost BIGINT;
    v_subtotal BIGINT;
    v_tax_amount BIGINT;
    v_total_amount BIGINT;
    v_total_cogs BIGINT;
    v_inv_date DATE;
    v_due_date DATE;
    v_status TEXT;
    v_amount_paid BIGINT;
    -- Journal Entry
    v_journal_id UUID;
    v_ar_account_id UUID;
    v_sales_account_id UUID;
    v_ppn_out_account_id UUID;
    v_cogs_account_id UUID;
    v_inventory_account_id UUID;
BEGIN
    IF v_tenant_id IS NULL OR v_tenant_id = '' THEN
        v_tenant_id := 'evlogia';
    END IF;

    RAISE NOTICE 'Creating sales invoices for tenant: %', v_tenant_id;

    -- Get account IDs for journal entries
    SELECT id INTO v_ar_account_id FROM chart_of_accounts
    WHERE tenant_id = v_tenant_id AND account_code = '1-10300';

    SELECT id INTO v_sales_account_id FROM chart_of_accounts
    WHERE tenant_id = v_tenant_id AND account_code = '4-10100';

    SELECT id INTO v_ppn_out_account_id FROM chart_of_accounts
    WHERE tenant_id = v_tenant_id AND account_code = '2-10400';

    SELECT id INTO v_cogs_account_id FROM chart_of_accounts
    WHERE tenant_id = v_tenant_id AND account_code = '5-10100';

    SELECT id INTO v_inventory_account_id FROM chart_of_accounts
    WHERE tenant_id = v_tenant_id AND account_code = '1-10400';

    -- Get customer IDs
    SELECT array_agg(id) INTO v_customer_ids
    FROM customers
    WHERE tenant_id = v_tenant_id AND credit_limit > 0;

    -- Get product IDs (physical products only, not services)
    SELECT array_agg(id) INTO v_product_ids
    FROM products
    WHERE tenant_id = v_tenant_id
    AND kode_produk NOT LIKE 'SVC-%';

    -- Get main warehouse
    SELECT id INTO v_warehouse_id
    FROM warehouses
    WHERE tenant_id = v_tenant_id AND code = 'WH-ATPUT';

    -- ==========================================
    -- Generate 150+ Sales Invoices
    -- Distribution: Nov (45), Dec (55), Jan (50)
    -- Status: draft(15), posted(40), partial(25), paid(45), overdue(15), void(10)
    -- ==========================================

    -- NOVEMBER 2025 - 45 Invoices
    FOR i IN 1..45 LOOP
        v_inv_date := '2025-11-01'::DATE + (random() * 29)::INT;
        v_inv_id := gen_random_uuid();
        v_random_customer := v_customer_ids[1 + (random() * (array_length(v_customer_ids, 1) - 1))::INT];

        -- Get customer payment terms
        SELECT payment_terms_days INTO v_due_date
        FROM customers WHERE id = v_random_customer;
        v_due_date := v_inv_date + COALESCE(v_due_date, 30);

        IF i <= 5 THEN v_status := 'draft';
        ELSIF i <= 17 THEN v_status := 'posted';
        ELSIF i <= 25 THEN v_status := 'partial';
        ELSIF i <= 38 THEN v_status := 'paid';
        ELSIF i <= 42 THEN v_status := 'overdue';
        ELSE v_status := 'void';
        END IF;

        v_subtotal := 0;
        v_total_cogs := 0;

        INSERT INTO sales_invoices (
            id, tenant_id, invoice_number, invoice_date, due_date,
            customer_id, warehouse_id, subtotal, discount_type, discount_value,
            discount_amount, tax_amount, total_amount, amount_paid, status, notes,
            created_at, updated_at
        ) VALUES (
            v_inv_id, v_tenant_id,
            'INV-2511-' || LPAD((i)::TEXT, 4, '0'),
            v_inv_date, v_due_date,
            v_random_customer, v_warehouse_id, 0, 'percent', 0, 0, 0, 0, 0, v_status,
            'Invoice November ' || i,
            v_inv_date, v_inv_date
        );

        -- Insert 2-5 line items
        FOR j IN 1..(2 + (random() * 3)::INT) LOOP
            v_random_product := v_product_ids[1 + (random() * (array_length(v_product_ids, 1) - 1))::INT];
            v_qty := 3 + (random() * 20)::INT;

            SELECT sales_price, purchase_price INTO v_price, v_cost
            FROM products WHERE id = v_random_product;
            v_price := COALESCE(v_price, 100000);
            v_cost := COALESCE(v_cost, v_price * 70 / 100);

            INSERT INTO sales_invoice_items (
                id, invoice_id, product_id, description,
                quantity, unit, unit_price, unit_cost,
                discount_percent, tax_rate, tax_amount, line_total, sort_order
            )
            SELECT
                gen_random_uuid(), v_inv_id, v_random_product, p.nama_produk,
                v_qty, p.satuan, v_price, v_cost, 0,
                11, (v_qty * v_price * 11 / 100), v_qty * v_price * 111 / 100, j
            FROM products p WHERE p.id = v_random_product;

            v_subtotal := v_subtotal + (v_qty * v_price);
            v_total_cogs := v_total_cogs + (v_qty * v_cost);
        END LOOP;

        v_tax_amount := (v_subtotal * 11 / 100);
        v_total_amount := v_subtotal + v_tax_amount;

        -- Calculate amount_paid based on status
        IF v_status = 'paid' THEN
            v_amount_paid := v_total_amount;
        ELSIF v_status = 'partial' THEN
            v_amount_paid := (v_total_amount * (30 + random() * 60)::INT / 100);
        ELSE
            v_amount_paid := 0;
        END IF;

        UPDATE sales_invoices
        SET subtotal = v_subtotal, tax_amount = v_tax_amount,
            total_amount = v_total_amount, amount_paid = v_amount_paid
        WHERE id = v_inv_id;

        -- Create journal entry for posted/partial/paid/overdue invoices
        IF v_status IN ('posted', 'partial', 'paid', 'overdue') AND v_ar_account_id IS NOT NULL THEN
            v_journal_id := gen_random_uuid();

            INSERT INTO journal_entries (
                id, tenant_id, entry_number, entry_date, description,
                source_type, source_id, status, created_at, updated_at
            ) VALUES (
                v_journal_id, v_tenant_id,
                'JE-INV-2511-' || LPAD((i)::TEXT, 4, '0'),
                v_inv_date, 'Sales Invoice INV-2511-' || LPAD((i)::TEXT, 4, '0'),
                'sales_invoice', v_inv_id, 'POSTED', v_inv_date, v_inv_date
            );

            -- Dr. Piutang Usaha
            INSERT INTO journal_lines (id, journal_id, account_id, debit, credit, description)
            VALUES (gen_random_uuid(), v_journal_id, v_ar_account_id, v_total_amount, 0, 'Piutang penjualan');

            -- Cr. Penjualan
            INSERT INTO journal_lines (id, journal_id, account_id, debit, credit, description)
            VALUES (gen_random_uuid(), v_journal_id, v_sales_account_id, 0, v_subtotal, 'Pendapatan penjualan');

            -- Cr. PPN Keluaran
            INSERT INTO journal_lines (id, journal_id, account_id, debit, credit, description)
            VALUES (gen_random_uuid(), v_journal_id, v_ppn_out_account_id, 0, v_tax_amount, 'PPN Keluaran 11%');

            -- COGS Journal (Dr. HPP, Cr. Persediaan)
            IF v_cogs_account_id IS NOT NULL AND v_inventory_account_id IS NOT NULL THEN
                INSERT INTO journal_lines (id, journal_id, account_id, debit, credit, description)
                VALUES (gen_random_uuid(), v_journal_id, v_cogs_account_id, v_total_cogs, 0, 'Harga Pokok Penjualan');

                INSERT INTO journal_lines (id, journal_id, account_id, debit, credit, description)
                VALUES (gen_random_uuid(), v_journal_id, v_inventory_account_id, 0, v_total_cogs, 'Pengeluaran persediaan');
            END IF;
        END IF;

        v_inv_count := v_inv_count + 1;
    END LOOP;

    -- DECEMBER 2025 - 55 Invoices
    FOR i IN 1..55 LOOP
        v_inv_date := '2025-12-01'::DATE + (random() * 30)::INT;
        v_inv_id := gen_random_uuid();
        v_random_customer := v_customer_ids[1 + (random() * (array_length(v_customer_ids, 1) - 1))::INT];

        SELECT payment_terms_days INTO v_due_date
        FROM customers WHERE id = v_random_customer;
        v_due_date := v_inv_date + COALESCE(v_due_date, 30);

        IF i <= 5 THEN v_status := 'draft';
        ELSIF i <= 18 THEN v_status := 'posted';
        ELSIF i <= 28 THEN v_status := 'partial';
        ELSIF i <= 45 THEN v_status := 'paid';
        ELSIF i <= 52 THEN v_status := 'overdue';
        ELSE v_status := 'void';
        END IF;

        v_subtotal := 0;
        v_total_cogs := 0;

        INSERT INTO sales_invoices (
            id, tenant_id, invoice_number, invoice_date, due_date,
            customer_id, warehouse_id, subtotal, discount_type, discount_value,
            discount_amount, tax_amount, total_amount, amount_paid, status, notes,
            created_at, updated_at
        ) VALUES (
            v_inv_id, v_tenant_id,
            'INV-2512-' || LPAD((i)::TEXT, 4, '0'),
            v_inv_date, v_due_date,
            v_random_customer, v_warehouse_id, 0, 'percent', 0, 0, 0, 0, 0, v_status,
            'Invoice December ' || i,
            v_inv_date, v_inv_date
        );

        FOR j IN 1..(2 + (random() * 4)::INT) LOOP
            v_random_product := v_product_ids[1 + (random() * (array_length(v_product_ids, 1) - 1))::INT];
            v_qty := 3 + (random() * 25)::INT;

            SELECT sales_price, purchase_price INTO v_price, v_cost
            FROM products WHERE id = v_random_product;
            v_price := COALESCE(v_price, 100000);
            v_cost := COALESCE(v_cost, v_price * 70 / 100);

            INSERT INTO sales_invoice_items (
                id, invoice_id, product_id, description,
                quantity, unit, unit_price, unit_cost,
                discount_percent, tax_rate, tax_amount, line_total, sort_order
            )
            SELECT
                gen_random_uuid(), v_inv_id, v_random_product, p.nama_produk,
                v_qty, p.satuan, v_price, v_cost, 0,
                11, (v_qty * v_price * 11 / 100), v_qty * v_price * 111 / 100, j
            FROM products p WHERE p.id = v_random_product;

            v_subtotal := v_subtotal + (v_qty * v_price);
            v_total_cogs := v_total_cogs + (v_qty * v_cost);
        END LOOP;

        v_tax_amount := (v_subtotal * 11 / 100);
        v_total_amount := v_subtotal + v_tax_amount;

        IF v_status = 'paid' THEN
            v_amount_paid := v_total_amount;
        ELSIF v_status = 'partial' THEN
            v_amount_paid := (v_total_amount * (30 + random() * 60)::INT / 100);
        ELSE
            v_amount_paid := 0;
        END IF;

        UPDATE sales_invoices
        SET subtotal = v_subtotal, tax_amount = v_tax_amount,
            total_amount = v_total_amount, amount_paid = v_amount_paid
        WHERE id = v_inv_id;

        IF v_status IN ('posted', 'partial', 'paid', 'overdue') AND v_ar_account_id IS NOT NULL THEN
            v_journal_id := gen_random_uuid();

            INSERT INTO journal_entries (
                id, tenant_id, entry_number, entry_date, description,
                source_type, source_id, status, created_at, updated_at
            ) VALUES (
                v_journal_id, v_tenant_id,
                'JE-INV-2512-' || LPAD((i)::TEXT, 4, '0'),
                v_inv_date, 'Sales Invoice INV-2512-' || LPAD((i)::TEXT, 4, '0'),
                'sales_invoice', v_inv_id, 'POSTED', v_inv_date, v_inv_date
            );

            INSERT INTO journal_lines (id, journal_id, account_id, debit, credit, description)
            VALUES (gen_random_uuid(), v_journal_id, v_ar_account_id, v_total_amount, 0, 'Piutang penjualan');

            INSERT INTO journal_lines (id, journal_id, account_id, debit, credit, description)
            VALUES (gen_random_uuid(), v_journal_id, v_sales_account_id, 0, v_subtotal, 'Pendapatan penjualan');

            INSERT INTO journal_lines (id, journal_id, account_id, debit, credit, description)
            VALUES (gen_random_uuid(), v_journal_id, v_ppn_out_account_id, 0, v_tax_amount, 'PPN Keluaran 11%');

            IF v_cogs_account_id IS NOT NULL AND v_inventory_account_id IS NOT NULL THEN
                INSERT INTO journal_lines (id, journal_id, account_id, debit, credit, description)
                VALUES (gen_random_uuid(), v_journal_id, v_cogs_account_id, v_total_cogs, 0, 'Harga Pokok Penjualan');

                INSERT INTO journal_lines (id, journal_id, account_id, debit, credit, description)
                VALUES (gen_random_uuid(), v_journal_id, v_inventory_account_id, 0, v_total_cogs, 'Pengeluaran persediaan');
            END IF;
        END IF;

        v_inv_count := v_inv_count + 1;
    END LOOP;

    -- JANUARY 2026 - 50 Invoices
    FOR i IN 1..50 LOOP
        v_inv_date := '2026-01-01'::DATE + (random() * 15)::INT;
        v_inv_id := gen_random_uuid();
        v_random_customer := v_customer_ids[1 + (random() * (array_length(v_customer_ids, 1) - 1))::INT];

        SELECT payment_terms_days INTO v_due_date
        FROM customers WHERE id = v_random_customer;
        v_due_date := v_inv_date + COALESCE(v_due_date, 30);

        IF i <= 5 THEN v_status := 'draft';
        ELSIF i <= 15 THEN v_status := 'posted';
        ELSIF i <= 23 THEN v_status := 'partial';
        ELSIF i <= 40 THEN v_status := 'paid';
        ELSIF i <= 47 THEN v_status := 'overdue';
        ELSE v_status := 'void';
        END IF;

        v_subtotal := 0;
        v_total_cogs := 0;

        INSERT INTO sales_invoices (
            id, tenant_id, invoice_number, invoice_date, due_date,
            customer_id, warehouse_id, subtotal, discount_type, discount_value,
            discount_amount, tax_amount, total_amount, amount_paid, status, notes,
            created_at, updated_at
        ) VALUES (
            v_inv_id, v_tenant_id,
            'INV-2601-' || LPAD((i)::TEXT, 4, '0'),
            v_inv_date, v_due_date,
            v_random_customer, v_warehouse_id, 0, 'percent', 0, 0, 0, 0, 0, v_status,
            'Invoice January ' || i,
            v_inv_date, v_inv_date
        );

        FOR j IN 1..(2 + (random() * 3)::INT) LOOP
            v_random_product := v_product_ids[1 + (random() * (array_length(v_product_ids, 1) - 1))::INT];
            v_qty := 3 + (random() * 20)::INT;

            SELECT sales_price, purchase_price INTO v_price, v_cost
            FROM products WHERE id = v_random_product;
            v_price := COALESCE(v_price, 100000);
            v_cost := COALESCE(v_cost, v_price * 70 / 100);

            INSERT INTO sales_invoice_items (
                id, invoice_id, product_id, description,
                quantity, unit, unit_price, unit_cost,
                discount_percent, tax_rate, tax_amount, line_total, sort_order
            )
            SELECT
                gen_random_uuid(), v_inv_id, v_random_product, p.nama_produk,
                v_qty, p.satuan, v_price, v_cost, 0,
                11, (v_qty * v_price * 11 / 100), v_qty * v_price * 111 / 100, j
            FROM products p WHERE p.id = v_random_product;

            v_subtotal := v_subtotal + (v_qty * v_price);
            v_total_cogs := v_total_cogs + (v_qty * v_cost);
        END LOOP;

        v_tax_amount := (v_subtotal * 11 / 100);
        v_total_amount := v_subtotal + v_tax_amount;

        IF v_status = 'paid' THEN
            v_amount_paid := v_total_amount;
        ELSIF v_status = 'partial' THEN
            v_amount_paid := (v_total_amount * (30 + random() * 60)::INT / 100);
        ELSE
            v_amount_paid := 0;
        END IF;

        UPDATE sales_invoices
        SET subtotal = v_subtotal, tax_amount = v_tax_amount,
            total_amount = v_total_amount, amount_paid = v_amount_paid
        WHERE id = v_inv_id;

        IF v_status IN ('posted', 'partial', 'paid', 'overdue') AND v_ar_account_id IS NOT NULL THEN
            v_journal_id := gen_random_uuid();

            INSERT INTO journal_entries (
                id, tenant_id, entry_number, entry_date, description,
                source_type, source_id, status, created_at, updated_at
            ) VALUES (
                v_journal_id, v_tenant_id,
                'JE-INV-2601-' || LPAD((i)::TEXT, 4, '0'),
                v_inv_date, 'Sales Invoice INV-2601-' || LPAD((i)::TEXT, 4, '0'),
                'sales_invoice', v_inv_id, 'POSTED', v_inv_date, v_inv_date
            );

            INSERT INTO journal_lines (id, journal_id, account_id, debit, credit, description)
            VALUES (gen_random_uuid(), v_journal_id, v_ar_account_id, v_total_amount, 0, 'Piutang penjualan');

            INSERT INTO journal_lines (id, journal_id, account_id, debit, credit, description)
            VALUES (gen_random_uuid(), v_journal_id, v_sales_account_id, 0, v_subtotal, 'Pendapatan penjualan');

            INSERT INTO journal_lines (id, journal_id, account_id, debit, credit, description)
            VALUES (gen_random_uuid(), v_journal_id, v_ppn_out_account_id, 0, v_tax_amount, 'PPN Keluaran 11%');

            IF v_cogs_account_id IS NOT NULL AND v_inventory_account_id IS NOT NULL THEN
                INSERT INTO journal_lines (id, journal_id, account_id, debit, credit, description)
                VALUES (gen_random_uuid(), v_journal_id, v_cogs_account_id, v_total_cogs, 0, 'Harga Pokok Penjualan');

                INSERT INTO journal_lines (id, journal_id, account_id, debit, credit, description)
                VALUES (gen_random_uuid(), v_journal_id, v_inventory_account_id, 0, v_total_cogs, 'Pengeluaran persediaan');
            END IF;
        END IF;

        v_inv_count := v_inv_count + 1;
    END LOOP;

    RAISE NOTICE '========================================';
    RAISE NOTICE 'Sales Invoices created: %', v_inv_count;
    RAISE NOTICE '========================================';
END $$;

-- Verify Sales Invoices by status
SELECT status, COUNT(*) as count, SUM(total_amount) as total_value, SUM(amount_paid) as total_paid
FROM sales_invoices
WHERE tenant_id = 'evlogia'
GROUP BY status
ORDER BY status;
