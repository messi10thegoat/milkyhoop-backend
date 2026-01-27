-- =============================================
-- EVLOGIA SEED: 13_sales_orders.sql
-- Purpose: Create 80+ Sales Orders with all status variants
-- Sales Orders do NOT create journal entries (pre-accounting)
-- =============================================

DO $$
DECLARE
    v_tenant_id TEXT := current_setting('seed.tenant_id', true);
    v_customer_ids UUID[];
    v_product_ids UUID[];
    v_warehouse_id UUID;
    v_so_id UUID;
    v_so_count INT := 0;
    v_random_customer UUID;
    v_random_product UUID;
    v_qty INT;
    v_price BIGINT;
    v_subtotal BIGINT;
    v_tax_amount BIGINT;
    v_total_amount BIGINT;
    v_so_date DATE;
    v_status TEXT;
BEGIN
    IF v_tenant_id IS NULL OR v_tenant_id = '' THEN
        v_tenant_id := 'evlogia';
    END IF;

    RAISE NOTICE 'Creating sales orders for tenant: %', v_tenant_id;

    -- Get customer IDs (exclude walk-in/cash customers for credit sales)
    SELECT array_agg(id) INTO v_customer_ids
    FROM customers
    WHERE tenant_id = v_tenant_id AND credit_limit > 0;

    -- Get product IDs (sellable products)
    SELECT array_agg(id) INTO v_product_ids
    FROM products
    WHERE tenant_id = v_tenant_id
    AND kode_produk NOT LIKE 'SVC-%';  -- Exclude services for SO

    -- Get main warehouse
    SELECT id INTO v_warehouse_id
    FROM warehouses
    WHERE tenant_id = v_tenant_id AND code = 'WH-ATPUT';

    -- ==========================================
    -- Generate 80+ Sales Orders
    -- Distribution: Nov (25), Dec (30), Jan (25)
    -- Status: draft(8), confirmed(15), partial_shipped(5), shipped(10),
    --         partial_invoiced(8), invoiced(15), completed(12), cancelled(7)
    -- ==========================================

    -- NOVEMBER 2025 - 25 Sales Orders
    FOR i IN 1..25 LOOP
        v_so_date := '2025-11-01'::DATE + (random() * 29)::INT;
        v_so_id := gen_random_uuid();
        v_random_customer := v_customer_ids[1 + (random() * (array_length(v_customer_ids, 1) - 1))::INT];

        IF i <= 3 THEN v_status := 'draft';
        ELSIF i <= 8 THEN v_status := 'confirmed';
        ELSIF i <= 10 THEN v_status := 'partial_shipped';
        ELSIF i <= 13 THEN v_status := 'shipped';
        ELSIF i <= 16 THEN v_status := 'partial_invoiced';
        ELSIF i <= 20 THEN v_status := 'invoiced';
        ELSIF i <= 23 THEN v_status := 'completed';
        ELSE v_status := 'cancelled';
        END IF;

        v_subtotal := 0;

        INSERT INTO sales_orders (
            id, tenant_id, so_number, so_date, expected_date,
            customer_id, warehouse_id, subtotal, discount_type, discount_value,
            discount_amount, tax_amount, total_amount, status, notes,
            created_at, updated_at
        ) VALUES (
            v_so_id, v_tenant_id,
            'SO-2511-' || LPAD((i)::TEXT, 4, '0'),
            v_so_date, v_so_date + 7,
            v_random_customer, v_warehouse_id, 0, 'percent', 0, 0, 0, 0, v_status,
            'Sales Order November ' || i,
            v_so_date, v_so_date
        );

        -- Insert 2-6 line items
        FOR j IN 1..(2 + (random() * 4)::INT) LOOP
            v_random_product := v_product_ids[1 + (random() * (array_length(v_product_ids, 1) - 1))::INT];
            v_qty := 5 + (random() * 30)::INT;

            SELECT sales_price INTO v_price FROM products WHERE id = v_random_product;
            v_price := COALESCE(v_price, 100000);

            INSERT INTO sales_order_items (
                id, sales_order_id, product_id, description,
                quantity, shipped_quantity, invoiced_quantity, unit, unit_price,
                discount_percent, tax_rate, tax_amount, line_total, sort_order
            )
            SELECT
                gen_random_uuid(), v_so_id, v_random_product, p.nama_produk,
                v_qty,
                CASE
                    WHEN v_status IN ('shipped', 'invoiced', 'completed') THEN v_qty
                    WHEN v_status = 'partial_shipped' THEN (v_qty * 0.5)::INT
                    ELSE 0
                END,
                CASE
                    WHEN v_status IN ('invoiced', 'completed') THEN v_qty
                    WHEN v_status = 'partial_invoiced' THEN (v_qty * 0.6)::INT
                    ELSE 0
                END,
                p.satuan, v_price, 0,
                11, (v_qty * v_price * 11 / 100), v_qty * v_price * 111 / 100, j
            FROM products p WHERE p.id = v_random_product;

            v_subtotal := v_subtotal + (v_qty * v_price);
        END LOOP;

        v_tax_amount := (v_subtotal * 11 / 100);
        v_total_amount := v_subtotal + v_tax_amount;

        UPDATE sales_orders
        SET subtotal = v_subtotal, tax_amount = v_tax_amount, total_amount = v_total_amount
        WHERE id = v_so_id;

        v_so_count := v_so_count + 1;
    END LOOP;

    -- DECEMBER 2025 - 30 Sales Orders
    FOR i IN 1..30 LOOP
        v_so_date := '2025-12-01'::DATE + (random() * 30)::INT;
        v_so_id := gen_random_uuid();
        v_random_customer := v_customer_ids[1 + (random() * (array_length(v_customer_ids, 1) - 1))::INT];

        IF i <= 3 THEN v_status := 'draft';
        ELSIF i <= 8 THEN v_status := 'confirmed';
        ELSIF i <= 10 THEN v_status := 'partial_shipped';
        ELSIF i <= 14 THEN v_status := 'shipped';
        ELSIF i <= 17 THEN v_status := 'partial_invoiced';
        ELSIF i <= 23 THEN v_status := 'invoiced';
        ELSIF i <= 27 THEN v_status := 'completed';
        ELSE v_status := 'cancelled';
        END IF;

        v_subtotal := 0;

        INSERT INTO sales_orders (
            id, tenant_id, so_number, so_date, expected_date,
            customer_id, warehouse_id, subtotal, discount_type, discount_value,
            discount_amount, tax_amount, total_amount, status, notes,
            created_at, updated_at
        ) VALUES (
            v_so_id, v_tenant_id,
            'SO-2512-' || LPAD((i)::TEXT, 4, '0'),
            v_so_date, v_so_date + 7,
            v_random_customer, v_warehouse_id, 0, 'percent', 0, 0, 0, 0, v_status,
            'Sales Order December ' || i,
            v_so_date, v_so_date
        );

        FOR j IN 1..(2 + (random() * 5)::INT) LOOP
            v_random_product := v_product_ids[1 + (random() * (array_length(v_product_ids, 1) - 1))::INT];
            v_qty := 5 + (random() * 40)::INT;

            SELECT sales_price INTO v_price FROM products WHERE id = v_random_product;
            v_price := COALESCE(v_price, 100000);

            INSERT INTO sales_order_items (
                id, sales_order_id, product_id, description,
                quantity, shipped_quantity, invoiced_quantity, unit, unit_price,
                discount_percent, tax_rate, tax_amount, line_total, sort_order
            )
            SELECT
                gen_random_uuid(), v_so_id, v_random_product, p.nama_produk,
                v_qty,
                CASE
                    WHEN v_status IN ('shipped', 'invoiced', 'completed') THEN v_qty
                    WHEN v_status = 'partial_shipped' THEN (v_qty * 0.5)::INT
                    ELSE 0
                END,
                CASE
                    WHEN v_status IN ('invoiced', 'completed') THEN v_qty
                    WHEN v_status = 'partial_invoiced' THEN (v_qty * 0.6)::INT
                    ELSE 0
                END,
                p.satuan, v_price, 0,
                11, (v_qty * v_price * 11 / 100), v_qty * v_price * 111 / 100, j
            FROM products p WHERE p.id = v_random_product;

            v_subtotal := v_subtotal + (v_qty * v_price);
        END LOOP;

        v_tax_amount := (v_subtotal * 11 / 100);
        v_total_amount := v_subtotal + v_tax_amount;

        UPDATE sales_orders
        SET subtotal = v_subtotal, tax_amount = v_tax_amount, total_amount = v_total_amount
        WHERE id = v_so_id;

        v_so_count := v_so_count + 1;
    END LOOP;

    -- JANUARY 2026 - 25 Sales Orders
    FOR i IN 1..25 LOOP
        v_so_date := '2026-01-01'::DATE + (random() * 15)::INT;
        v_so_id := gen_random_uuid();
        v_random_customer := v_customer_ids[1 + (random() * (array_length(v_customer_ids, 1) - 1))::INT];

        IF i <= 2 THEN v_status := 'draft';
        ELSIF i <= 7 THEN v_status := 'confirmed';
        ELSIF i <= 9 THEN v_status := 'partial_shipped';
        ELSIF i <= 12 THEN v_status := 'shipped';
        ELSIF i <= 15 THEN v_status := 'partial_invoiced';
        ELSIF i <= 20 THEN v_status := 'invoiced';
        ELSIF i <= 23 THEN v_status := 'completed';
        ELSE v_status := 'cancelled';
        END IF;

        v_subtotal := 0;

        INSERT INTO sales_orders (
            id, tenant_id, so_number, so_date, expected_date,
            customer_id, warehouse_id, subtotal, discount_type, discount_value,
            discount_amount, tax_amount, total_amount, status, notes,
            created_at, updated_at
        ) VALUES (
            v_so_id, v_tenant_id,
            'SO-2601-' || LPAD((i)::TEXT, 4, '0'),
            v_so_date, v_so_date + 7,
            v_random_customer, v_warehouse_id, 0, 'percent', 0, 0, 0, 0, v_status,
            'Sales Order January ' || i,
            v_so_date, v_so_date
        );

        FOR j IN 1..(2 + (random() * 4)::INT) LOOP
            v_random_product := v_product_ids[1 + (random() * (array_length(v_product_ids, 1) - 1))::INT];
            v_qty := 5 + (random() * 35)::INT;

            SELECT sales_price INTO v_price FROM products WHERE id = v_random_product;
            v_price := COALESCE(v_price, 100000);

            INSERT INTO sales_order_items (
                id, sales_order_id, product_id, description,
                quantity, shipped_quantity, invoiced_quantity, unit, unit_price,
                discount_percent, tax_rate, tax_amount, line_total, sort_order
            )
            SELECT
                gen_random_uuid(), v_so_id, v_random_product, p.nama_produk,
                v_qty,
                CASE
                    WHEN v_status IN ('shipped', 'invoiced', 'completed') THEN v_qty
                    WHEN v_status = 'partial_shipped' THEN (v_qty * 0.5)::INT
                    ELSE 0
                END,
                CASE
                    WHEN v_status IN ('invoiced', 'completed') THEN v_qty
                    WHEN v_status = 'partial_invoiced' THEN (v_qty * 0.6)::INT
                    ELSE 0
                END,
                p.satuan, v_price, 0,
                11, (v_qty * v_price * 11 / 100), v_qty * v_price * 111 / 100, j
            FROM products p WHERE p.id = v_random_product;

            v_subtotal := v_subtotal + (v_qty * v_price);
        END LOOP;

        v_tax_amount := (v_subtotal * 11 / 100);
        v_total_amount := v_subtotal + v_tax_amount;

        UPDATE sales_orders
        SET subtotal = v_subtotal, tax_amount = v_tax_amount, total_amount = v_total_amount
        WHERE id = v_so_id;

        v_so_count := v_so_count + 1;
    END LOOP;

    RAISE NOTICE '========================================';
    RAISE NOTICE 'Sales Orders created: %', v_so_count;
    RAISE NOTICE '========================================';
END $$;

-- Verify Sales Orders by status
SELECT status, COUNT(*) as count, SUM(total_amount) as total_value
FROM sales_orders
WHERE tenant_id = 'evlogia'
GROUP BY status
ORDER BY status;
