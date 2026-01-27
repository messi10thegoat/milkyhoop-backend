-- =============================================
-- EVLOGIA SEED: 15_sales_receipts.sql
-- Purpose: Create 200+ POS Sales Receipts with journal entries
-- POS Receipts CREATE journal entries:
--   Dr. Kas/Bank (1-10100/1-10200)
--   Cr. Penjualan (4-10100)
--   Cr. PPN Keluaran (2-10400)
--   Dr. HPP (5-10100)
--   Cr. Persediaan (1-10400)
-- =============================================

DO $$
DECLARE
    v_tenant_id TEXT := current_setting('seed.tenant_id', true);
    v_product_ids UUID[];
    v_warehouse_id UUID;
    v_cash_customer_id UUID;
    v_receipt_id UUID;
    v_receipt_count INT := 0;
    v_random_product UUID;
    v_qty INT;
    v_price BIGINT;
    v_cost BIGINT;
    v_subtotal BIGINT;
    v_tax_amount BIGINT;
    v_total_amount BIGINT;
    v_total_cogs BIGINT;
    v_receipt_date DATE;
    v_status TEXT;
    v_payment_method TEXT;
    -- Journal Entry
    v_journal_id UUID;
    v_cash_account_id UUID;
    v_bank_account_id UUID;
    v_sales_account_id UUID;
    v_ppn_out_account_id UUID;
    v_cogs_account_id UUID;
    v_inventory_account_id UUID;
    v_payment_account_id UUID;
BEGIN
    IF v_tenant_id IS NULL OR v_tenant_id = '' THEN
        v_tenant_id := 'evlogia';
    END IF;

    RAISE NOTICE 'Creating sales receipts (POS) for tenant: %', v_tenant_id;

    -- Get account IDs for journal entries
    SELECT id INTO v_cash_account_id FROM chart_of_accounts
    WHERE tenant_id = v_tenant_id AND account_code = '1-10100';

    SELECT id INTO v_bank_account_id FROM chart_of_accounts
    WHERE tenant_id = v_tenant_id AND account_code = '1-10201';  -- BCA

    SELECT id INTO v_sales_account_id FROM chart_of_accounts
    WHERE tenant_id = v_tenant_id AND account_code = '4-10100';

    SELECT id INTO v_ppn_out_account_id FROM chart_of_accounts
    WHERE tenant_id = v_tenant_id AND account_code = '2-10400';

    SELECT id INTO v_cogs_account_id FROM chart_of_accounts
    WHERE tenant_id = v_tenant_id AND account_code = '5-10100';

    SELECT id INTO v_inventory_account_id FROM chart_of_accounts
    WHERE tenant_id = v_tenant_id AND account_code = '1-10400';

    -- Get cash customer (walk-in)
    SELECT id INTO v_cash_customer_id
    FROM customers
    WHERE tenant_id = v_tenant_id AND code = 'CST-022';  -- Cash Customer

    -- Get product IDs (sellable physical products and FG)
    SELECT array_agg(id) INTO v_product_ids
    FROM products
    WHERE tenant_id = v_tenant_id
    AND kode_produk NOT LIKE 'SVC-%'  -- Exclude services
    AND (kode_produk LIKE 'IMP-%' OR kode_produk LIKE 'EVL-%');  -- Only FG Trading & FG Produksi

    -- Get showroom warehouse (4A)
    SELECT id INTO v_warehouse_id
    FROM warehouses
    WHERE tenant_id = v_tenant_id AND code = 'WH-4A';

    IF v_warehouse_id IS NULL THEN
        SELECT id INTO v_warehouse_id
        FROM warehouses
        WHERE tenant_id = v_tenant_id AND code = 'WH-ATPUT';
    END IF;

    -- ==========================================
    -- Generate 200+ POS Sales Receipts
    -- Distribution: Nov (60), Dec (80), Jan (60)
    -- Status: completed(190), void(10)
    -- Payment: cash(100), transfer(60), debit(25), qris(15)
    -- ==========================================

    -- NOVEMBER 2025 - 60 Receipts
    FOR i IN 1..60 LOOP
        v_receipt_date := '2025-11-01'::DATE + (random() * 29)::INT;
        v_receipt_id := gen_random_uuid();

        IF i <= 58 THEN v_status := 'completed';
        ELSE v_status := 'void';
        END IF;

        -- Payment method distribution
        IF i <= 30 THEN v_payment_method := 'cash';
        ELSIF i <= 48 THEN v_payment_method := 'transfer';
        ELSIF i <= 55 THEN v_payment_method := 'debit';
        ELSE v_payment_method := 'qris';
        END IF;

        v_subtotal := 0;
        v_total_cogs := 0;

        INSERT INTO sales_receipts (
            id, tenant_id, receipt_number, receipt_date,
            customer_id, warehouse_id, subtotal, discount_type, discount_value,
            discount_amount, tax_amount, total_amount, payment_method,
            payment_reference, status, notes, created_at, updated_at
        ) VALUES (
            v_receipt_id, v_tenant_id,
            'POS-2511-' || LPAD((i)::TEXT, 4, '0'),
            v_receipt_date,
            v_cash_customer_id, v_warehouse_id, 0, 'percent', 0, 0, 0, 0,
            v_payment_method,
            CASE WHEN v_payment_method != 'cash' THEN 'REF-' || i ELSE NULL END,
            v_status, 'POS Sale November ' || i,
            v_receipt_date, v_receipt_date
        );

        -- Insert 1-4 line items (POS typically smaller transactions)
        FOR j IN 1..(1 + (random() * 3)::INT) LOOP
            v_random_product := v_product_ids[1 + (random() * (array_length(v_product_ids, 1) - 1))::INT];
            v_qty := 1 + (random() * 5)::INT;

            SELECT sales_price, purchase_price INTO v_price, v_cost
            FROM products WHERE id = v_random_product;
            v_price := COALESCE(v_price, 150000);
            v_cost := COALESCE(v_cost, v_price * 70 / 100);

            INSERT INTO sales_receipt_items (
                id, receipt_id, product_id, description,
                quantity, unit, unit_price, unit_cost,
                discount_percent, tax_rate, tax_amount, line_total, sort_order
            )
            SELECT
                gen_random_uuid(), v_receipt_id, v_random_product, p.nama_produk,
                v_qty, p.satuan, v_price, v_cost, 0,
                11, (v_qty * v_price * 11 / 100), v_qty * v_price * 111 / 100, j
            FROM products p WHERE p.id = v_random_product;

            v_subtotal := v_subtotal + (v_qty * v_price);
            v_total_cogs := v_total_cogs + (v_qty * v_cost);
        END LOOP;

        v_tax_amount := (v_subtotal * 11 / 100);
        v_total_amount := v_subtotal + v_tax_amount;

        UPDATE sales_receipts
        SET subtotal = v_subtotal, tax_amount = v_tax_amount, total_amount = v_total_amount
        WHERE id = v_receipt_id;

        -- Create journal entry for completed receipts
        IF v_status = 'completed' AND v_cash_account_id IS NOT NULL THEN
            v_journal_id := gen_random_uuid();

            -- Determine payment account
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
                'JE-POS-2511-' || LPAD((i)::TEXT, 4, '0'),
                v_receipt_date, 'POS Sale POS-2511-' || LPAD((i)::TEXT, 4, '0'),
                'sales_receipt', v_receipt_id, 'POSTED', v_receipt_date, v_receipt_date
            );

            -- Dr. Kas/Bank
            INSERT INTO journal_lines (id, journal_id, account_id, debit, credit, description)
            VALUES (gen_random_uuid(), v_journal_id, v_payment_account_id, v_total_amount, 0, 'Penerimaan penjualan tunai');

            -- Cr. Penjualan
            INSERT INTO journal_lines (id, journal_id, account_id, debit, credit, description)
            VALUES (gen_random_uuid(), v_journal_id, v_sales_account_id, 0, v_subtotal, 'Pendapatan penjualan');

            -- Cr. PPN Keluaran
            INSERT INTO journal_lines (id, journal_id, account_id, debit, credit, description)
            VALUES (gen_random_uuid(), v_journal_id, v_ppn_out_account_id, 0, v_tax_amount, 'PPN Keluaran 11%');

            -- COGS Journal
            IF v_cogs_account_id IS NOT NULL AND v_inventory_account_id IS NOT NULL THEN
                INSERT INTO journal_lines (id, journal_id, account_id, debit, credit, description)
                VALUES (gen_random_uuid(), v_journal_id, v_cogs_account_id, v_total_cogs, 0, 'Harga Pokok Penjualan');

                INSERT INTO journal_lines (id, journal_id, account_id, debit, credit, description)
                VALUES (gen_random_uuid(), v_journal_id, v_inventory_account_id, 0, v_total_cogs, 'Pengeluaran persediaan');
            END IF;
        END IF;

        v_receipt_count := v_receipt_count + 1;
    END LOOP;

    -- DECEMBER 2025 - 80 Receipts (peak season)
    FOR i IN 1..80 LOOP
        v_receipt_date := '2025-12-01'::DATE + (random() * 30)::INT;
        v_receipt_id := gen_random_uuid();

        IF i <= 76 THEN v_status := 'completed';
        ELSE v_status := 'void';
        END IF;

        IF i <= 40 THEN v_payment_method := 'cash';
        ELSIF i <= 62 THEN v_payment_method := 'transfer';
        ELSIF i <= 72 THEN v_payment_method := 'debit';
        ELSE v_payment_method := 'qris';
        END IF;

        v_subtotal := 0;
        v_total_cogs := 0;

        INSERT INTO sales_receipts (
            id, tenant_id, receipt_number, receipt_date,
            customer_id, warehouse_id, subtotal, discount_type, discount_value,
            discount_amount, tax_amount, total_amount, payment_method,
            payment_reference, status, notes, created_at, updated_at
        ) VALUES (
            v_receipt_id, v_tenant_id,
            'POS-2512-' || LPAD((i)::TEXT, 4, '0'),
            v_receipt_date,
            v_cash_customer_id, v_warehouse_id, 0, 'percent', 0, 0, 0, 0,
            v_payment_method,
            CASE WHEN v_payment_method != 'cash' THEN 'REF-' || i ELSE NULL END,
            v_status, 'POS Sale December ' || i,
            v_receipt_date, v_receipt_date
        );

        FOR j IN 1..(1 + (random() * 4)::INT) LOOP
            v_random_product := v_product_ids[1 + (random() * (array_length(v_product_ids, 1) - 1))::INT];
            v_qty := 1 + (random() * 6)::INT;

            SELECT sales_price, purchase_price INTO v_price, v_cost
            FROM products WHERE id = v_random_product;
            v_price := COALESCE(v_price, 150000);
            v_cost := COALESCE(v_cost, v_price * 70 / 100);

            INSERT INTO sales_receipt_items (
                id, receipt_id, product_id, description,
                quantity, unit, unit_price, unit_cost,
                discount_percent, tax_rate, tax_amount, line_total, sort_order
            )
            SELECT
                gen_random_uuid(), v_receipt_id, v_random_product, p.nama_produk,
                v_qty, p.satuan, v_price, v_cost, 0,
                11, (v_qty * v_price * 11 / 100), v_qty * v_price * 111 / 100, j
            FROM products p WHERE p.id = v_random_product;

            v_subtotal := v_subtotal + (v_qty * v_price);
            v_total_cogs := v_total_cogs + (v_qty * v_cost);
        END LOOP;

        v_tax_amount := (v_subtotal * 11 / 100);
        v_total_amount := v_subtotal + v_tax_amount;

        UPDATE sales_receipts
        SET subtotal = v_subtotal, tax_amount = v_tax_amount, total_amount = v_total_amount
        WHERE id = v_receipt_id;

        IF v_status = 'completed' AND v_cash_account_id IS NOT NULL THEN
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
                'JE-POS-2512-' || LPAD((i)::TEXT, 4, '0'),
                v_receipt_date, 'POS Sale POS-2512-' || LPAD((i)::TEXT, 4, '0'),
                'sales_receipt', v_receipt_id, 'POSTED', v_receipt_date, v_receipt_date
            );

            INSERT INTO journal_lines (id, journal_id, account_id, debit, credit, description)
            VALUES (gen_random_uuid(), v_journal_id, v_payment_account_id, v_total_amount, 0, 'Penerimaan penjualan tunai');

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

        v_receipt_count := v_receipt_count + 1;
    END LOOP;

    -- JANUARY 2026 - 60 Receipts
    FOR i IN 1..60 LOOP
        v_receipt_date := '2026-01-01'::DATE + (random() * 15)::INT;
        v_receipt_id := gen_random_uuid();

        IF i <= 56 THEN v_status := 'completed';
        ELSE v_status := 'void';
        END IF;

        IF i <= 30 THEN v_payment_method := 'cash';
        ELSIF i <= 48 THEN v_payment_method := 'transfer';
        ELSIF i <= 55 THEN v_payment_method := 'debit';
        ELSE v_payment_method := 'qris';
        END IF;

        v_subtotal := 0;
        v_total_cogs := 0;

        INSERT INTO sales_receipts (
            id, tenant_id, receipt_number, receipt_date,
            customer_id, warehouse_id, subtotal, discount_type, discount_value,
            discount_amount, tax_amount, total_amount, payment_method,
            payment_reference, status, notes, created_at, updated_at
        ) VALUES (
            v_receipt_id, v_tenant_id,
            'POS-2601-' || LPAD((i)::TEXT, 4, '0'),
            v_receipt_date,
            v_cash_customer_id, v_warehouse_id, 0, 'percent', 0, 0, 0, 0,
            v_payment_method,
            CASE WHEN v_payment_method != 'cash' THEN 'REF-' || i ELSE NULL END,
            v_status, 'POS Sale January ' || i,
            v_receipt_date, v_receipt_date
        );

        FOR j IN 1..(1 + (random() * 3)::INT) LOOP
            v_random_product := v_product_ids[1 + (random() * (array_length(v_product_ids, 1) - 1))::INT];
            v_qty := 1 + (random() * 5)::INT;

            SELECT sales_price, purchase_price INTO v_price, v_cost
            FROM products WHERE id = v_random_product;
            v_price := COALESCE(v_price, 150000);
            v_cost := COALESCE(v_cost, v_price * 70 / 100);

            INSERT INTO sales_receipt_items (
                id, receipt_id, product_id, description,
                quantity, unit, unit_price, unit_cost,
                discount_percent, tax_rate, tax_amount, line_total, sort_order
            )
            SELECT
                gen_random_uuid(), v_receipt_id, v_random_product, p.nama_produk,
                v_qty, p.satuan, v_price, v_cost, 0,
                11, (v_qty * v_price * 11 / 100), v_qty * v_price * 111 / 100, j
            FROM products p WHERE p.id = v_random_product;

            v_subtotal := v_subtotal + (v_qty * v_price);
            v_total_cogs := v_total_cogs + (v_qty * v_cost);
        END LOOP;

        v_tax_amount := (v_subtotal * 11 / 100);
        v_total_amount := v_subtotal + v_tax_amount;

        UPDATE sales_receipts
        SET subtotal = v_subtotal, tax_amount = v_tax_amount, total_amount = v_total_amount
        WHERE id = v_receipt_id;

        IF v_status = 'completed' AND v_cash_account_id IS NOT NULL THEN
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
                'JE-POS-2601-' || LPAD((i)::TEXT, 4, '0'),
                v_receipt_date, 'POS Sale POS-2601-' || LPAD((i)::TEXT, 4, '0'),
                'sales_receipt', v_receipt_id, 'POSTED', v_receipt_date, v_receipt_date
            );

            INSERT INTO journal_lines (id, journal_id, account_id, debit, credit, description)
            VALUES (gen_random_uuid(), v_journal_id, v_payment_account_id, v_total_amount, 0, 'Penerimaan penjualan tunai');

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

        v_receipt_count := v_receipt_count + 1;
    END LOOP;

    RAISE NOTICE '========================================';
    RAISE NOTICE 'Sales Receipts (POS) created: %', v_receipt_count;
    RAISE NOTICE '========================================';
END $$;

-- Verify Sales Receipts by status and payment method
SELECT status, payment_method, COUNT(*) as count, SUM(total_amount) as total_value
FROM sales_receipts
WHERE tenant_id = 'evlogia'
GROUP BY status, payment_method
ORDER BY status, payment_method;
