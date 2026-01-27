-- =============================================
-- EVLOGIA SEED: 12_quotes.sql
-- Purpose: Create 50+ Quotes with all status variants
-- Quotes do NOT create journal entries (pre-accounting)
-- =============================================

DO $$
DECLARE
    v_tenant_id TEXT := current_setting('seed.tenant_id', true);
    v_customer_ids UUID[];
    v_product_ids UUID[];
    v_quote_id UUID;
    v_quote_count INT := 0;
    v_random_customer UUID;
    v_random_product UUID;
    v_qty INT;
    v_price BIGINT;
    v_subtotal BIGINT;
    v_tax_amount BIGINT;
    v_total_amount BIGINT;
    v_quote_date DATE;
    v_status TEXT;
BEGIN
    IF v_tenant_id IS NULL OR v_tenant_id = '' THEN
        v_tenant_id := 'evlogia';
    END IF;

    RAISE NOTICE 'Creating quotes for tenant: %', v_tenant_id;

    -- Get customer IDs (exclude walk-in/cash customers)
    SELECT array_agg(id) INTO v_customer_ids
    FROM customers
    WHERE tenant_id = v_tenant_id AND credit_limit > 0;

    -- Get product IDs
    SELECT array_agg(id) INTO v_product_ids
    FROM products
    WHERE tenant_id = v_tenant_id;

    -- ==========================================
    -- Generate 50+ Quotes
    -- Distribution: Nov (15), Dec (20), Jan (15)
    -- Status: draft(10), sent(15), viewed(5), accepted(10), declined(5), expired(3), converted(2)
    -- ==========================================

    -- NOVEMBER 2025 - 15 Quotes
    FOR i IN 1..15 LOOP
        v_quote_date := '2025-11-01'::DATE + (random() * 29)::INT;
        v_quote_id := gen_random_uuid();
        v_random_customer := v_customer_ids[1 + (random() * (array_length(v_customer_ids, 1) - 1))::INT];

        IF i <= 3 THEN v_status := 'draft';
        ELSIF i <= 8 THEN v_status := 'sent';
        ELSIF i <= 9 THEN v_status := 'viewed';
        ELSIF i <= 12 THEN v_status := 'accepted';
        ELSIF i <= 14 THEN v_status := 'declined';
        ELSE v_status := 'expired';
        END IF;

        v_subtotal := 0;

        INSERT INTO quotes (
            id, tenant_id, quote_number, quote_date, expiry_date,
            customer_id, subtotal, discount_type, discount_value, discount_amount,
            tax_amount, total_amount, status, notes,
            created_at, updated_at
        ) VALUES (
            v_quote_id, v_tenant_id,
            'QUO-2511-' || LPAD((i)::TEXT, 4, '0'),
            v_quote_date, v_quote_date + 14,
            v_random_customer, 0, 'percent', 0, 0, 0, 0, v_status,
            'Quote November ' || i,
            v_quote_date, v_quote_date
        );

        -- Insert 2-5 line items
        FOR j IN 1..(2 + (random() * 3)::INT) LOOP
            v_random_product := v_product_ids[1 + (random() * (array_length(v_product_ids, 1) - 1))::INT];
            v_qty := 5 + (random() * 50)::INT;

            SELECT sales_price INTO v_price FROM products WHERE id = v_random_product;
            v_price := COALESCE(v_price, 100000);

            INSERT INTO quote_items (
                id, quote_id, item_id, description,
                quantity, unit, unit_price, discount_percent,
                tax_rate, tax_amount, line_total, sort_order
            )
            SELECT
                gen_random_uuid(), v_quote_id, v_random_product, p.nama_produk,
                v_qty, p.satuan, v_price, 0,
                11, (v_qty * v_price * 11 / 100), v_qty * v_price * 111 / 100, j
            FROM products p WHERE p.id = v_random_product;

            v_subtotal := v_subtotal + (v_qty * v_price);
        END LOOP;

        v_tax_amount := (v_subtotal * 11 / 100);
        v_total_amount := v_subtotal + v_tax_amount;

        UPDATE quotes
        SET subtotal = v_subtotal, tax_amount = v_tax_amount, total_amount = v_total_amount
        WHERE id = v_quote_id;

        v_quote_count := v_quote_count + 1;
    END LOOP;

    -- DECEMBER 2025 - 20 Quotes
    FOR i IN 1..20 LOOP
        v_quote_date := '2025-12-01'::DATE + (random() * 30)::INT;
        v_quote_id := gen_random_uuid();
        v_random_customer := v_customer_ids[1 + (random() * (array_length(v_customer_ids, 1) - 1))::INT];

        IF i <= 4 THEN v_status := 'draft';
        ELSIF i <= 10 THEN v_status := 'sent';
        ELSIF i <= 12 THEN v_status := 'viewed';
        ELSIF i <= 16 THEN v_status := 'accepted';
        ELSIF i <= 18 THEN v_status := 'declined';
        ELSIF i <= 19 THEN v_status := 'expired';
        ELSE v_status := 'converted';
        END IF;

        v_subtotal := 0;

        INSERT INTO quotes (
            id, tenant_id, quote_number, quote_date, expiry_date,
            customer_id, subtotal, discount_type, discount_value, discount_amount,
            tax_amount, total_amount, status, notes,
            created_at, updated_at
        ) VALUES (
            v_quote_id, v_tenant_id,
            'QUO-2512-' || LPAD((i)::TEXT, 4, '0'),
            v_quote_date, v_quote_date + 14,
            v_random_customer, 0, 'percent', 0, 0, 0, 0, v_status,
            'Quote December ' || i,
            v_quote_date, v_quote_date
        );

        FOR j IN 1..(2 + (random() * 4)::INT) LOOP
            v_random_product := v_product_ids[1 + (random() * (array_length(v_product_ids, 1) - 1))::INT];
            v_qty := 5 + (random() * 60)::INT;

            SELECT sales_price INTO v_price FROM products WHERE id = v_random_product;
            v_price := COALESCE(v_price, 100000);

            INSERT INTO quote_items (
                id, quote_id, item_id, description,
                quantity, unit, unit_price, discount_percent,
                tax_rate, tax_amount, line_total, sort_order
            )
            SELECT
                gen_random_uuid(), v_quote_id, v_random_product, p.nama_produk,
                v_qty, p.satuan, v_price, 0,
                11, (v_qty * v_price * 11 / 100), v_qty * v_price * 111 / 100, j
            FROM products p WHERE p.id = v_random_product;

            v_subtotal := v_subtotal + (v_qty * v_price);
        END LOOP;

        v_tax_amount := (v_subtotal * 11 / 100);
        v_total_amount := v_subtotal + v_tax_amount;

        UPDATE quotes
        SET subtotal = v_subtotal, tax_amount = v_tax_amount, total_amount = v_total_amount
        WHERE id = v_quote_id;

        v_quote_count := v_quote_count + 1;
    END LOOP;

    -- JANUARY 2026 - 15 Quotes
    FOR i IN 1..15 LOOP
        v_quote_date := '2026-01-01'::DATE + (random() * 15)::INT;
        v_quote_id := gen_random_uuid();
        v_random_customer := v_customer_ids[1 + (random() * (array_length(v_customer_ids, 1) - 1))::INT];

        IF i <= 4 THEN v_status := 'draft';
        ELSIF i <= 9 THEN v_status := 'sent';
        ELSIF i <= 10 THEN v_status := 'viewed';
        ELSIF i <= 13 THEN v_status := 'accepted';
        ELSIF i <= 14 THEN v_status := 'declined';
        ELSE v_status := 'converted';
        END IF;

        v_subtotal := 0;

        INSERT INTO quotes (
            id, tenant_id, quote_number, quote_date, expiry_date,
            customer_id, subtotal, discount_type, discount_value, discount_amount,
            tax_amount, total_amount, status, notes,
            created_at, updated_at
        ) VALUES (
            v_quote_id, v_tenant_id,
            'QUO-2601-' || LPAD((i)::TEXT, 4, '0'),
            v_quote_date, v_quote_date + 14,
            v_random_customer, 0, 'percent', 0, 0, 0, 0, v_status,
            'Quote January ' || i,
            v_quote_date, v_quote_date
        );

        FOR j IN 1..(2 + (random() * 3)::INT) LOOP
            v_random_product := v_product_ids[1 + (random() * (array_length(v_product_ids, 1) - 1))::INT];
            v_qty := 5 + (random() * 40)::INT;

            SELECT sales_price INTO v_price FROM products WHERE id = v_random_product;
            v_price := COALESCE(v_price, 100000);

            INSERT INTO quote_items (
                id, quote_id, item_id, description,
                quantity, unit, unit_price, discount_percent,
                tax_rate, tax_amount, line_total, sort_order
            )
            SELECT
                gen_random_uuid(), v_quote_id, v_random_product, p.nama_produk,
                v_qty, p.satuan, v_price, 0,
                11, (v_qty * v_price * 11 / 100), v_qty * v_price * 111 / 100, j
            FROM products p WHERE p.id = v_random_product;

            v_subtotal := v_subtotal + (v_qty * v_price);
        END LOOP;

        v_tax_amount := (v_subtotal * 11 / 100);
        v_total_amount := v_subtotal + v_tax_amount;

        UPDATE quotes
        SET subtotal = v_subtotal, tax_amount = v_tax_amount, total_amount = v_total_amount
        WHERE id = v_quote_id;

        v_quote_count := v_quote_count + 1;
    END LOOP;

    RAISE NOTICE '========================================';
    RAISE NOTICE 'Quotes created: %', v_quote_count;
    RAISE NOTICE '========================================';
END $$;

-- Verify Quotes by status
SELECT status, COUNT(*) as count, SUM(total_amount) as total_value
FROM quotes
WHERE tenant_id = 'evlogia'
GROUP BY status
ORDER BY status;
