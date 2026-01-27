-- =============================================
-- EVLOGIA SEED: 17_stock_transfers.sql
-- Purpose: Create 20+ Stock Transfers between warehouses
-- Stock Transfers do NOT create journal entries (internal movement only)
-- =============================================

DO $$
DECLARE
    v_tenant_id TEXT := current_setting('seed.tenant_id', true);
    v_product_ids UUID[];
    v_warehouse_atput UUID;
    v_warehouse_4a UUID;
    v_transfer_id UUID;
    v_transfer_count INT := 0;
    v_random_product UUID;
    v_qty INT;
    v_cost BIGINT;
    v_total_value BIGINT;
    v_transfer_date DATE;
    v_status TEXT;
    v_from_warehouse UUID;
    v_to_warehouse UUID;
BEGIN
    IF v_tenant_id IS NULL OR v_tenant_id = '' THEN
        v_tenant_id := 'evlogia';
    END IF;

    RAISE NOTICE 'Creating stock transfers for tenant: %', v_tenant_id;

    -- Get warehouse IDs
    SELECT id INTO v_warehouse_atput FROM warehouses
    WHERE tenant_id = v_tenant_id AND code = 'WH-ATPUT';

    SELECT id INTO v_warehouse_4a FROM warehouses
    WHERE tenant_id = v_tenant_id AND code = 'WH-4A';

    -- Get product IDs (physical products only)
    SELECT array_agg(id) INTO v_product_ids
    FROM products
    WHERE tenant_id = v_tenant_id
    AND kode_produk NOT LIKE 'SVC-%';

    -- ==========================================
    -- Generate 20+ Stock Transfers
    -- Distribution: Nov (6), Dec (8), Jan (6)
    -- Status: draft(4), in_transit(3), received(10), cancelled(3)
    -- Direction: Atput→4A (15), 4A→Atput (5)
    -- ==========================================

    -- NOVEMBER 2025 - 6 Transfers
    FOR i IN 1..6 LOOP
        v_transfer_date := '2025-11-01'::DATE + (random() * 29)::INT;
        v_transfer_id := gen_random_uuid();

        IF i <= 1 THEN v_status := 'draft';
        ELSIF i <= 2 THEN v_status := 'in_transit';
        ELSIF i <= 5 THEN v_status := 'received';
        ELSE v_status := 'cancelled';
        END IF;

        -- Direction: mostly Atput to 4A (stock replenishment)
        IF i <= 5 THEN
            v_from_warehouse := v_warehouse_atput;
            v_to_warehouse := v_warehouse_4a;
        ELSE
            v_from_warehouse := v_warehouse_4a;
            v_to_warehouse := v_warehouse_atput;
        END IF;

        v_total_value := 0;

        INSERT INTO stock_transfers (
            id, tenant_id, transfer_number, transfer_date,
            from_warehouse_id, to_warehouse_id, total_value,
            status, notes, created_at, updated_at
        ) VALUES (
            v_transfer_id, v_tenant_id,
            'TRF-2511-' || LPAD((i)::TEXT, 4, '0'),
            v_transfer_date, v_from_warehouse, v_to_warehouse, 0,
            v_status, 'Transfer November ' || i || ' - ' ||
            CASE WHEN v_from_warehouse = v_warehouse_atput THEN 'Atput ke 4A' ELSE '4A ke Atput' END,
            v_transfer_date, v_transfer_date
        );

        -- Insert 3-6 line items
        FOR j IN 1..(3 + (random() * 3)::INT) LOOP
            v_random_product := v_product_ids[1 + (random() * (array_length(v_product_ids, 1) - 1))::INT];
            v_qty := 5 + (random() * 30)::INT;

            SELECT purchase_price INTO v_cost FROM products WHERE id = v_random_product;
            v_cost := COALESCE(v_cost, 50000);

            INSERT INTO stock_transfer_items (
                id, transfer_id, product_id, quantity,
                unit_cost, line_value, notes
            )
            SELECT
                gen_random_uuid(), v_transfer_id, v_random_product, v_qty,
                v_cost, v_qty * v_cost, 'Transfer ' || p.nama_produk
            FROM products p WHERE p.id = v_random_product;

            v_total_value := v_total_value + (v_qty * v_cost);
        END LOOP;

        UPDATE stock_transfers
        SET total_value = v_total_value
        WHERE id = v_transfer_id;

        v_transfer_count := v_transfer_count + 1;
    END LOOP;

    -- DECEMBER 2025 - 8 Transfers (busier month)
    FOR i IN 1..8 LOOP
        v_transfer_date := '2025-12-01'::DATE + (random() * 30)::INT;
        v_transfer_id := gen_random_uuid();

        IF i <= 2 THEN v_status := 'draft';
        ELSIF i <= 3 THEN v_status := 'in_transit';
        ELSIF i <= 7 THEN v_status := 'received';
        ELSE v_status := 'cancelled';
        END IF;

        IF i <= 6 THEN
            v_from_warehouse := v_warehouse_atput;
            v_to_warehouse := v_warehouse_4a;
        ELSE
            v_from_warehouse := v_warehouse_4a;
            v_to_warehouse := v_warehouse_atput;
        END IF;

        v_total_value := 0;

        INSERT INTO stock_transfers (
            id, tenant_id, transfer_number, transfer_date,
            from_warehouse_id, to_warehouse_id, total_value,
            status, notes, created_at, updated_at
        ) VALUES (
            v_transfer_id, v_tenant_id,
            'TRF-2512-' || LPAD((i)::TEXT, 4, '0'),
            v_transfer_date, v_from_warehouse, v_to_warehouse, 0,
            v_status, 'Transfer December ' || i || ' - Peak Season Stock',
            v_transfer_date, v_transfer_date
        );

        FOR j IN 1..(3 + (random() * 4)::INT) LOOP
            v_random_product := v_product_ids[1 + (random() * (array_length(v_product_ids, 1) - 1))::INT];
            v_qty := 10 + (random() * 40)::INT;  -- Higher qty for peak season

            SELECT purchase_price INTO v_cost FROM products WHERE id = v_random_product;
            v_cost := COALESCE(v_cost, 50000);

            INSERT INTO stock_transfer_items (
                id, transfer_id, product_id, quantity,
                unit_cost, line_value, notes
            )
            SELECT
                gen_random_uuid(), v_transfer_id, v_random_product, v_qty,
                v_cost, v_qty * v_cost, 'Transfer ' || p.nama_produk
            FROM products p WHERE p.id = v_random_product;

            v_total_value := v_total_value + (v_qty * v_cost);
        END LOOP;

        UPDATE stock_transfers
        SET total_value = v_total_value
        WHERE id = v_transfer_id;

        v_transfer_count := v_transfer_count + 1;
    END LOOP;

    -- JANUARY 2026 - 6 Transfers
    FOR i IN 1..6 LOOP
        v_transfer_date := '2026-01-01'::DATE + (random() * 15)::INT;
        v_transfer_id := gen_random_uuid();

        IF i <= 1 THEN v_status := 'draft';
        ELSIF i <= 2 THEN v_status := 'in_transit';
        ELSIF i <= 5 THEN v_status := 'received';
        ELSE v_status := 'cancelled';
        END IF;

        IF i <= 4 THEN
            v_from_warehouse := v_warehouse_atput;
            v_to_warehouse := v_warehouse_4a;
        ELSE
            v_from_warehouse := v_warehouse_4a;
            v_to_warehouse := v_warehouse_atput;
        END IF;

        v_total_value := 0;

        INSERT INTO stock_transfers (
            id, tenant_id, transfer_number, transfer_date,
            from_warehouse_id, to_warehouse_id, total_value,
            status, notes, created_at, updated_at
        ) VALUES (
            v_transfer_id, v_tenant_id,
            'TRF-2601-' || LPAD((i)::TEXT, 4, '0'),
            v_transfer_date, v_from_warehouse, v_to_warehouse, 0,
            v_status, 'Transfer January ' || i || ' - New Year Replenishment',
            v_transfer_date, v_transfer_date
        );

        FOR j IN 1..(3 + (random() * 3)::INT) LOOP
            v_random_product := v_product_ids[1 + (random() * (array_length(v_product_ids, 1) - 1))::INT];
            v_qty := 5 + (random() * 25)::INT;

            SELECT purchase_price INTO v_cost FROM products WHERE id = v_random_product;
            v_cost := COALESCE(v_cost, 50000);

            INSERT INTO stock_transfer_items (
                id, transfer_id, product_id, quantity,
                unit_cost, line_value, notes
            )
            SELECT
                gen_random_uuid(), v_transfer_id, v_random_product, v_qty,
                v_cost, v_qty * v_cost, 'Transfer ' || p.nama_produk
            FROM products p WHERE p.id = v_random_product;

            v_total_value := v_total_value + (v_qty * v_cost);
        END LOOP;

        UPDATE stock_transfers
        SET total_value = v_total_value
        WHERE id = v_transfer_id;

        v_transfer_count := v_transfer_count + 1;
    END LOOP;

    RAISE NOTICE '========================================';
    RAISE NOTICE 'Stock Transfers created: %', v_transfer_count;
    RAISE NOTICE '========================================';
END $$;

-- Verify Stock Transfers by status
SELECT
    st.status,
    COUNT(*) as count,
    SUM(st.total_value) as total_value,
    COUNT(CASE WHEN wf.code = 'WH-ATPUT' THEN 1 END) as from_atput,
    COUNT(CASE WHEN wf.code = 'WH-4A' THEN 1 END) as from_4a
FROM stock_transfers st
JOIN warehouses wf ON st.from_warehouse_id = wf.id
WHERE st.tenant_id = 'evlogia'
GROUP BY st.status
ORDER BY st.status;
