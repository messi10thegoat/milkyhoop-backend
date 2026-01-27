-- =============================================
-- EVLOGIA SEED: 09_purchase_orders.sql
-- Purpose: Create 100+ Purchase Orders with all status variants
-- Timeline: Nov 2025 - Jan 2026
-- =============================================

DO $$
DECLARE
    v_tenant_id TEXT := current_setting('seed.tenant_id', true);
    v_vendor_ids UUID[];
    v_product_ids UUID[];
    v_wh_atput_id UUID;
    v_po_id UUID;
    v_line_num INT;
    v_po_count INT := 0;
    v_random_vendor UUID;
    v_random_product UUID;
    v_qty INT;
    v_price BIGINT;
    v_subtotal BIGINT;
    v_tax_amount BIGINT;
    v_total_amount BIGINT;
    v_po_date DATE;
    v_status TEXT;
    v_month INT;
    v_day INT;
BEGIN
    IF v_tenant_id IS NULL OR v_tenant_id = '' THEN
        v_tenant_id := 'evlogia';
    END IF;

    RAISE NOTICE 'Creating purchase orders for tenant: %', v_tenant_id;

    -- Get warehouse ID
    SELECT id INTO v_wh_atput_id FROM warehouses WHERE tenant_id = v_tenant_id AND code = 'WH-ATPUT';

    -- Get vendor IDs (array)
    SELECT array_agg(id) INTO v_vendor_ids FROM vendors WHERE tenant_id = v_tenant_id;

    -- Get product IDs for inventory items only
    SELECT array_agg(id) INTO v_product_ids
    FROM products
    WHERE tenant_id = v_tenant_id AND item_type = 'goods';

    -- ==========================================
    -- Generate 100+ Purchase Orders
    -- Distribution: Nov (30), Dec (40), Jan (30)
    -- Status: draft(10), sent(20), partial_received(15), received(30), partial_billed(10), billed(10), closed(0), cancelled(5)
    -- ==========================================

    -- NOVEMBER 2025 - 30 POs
    FOR i IN 1..30 LOOP
        v_po_date := '2025-11-01'::DATE + (random() * 29)::INT;
        v_po_id := gen_random_uuid();
        v_random_vendor := v_vendor_ids[1 + (random() * (array_length(v_vendor_ids, 1) - 1))::INT];

        -- Determine status based on index
        IF i <= 3 THEN v_status := 'draft';
        ELSIF i <= 9 THEN v_status := 'sent';
        ELSIF i <= 14 THEN v_status := 'partial_received';
        ELSIF i <= 24 THEN v_status := 'received';
        ELSIF i <= 27 THEN v_status := 'partial_billed';
        ELSIF i <= 29 THEN v_status := 'billed';
        ELSE v_status := 'cancelled';
        END IF;

        -- Calculate amounts (will be updated after line items)
        v_subtotal := 0;

        -- Insert PO header
        INSERT INTO purchase_orders (
            id, tenant_id, order_number, order_date, expected_date,
            vendor_id, warehouse_id, subtotal, discount_amount,
            tax_amount, total_amount, status, notes,
            created_at, updated_at
        ) VALUES (
            v_po_id, v_tenant_id,
            'PO-2511-' || LPAD((i)::TEXT, 4, '0'),
            v_po_date, v_po_date + 7,
            v_random_vendor, v_wh_atput_id, 0, 0, 0, 0, v_status,
            'Purchase Order November ' || i,
            v_po_date, v_po_date
        );

        -- Insert 2-5 line items
        v_line_num := 0;
        FOR j IN 1..(2 + (random() * 3)::INT) LOOP
            v_line_num := v_line_num + 1;
            v_random_product := v_product_ids[1 + (random() * (array_length(v_product_ids, 1) - 1))::INT];
            v_qty := 10 + (random() * 90)::INT;

            SELECT purchase_price INTO v_price FROM products WHERE id = v_random_product;
            v_price := COALESCE(v_price, 50000);

            INSERT INTO purchase_order_items (
                id, purchase_order_id, item_id, description,
                quantity, quantity_received, quantity_billed,
                unit, unit_price, discount_percent, tax_rate,
                subtotal, line_number
            )
            SELECT
                gen_random_uuid(), v_po_id, v_random_product, p.nama_produk,
                v_qty,
                CASE WHEN v_status IN ('partial_received', 'received', 'partial_billed', 'billed') THEN (v_qty * 0.6)::INT ELSE 0 END,
                CASE WHEN v_status IN ('partial_billed', 'billed') THEN (v_qty * 0.5)::INT ELSE 0 END,
                p.satuan, v_price, 0, 11,
                v_qty * v_price, v_line_num
            FROM products p WHERE p.id = v_random_product;

            v_subtotal := v_subtotal + (v_qty * v_price);
        END LOOP;

        -- Update PO totals
        v_tax_amount := (v_subtotal * 11 / 100);
        v_total_amount := v_subtotal + v_tax_amount;

        UPDATE purchase_orders
        SET subtotal = v_subtotal, tax_amount = v_tax_amount, total_amount = v_total_amount
        WHERE id = v_po_id;

        v_po_count := v_po_count + 1;
    END LOOP;

    -- DECEMBER 2025 - 40 POs
    FOR i IN 1..40 LOOP
        v_po_date := '2025-12-01'::DATE + (random() * 30)::INT;
        v_po_id := gen_random_uuid();
        v_random_vendor := v_vendor_ids[1 + (random() * (array_length(v_vendor_ids, 1) - 1))::INT];

        IF i <= 4 THEN v_status := 'draft';
        ELSIF i <= 12 THEN v_status := 'sent';
        ELSIF i <= 18 THEN v_status := 'partial_received';
        ELSIF i <= 30 THEN v_status := 'received';
        ELSIF i <= 35 THEN v_status := 'partial_billed';
        ELSIF i <= 38 THEN v_status := 'billed';
        ELSE v_status := 'cancelled';
        END IF;

        v_subtotal := 0;

        INSERT INTO purchase_orders (
            id, tenant_id, order_number, order_date, expected_date,
            vendor_id, warehouse_id, subtotal, discount_amount,
            tax_amount, total_amount, status, notes,
            created_at, updated_at
        ) VALUES (
            v_po_id, v_tenant_id,
            'PO-2512-' || LPAD((i)::TEXT, 4, '0'),
            v_po_date, v_po_date + 7,
            v_random_vendor, v_wh_atput_id, 0, 0, 0, 0, v_status,
            'Purchase Order December ' || i,
            v_po_date, v_po_date
        );

        FOR j IN 1..(2 + (random() * 4)::INT) LOOP
            v_line_num := j;
            v_random_product := v_product_ids[1 + (random() * (array_length(v_product_ids, 1) - 1))::INT];
            v_qty := 15 + (random() * 85)::INT;

            SELECT purchase_price INTO v_price FROM products WHERE id = v_random_product;
            v_price := COALESCE(v_price, 50000);

            INSERT INTO purchase_order_items (
                id, purchase_order_id, item_id, description,
                quantity, quantity_received, quantity_billed,
                unit, unit_price, discount_percent, tax_rate,
                subtotal, line_number
            )
            SELECT
                gen_random_uuid(), v_po_id, v_random_product, p.nama_produk,
                v_qty,
                CASE WHEN v_status IN ('partial_received', 'received', 'partial_billed', 'billed') THEN (v_qty * 0.7)::INT ELSE 0 END,
                CASE WHEN v_status IN ('partial_billed', 'billed') THEN (v_qty * 0.6)::INT ELSE 0 END,
                p.satuan, v_price, 0, 11,
                v_qty * v_price, v_line_num
            FROM products p WHERE p.id = v_random_product;

            v_subtotal := v_subtotal + (v_qty * v_price);
        END LOOP;

        v_tax_amount := (v_subtotal * 11 / 100);
        v_total_amount := v_subtotal + v_tax_amount;

        UPDATE purchase_orders
        SET subtotal = v_subtotal, tax_amount = v_tax_amount, total_amount = v_total_amount
        WHERE id = v_po_id;

        v_po_count := v_po_count + 1;
    END LOOP;

    -- JANUARY 2026 - 30 POs (more recent, more drafts/sent)
    FOR i IN 1..30 LOOP
        v_po_date := '2026-01-01'::DATE + (random() * 15)::INT;
        v_po_id := gen_random_uuid();
        v_random_vendor := v_vendor_ids[1 + (random() * (array_length(v_vendor_ids, 1) - 1))::INT];

        IF i <= 5 THEN v_status := 'draft';
        ELSIF i <= 15 THEN v_status := 'sent';
        ELSIF i <= 20 THEN v_status := 'partial_received';
        ELSIF i <= 25 THEN v_status := 'received';
        ELSIF i <= 28 THEN v_status := 'partial_billed';
        ELSE v_status := 'billed';
        END IF;

        v_subtotal := 0;

        INSERT INTO purchase_orders (
            id, tenant_id, order_number, order_date, expected_date,
            vendor_id, warehouse_id, subtotal, discount_amount,
            tax_amount, total_amount, status, notes,
            created_at, updated_at
        ) VALUES (
            v_po_id, v_tenant_id,
            'PO-2601-' || LPAD((i)::TEXT, 4, '0'),
            v_po_date, v_po_date + 7,
            v_random_vendor, v_wh_atput_id, 0, 0, 0, 0, v_status,
            'Purchase Order January ' || i,
            v_po_date, v_po_date
        );

        FOR j IN 1..(2 + (random() * 3)::INT) LOOP
            v_line_num := j;
            v_random_product := v_product_ids[1 + (random() * (array_length(v_product_ids, 1) - 1))::INT];
            v_qty := 10 + (random() * 50)::INT;

            SELECT purchase_price INTO v_price FROM products WHERE id = v_random_product;
            v_price := COALESCE(v_price, 50000);

            INSERT INTO purchase_order_items (
                id, purchase_order_id, item_id, description,
                quantity, quantity_received, quantity_billed,
                unit, unit_price, discount_percent, tax_rate,
                subtotal, line_number
            )
            SELECT
                gen_random_uuid(), v_po_id, v_random_product, p.nama_produk,
                v_qty,
                CASE WHEN v_status IN ('partial_received', 'received', 'partial_billed', 'billed') THEN (v_qty * 0.5)::INT ELSE 0 END,
                CASE WHEN v_status IN ('partial_billed', 'billed') THEN (v_qty * 0.4)::INT ELSE 0 END,
                p.satuan, v_price, 0, 11,
                v_qty * v_price, v_line_num
            FROM products p WHERE p.id = v_random_product;

            v_subtotal := v_subtotal + (v_qty * v_price);
        END LOOP;

        v_tax_amount := (v_subtotal * 11 / 100);
        v_total_amount := v_subtotal + v_tax_amount;

        UPDATE purchase_orders
        SET subtotal = v_subtotal, tax_amount = v_tax_amount, total_amount = v_total_amount
        WHERE id = v_po_id;

        v_po_count := v_po_count + 1;
    END LOOP;

    RAISE NOTICE '========================================';
    RAISE NOTICE 'Purchase Orders created: %', v_po_count;
    RAISE NOTICE '========================================';
END $$;

-- Verify PO counts by status
SELECT status, COUNT(*) as count, SUM(total_amount) as total_value
FROM purchase_orders
WHERE tenant_id = 'evlogia'
GROUP BY status
ORDER BY status;

-- Verify by month
SELECT
    TO_CHAR(order_date, 'YYYY-MM') as month,
    COUNT(*) as po_count,
    SUM(total_amount) as total_value
FROM purchase_orders
WHERE tenant_id = 'evlogia'
GROUP BY TO_CHAR(order_date, 'YYYY-MM')
ORDER BY month;
