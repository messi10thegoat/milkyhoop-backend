-- =============================================
-- EVLOGIA SEED: 16_stock_adjustments.sql
-- Purpose: Create 40+ Stock Adjustments with journal entries
-- Posted adjustments CREATE journal entries:
--   Increase: Dr. Persediaan (1-10400), Cr. Penyesuaian Persediaan (6-10100)
--   Decrease: Dr. Penyesuaian Persediaan (6-10100), Cr. Persediaan (1-10400)
-- =============================================

DO $$
DECLARE
    v_tenant_id TEXT := current_setting('seed.tenant_id', true);
    v_product_ids UUID[];
    v_warehouse_ids UUID[];
    v_adj_id UUID;
    v_adj_count INT := 0;
    v_random_product UUID;
    v_random_warehouse UUID;
    v_qty INT;
    v_cost BIGINT;
    v_total_value BIGINT;
    v_adj_date DATE;
    v_status TEXT;
    v_adj_type TEXT;
    v_reason TEXT;
    -- Journal Entry
    v_journal_id UUID;
    v_inventory_account_id UUID;
    v_adjustment_account_id UUID;
BEGIN
    IF v_tenant_id IS NULL OR v_tenant_id = '' THEN
        v_tenant_id := 'evlogia';
    END IF;

    RAISE NOTICE 'Creating stock adjustments for tenant: %', v_tenant_id;

    -- Get account IDs for journal entries
    SELECT id INTO v_inventory_account_id FROM chart_of_accounts
    WHERE tenant_id = v_tenant_id AND account_code = '1-10400';

    SELECT id INTO v_adjustment_account_id FROM chart_of_accounts
    WHERE tenant_id = v_tenant_id AND account_code = '6-10100';

    -- Get product IDs (physical products only)
    SELECT array_agg(id) INTO v_product_ids
    FROM products
    WHERE tenant_id = v_tenant_id
    AND kode_produk NOT LIKE 'SVC-%';

    -- Get warehouse IDs
    SELECT array_agg(id) INTO v_warehouse_ids
    FROM warehouses
    WHERE tenant_id = v_tenant_id;

    -- ==========================================
    -- Generate 40+ Stock Adjustments
    -- Distribution: Nov (12), Dec (15), Jan (13)
    -- Status: draft(8), posted(28), void(4)
    -- Type: increase(15), decrease(10), recount(15)
    -- ==========================================

    -- NOVEMBER 2025 - 12 Adjustments
    FOR i IN 1..12 LOOP
        v_adj_date := '2025-11-01'::DATE + (random() * 29)::INT;
        v_adj_id := gen_random_uuid();
        v_random_warehouse := v_warehouse_ids[1 + (random() * (array_length(v_warehouse_ids, 1) - 1))::INT];

        IF i <= 2 THEN v_status := 'draft';
        ELSIF i <= 11 THEN v_status := 'posted';
        ELSE v_status := 'void';
        END IF;

        -- Adjustment type
        IF i <= 4 THEN
            v_adj_type := 'increase';
            v_reason := 'Barang ditemukan di gudang';
        ELSIF i <= 7 THEN
            v_adj_type := 'decrease';
            v_reason := 'Barang rusak/expired';
        ELSE
            v_adj_type := 'recount';
            v_reason := 'Stock opname bulanan';
        END IF;

        v_total_value := 0;

        INSERT INTO stock_adjustments (
            id, tenant_id, adjustment_number, adjustment_date,
            warehouse_id, adjustment_type, reason, total_value,
            status, notes, created_at, updated_at
        ) VALUES (
            v_adj_id, v_tenant_id,
            'ADJ-2511-' || LPAD((i)::TEXT, 4, '0'),
            v_adj_date, v_random_warehouse, v_adj_type, v_reason, 0,
            v_status, 'Adjustment November ' || i,
            v_adj_date, v_adj_date
        );

        -- Insert 2-5 line items
        FOR j IN 1..(2 + (random() * 3)::INT) LOOP
            v_random_product := v_product_ids[1 + (random() * (array_length(v_product_ids, 1) - 1))::INT];
            v_qty := 1 + (random() * 20)::INT;

            SELECT purchase_price INTO v_cost FROM products WHERE id = v_random_product;
            v_cost := COALESCE(v_cost, 50000);

            INSERT INTO stock_adjustment_items (
                id, adjustment_id, product_id, quantity,
                unit_cost, line_value, notes
            )
            SELECT
                gen_random_uuid(), v_adj_id, v_random_product, v_qty,
                v_cost, v_qty * v_cost, p.nama_produk || ' - qty: ' || v_qty
            FROM products p WHERE p.id = v_random_product;

            v_total_value := v_total_value + (v_qty * v_cost);
        END LOOP;

        UPDATE stock_adjustments
        SET total_value = v_total_value
        WHERE id = v_adj_id;

        -- Create journal entry for posted adjustments
        IF v_status = 'posted' AND v_inventory_account_id IS NOT NULL AND v_adjustment_account_id IS NOT NULL THEN
            v_journal_id := gen_random_uuid();

            INSERT INTO journal_entries (
                id, tenant_id, entry_number, entry_date, description,
                source_type, source_id, status, created_at, updated_at
            ) VALUES (
                v_journal_id, v_tenant_id,
                'JE-ADJ-2511-' || LPAD((i)::TEXT, 4, '0'),
                v_adj_date, 'Stock Adjustment ADJ-2511-' || LPAD((i)::TEXT, 4, '0'),
                'stock_adjustment', v_adj_id, 'POSTED', v_adj_date, v_adj_date
            );

            IF v_adj_type IN ('increase', 'recount') THEN
                -- Increase: Dr. Persediaan, Cr. Penyesuaian
                INSERT INTO journal_lines (id, journal_id, account_id, debit, credit, description)
                VALUES (gen_random_uuid(), v_journal_id, v_inventory_account_id, v_total_value, 0, 'Penambahan persediaan');

                INSERT INTO journal_lines (id, journal_id, account_id, debit, credit, description)
                VALUES (gen_random_uuid(), v_journal_id, v_adjustment_account_id, 0, v_total_value, 'Penyesuaian persediaan');
            ELSE
                -- Decrease: Dr. Penyesuaian, Cr. Persediaan
                INSERT INTO journal_lines (id, journal_id, account_id, debit, credit, description)
                VALUES (gen_random_uuid(), v_journal_id, v_adjustment_account_id, v_total_value, 0, 'Penyesuaian persediaan');

                INSERT INTO journal_lines (id, journal_id, account_id, debit, credit, description)
                VALUES (gen_random_uuid(), v_journal_id, v_inventory_account_id, 0, v_total_value, 'Pengurangan persediaan');
            END IF;
        END IF;

        v_adj_count := v_adj_count + 1;
    END LOOP;

    -- DECEMBER 2025 - 15 Adjustments
    FOR i IN 1..15 LOOP
        v_adj_date := '2025-12-01'::DATE + (random() * 30)::INT;
        v_adj_id := gen_random_uuid();
        v_random_warehouse := v_warehouse_ids[1 + (random() * (array_length(v_warehouse_ids, 1) - 1))::INT];

        IF i <= 3 THEN v_status := 'draft';
        ELSIF i <= 13 THEN v_status := 'posted';
        ELSE v_status := 'void';
        END IF;

        IF i <= 5 THEN
            v_adj_type := 'increase';
            v_reason := 'Selisih penerimaan barang';
        ELSIF i <= 9 THEN
            v_adj_type := 'decrease';
            v_reason := 'Barang hilang/rusak';
        ELSE
            v_adj_type := 'recount';
            v_reason := 'Stock opname akhir tahun';
        END IF;

        v_total_value := 0;

        INSERT INTO stock_adjustments (
            id, tenant_id, adjustment_number, adjustment_date,
            warehouse_id, adjustment_type, reason, total_value,
            status, notes, created_at, updated_at
        ) VALUES (
            v_adj_id, v_tenant_id,
            'ADJ-2512-' || LPAD((i)::TEXT, 4, '0'),
            v_adj_date, v_random_warehouse, v_adj_type, v_reason, 0,
            v_status, 'Adjustment December ' || i,
            v_adj_date, v_adj_date
        );

        FOR j IN 1..(2 + (random() * 4)::INT) LOOP
            v_random_product := v_product_ids[1 + (random() * (array_length(v_product_ids, 1) - 1))::INT];
            v_qty := 1 + (random() * 25)::INT;

            SELECT purchase_price INTO v_cost FROM products WHERE id = v_random_product;
            v_cost := COALESCE(v_cost, 50000);

            INSERT INTO stock_adjustment_items (
                id, adjustment_id, product_id, quantity,
                unit_cost, line_value, notes
            )
            SELECT
                gen_random_uuid(), v_adj_id, v_random_product, v_qty,
                v_cost, v_qty * v_cost, p.nama_produk || ' - qty: ' || v_qty
            FROM products p WHERE p.id = v_random_product;

            v_total_value := v_total_value + (v_qty * v_cost);
        END LOOP;

        UPDATE stock_adjustments
        SET total_value = v_total_value
        WHERE id = v_adj_id;

        IF v_status = 'posted' AND v_inventory_account_id IS NOT NULL AND v_adjustment_account_id IS NOT NULL THEN
            v_journal_id := gen_random_uuid();

            INSERT INTO journal_entries (
                id, tenant_id, entry_number, entry_date, description,
                source_type, source_id, status, created_at, updated_at
            ) VALUES (
                v_journal_id, v_tenant_id,
                'JE-ADJ-2512-' || LPAD((i)::TEXT, 4, '0'),
                v_adj_date, 'Stock Adjustment ADJ-2512-' || LPAD((i)::TEXT, 4, '0'),
                'stock_adjustment', v_adj_id, 'POSTED', v_adj_date, v_adj_date
            );

            IF v_adj_type IN ('increase', 'recount') THEN
                INSERT INTO journal_lines (id, journal_id, account_id, debit, credit, description)
                VALUES (gen_random_uuid(), v_journal_id, v_inventory_account_id, v_total_value, 0, 'Penambahan persediaan');

                INSERT INTO journal_lines (id, journal_id, account_id, debit, credit, description)
                VALUES (gen_random_uuid(), v_journal_id, v_adjustment_account_id, 0, v_total_value, 'Penyesuaian persediaan');
            ELSE
                INSERT INTO journal_lines (id, journal_id, account_id, debit, credit, description)
                VALUES (gen_random_uuid(), v_journal_id, v_adjustment_account_id, v_total_value, 0, 'Penyesuaian persediaan');

                INSERT INTO journal_lines (id, journal_id, account_id, debit, credit, description)
                VALUES (gen_random_uuid(), v_journal_id, v_inventory_account_id, 0, v_total_value, 'Pengurangan persediaan');
            END IF;
        END IF;

        v_adj_count := v_adj_count + 1;
    END LOOP;

    -- JANUARY 2026 - 13 Adjustments
    FOR i IN 1..13 LOOP
        v_adj_date := '2026-01-01'::DATE + (random() * 15)::INT;
        v_adj_id := gen_random_uuid();
        v_random_warehouse := v_warehouse_ids[1 + (random() * (array_length(v_warehouse_ids, 1) - 1))::INT];

        IF i <= 3 THEN v_status := 'draft';
        ELSIF i <= 12 THEN v_status := 'posted';
        ELSE v_status := 'void';
        END IF;

        IF i <= 5 THEN
            v_adj_type := 'increase';
            v_reason := 'Koreksi stock awal tahun';
        ELSIF i <= 8 THEN
            v_adj_type := 'decrease';
            v_reason := 'Barang kadaluarsa';
        ELSE
            v_adj_type := 'recount';
            v_reason := 'Stock opname bulanan';
        END IF;

        v_total_value := 0;

        INSERT INTO stock_adjustments (
            id, tenant_id, adjustment_number, adjustment_date,
            warehouse_id, adjustment_type, reason, total_value,
            status, notes, created_at, updated_at
        ) VALUES (
            v_adj_id, v_tenant_id,
            'ADJ-2601-' || LPAD((i)::TEXT, 4, '0'),
            v_adj_date, v_random_warehouse, v_adj_type, v_reason, 0,
            v_status, 'Adjustment January ' || i,
            v_adj_date, v_adj_date
        );

        FOR j IN 1..(2 + (random() * 3)::INT) LOOP
            v_random_product := v_product_ids[1 + (random() * (array_length(v_product_ids, 1) - 1))::INT];
            v_qty := 1 + (random() * 20)::INT;

            SELECT purchase_price INTO v_cost FROM products WHERE id = v_random_product;
            v_cost := COALESCE(v_cost, 50000);

            INSERT INTO stock_adjustment_items (
                id, adjustment_id, product_id, quantity,
                unit_cost, line_value, notes
            )
            SELECT
                gen_random_uuid(), v_adj_id, v_random_product, v_qty,
                v_cost, v_qty * v_cost, p.nama_produk || ' - qty: ' || v_qty
            FROM products p WHERE p.id = v_random_product;

            v_total_value := v_total_value + (v_qty * v_cost);
        END LOOP;

        UPDATE stock_adjustments
        SET total_value = v_total_value
        WHERE id = v_adj_id;

        IF v_status = 'posted' AND v_inventory_account_id IS NOT NULL AND v_adjustment_account_id IS NOT NULL THEN
            v_journal_id := gen_random_uuid();

            INSERT INTO journal_entries (
                id, tenant_id, entry_number, entry_date, description,
                source_type, source_id, status, created_at, updated_at
            ) VALUES (
                v_journal_id, v_tenant_id,
                'JE-ADJ-2601-' || LPAD((i)::TEXT, 4, '0'),
                v_adj_date, 'Stock Adjustment ADJ-2601-' || LPAD((i)::TEXT, 4, '0'),
                'stock_adjustment', v_adj_id, 'POSTED', v_adj_date, v_adj_date
            );

            IF v_adj_type IN ('increase', 'recount') THEN
                INSERT INTO journal_lines (id, journal_id, account_id, debit, credit, description)
                VALUES (gen_random_uuid(), v_journal_id, v_inventory_account_id, v_total_value, 0, 'Penambahan persediaan');

                INSERT INTO journal_lines (id, journal_id, account_id, debit, credit, description)
                VALUES (gen_random_uuid(), v_journal_id, v_adjustment_account_id, 0, v_total_value, 'Penyesuaian persediaan');
            ELSE
                INSERT INTO journal_lines (id, journal_id, account_id, debit, credit, description)
                VALUES (gen_random_uuid(), v_journal_id, v_adjustment_account_id, v_total_value, 0, 'Penyesuaian persediaan');

                INSERT INTO journal_lines (id, journal_id, account_id, debit, credit, description)
                VALUES (gen_random_uuid(), v_journal_id, v_inventory_account_id, 0, v_total_value, 'Pengurangan persediaan');
            END IF;
        END IF;

        v_adj_count := v_adj_count + 1;
    END LOOP;

    RAISE NOTICE '========================================';
    RAISE NOTICE 'Stock Adjustments created: %', v_adj_count;
    RAISE NOTICE '========================================';
END $$;

-- Verify Stock Adjustments by status and type
SELECT status, adjustment_type, COUNT(*) as count, SUM(total_value) as total_value
FROM stock_adjustments
WHERE tenant_id = 'evlogia'
GROUP BY status, adjustment_type
ORDER BY status, adjustment_type;
