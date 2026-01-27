-- =============================================
-- EVLOGIA SEED: 18_credit_notes.sql
-- Purpose: Create 15+ Credit Notes (Sales Returns) with journal entries
-- Posted credit notes CREATE journal entries:
--   Dr. Retur Penjualan (4-10200)
--   Dr. PPN Keluaran (2-10400)
--   Cr. Piutang Usaha (1-10300)
-- =============================================

DO $$
DECLARE
    v_tenant_id TEXT := current_setting('seed.tenant_id', true);
    v_customer_ids UUID[];
    v_product_ids UUID[];
    v_invoice_ids UUID[];
    v_cn_id UUID;
    v_cn_count INT := 0;
    v_random_customer UUID;
    v_random_product UUID;
    v_random_invoice UUID;
    v_qty INT;
    v_price BIGINT;
    v_subtotal BIGINT;
    v_tax_amount BIGINT;
    v_total_amount BIGINT;
    v_cn_date DATE;
    v_status TEXT;
    v_amount_applied BIGINT;
    -- Journal Entry
    v_journal_id UUID;
    v_ar_account_id UUID;
    v_sales_return_account_id UUID;
    v_ppn_out_account_id UUID;
BEGIN
    IF v_tenant_id IS NULL OR v_tenant_id = '' THEN
        v_tenant_id := 'evlogia';
    END IF;

    RAISE NOTICE 'Creating credit notes for tenant: %', v_tenant_id;

    -- Get account IDs for journal entries
    SELECT id INTO v_ar_account_id FROM chart_of_accounts
    WHERE tenant_id = v_tenant_id AND account_code = '1-10300';

    SELECT id INTO v_sales_return_account_id FROM chart_of_accounts
    WHERE tenant_id = v_tenant_id AND account_code = '4-10200';

    SELECT id INTO v_ppn_out_account_id FROM chart_of_accounts
    WHERE tenant_id = v_tenant_id AND account_code = '2-10400';

    -- Get customer IDs
    SELECT array_agg(id) INTO v_customer_ids
    FROM customers
    WHERE tenant_id = v_tenant_id AND credit_limit > 0;

    -- Get product IDs
    SELECT array_agg(id) INTO v_product_ids
    FROM products
    WHERE tenant_id = v_tenant_id
    AND kode_produk NOT LIKE 'SVC-%';

    -- Get posted invoice IDs for reference
    SELECT array_agg(id) INTO v_invoice_ids
    FROM sales_invoices
    WHERE tenant_id = v_tenant_id
    AND status IN ('posted', 'partial', 'paid');

    -- ==========================================
    -- Generate 15+ Credit Notes
    -- Distribution: Nov (4), Dec (6), Jan (5)
    -- Status: draft(3), posted(5), partial(3), applied(3), void(1)
    -- ==========================================

    -- NOVEMBER 2025 - 4 Credit Notes
    FOR i IN 1..4 LOOP
        v_cn_date := '2025-11-05'::DATE + (random() * 25)::INT;
        v_cn_id := gen_random_uuid();
        v_random_customer := v_customer_ids[1 + (random() * (array_length(v_customer_ids, 1) - 1))::INT];

        IF v_invoice_ids IS NOT NULL AND array_length(v_invoice_ids, 1) > 0 THEN
            v_random_invoice := v_invoice_ids[1 + (random() * (array_length(v_invoice_ids, 1) - 1))::INT];
        ELSE
            v_random_invoice := NULL;
        END IF;

        IF i <= 1 THEN v_status := 'draft';
        ELSIF i <= 2 THEN v_status := 'posted';
        ELSIF i <= 3 THEN v_status := 'partial';
        ELSE v_status := 'applied';
        END IF;

        v_subtotal := 0;

        INSERT INTO credit_notes (
            id, tenant_id, credit_note_number, credit_note_date,
            customer_id, invoice_id, subtotal, discount_type, discount_value,
            discount_amount, tax_amount, total_amount, amount_applied, status,
            reason, notes, created_at, updated_at
        ) VALUES (
            v_cn_id, v_tenant_id,
            'CN-2511-' || LPAD((i)::TEXT, 4, '0'),
            v_cn_date, v_random_customer, v_random_invoice, 0, 'percent', 0, 0, 0, 0, 0,
            v_status, 'Barang tidak sesuai pesanan',
            'Credit Note November ' || i,
            v_cn_date, v_cn_date
        );

        -- Insert 1-3 line items
        FOR j IN 1..(1 + (random() * 2)::INT) LOOP
            v_random_product := v_product_ids[1 + (random() * (array_length(v_product_ids, 1) - 1))::INT];
            v_qty := 1 + (random() * 10)::INT;

            SELECT sales_price INTO v_price FROM products WHERE id = v_random_product;
            v_price := COALESCE(v_price, 100000);

            INSERT INTO credit_note_items (
                id, credit_note_id, product_id, description,
                quantity, unit, unit_price, discount_percent,
                tax_rate, tax_amount, line_total, sort_order
            )
            SELECT
                gen_random_uuid(), v_cn_id, v_random_product, p.nama_produk,
                v_qty, p.satuan, v_price, 0,
                11, (v_qty * v_price * 11 / 100), v_qty * v_price * 111 / 100, j
            FROM products p WHERE p.id = v_random_product;

            v_subtotal := v_subtotal + (v_qty * v_price);
        END LOOP;

        v_tax_amount := (v_subtotal * 11 / 100);
        v_total_amount := v_subtotal + v_tax_amount;

        -- Calculate amount_applied based on status
        IF v_status = 'applied' THEN
            v_amount_applied := v_total_amount;
        ELSIF v_status = 'partial' THEN
            v_amount_applied := (v_total_amount * (40 + random() * 40)::INT / 100);
        ELSE
            v_amount_applied := 0;
        END IF;

        UPDATE credit_notes
        SET subtotal = v_subtotal, tax_amount = v_tax_amount,
            total_amount = v_total_amount, amount_applied = v_amount_applied
        WHERE id = v_cn_id;

        -- Create journal entry for posted/partial/applied credit notes
        IF v_status IN ('posted', 'partial', 'applied') AND v_ar_account_id IS NOT NULL THEN
            v_journal_id := gen_random_uuid();

            INSERT INTO journal_entries (
                id, tenant_id, entry_number, entry_date, description,
                source_type, source_id, status, created_at, updated_at
            ) VALUES (
                v_journal_id, v_tenant_id,
                'JE-CN-2511-' || LPAD((i)::TEXT, 4, '0'),
                v_cn_date, 'Credit Note CN-2511-' || LPAD((i)::TEXT, 4, '0'),
                'credit_note', v_cn_id, 'POSTED', v_cn_date, v_cn_date
            );

            -- Dr. Retur Penjualan
            INSERT INTO journal_lines (id, journal_id, account_id, debit, credit, description)
            VALUES (gen_random_uuid(), v_journal_id, v_sales_return_account_id, v_subtotal, 0, 'Retur penjualan');

            -- Dr. PPN Keluaran
            INSERT INTO journal_lines (id, journal_id, account_id, debit, credit, description)
            VALUES (gen_random_uuid(), v_journal_id, v_ppn_out_account_id, v_tax_amount, 0, 'Koreksi PPN Keluaran');

            -- Cr. Piutang Usaha
            INSERT INTO journal_lines (id, journal_id, account_id, debit, credit, description)
            VALUES (gen_random_uuid(), v_journal_id, v_ar_account_id, 0, v_total_amount, 'Pengurangan piutang');
        END IF;

        v_cn_count := v_cn_count + 1;
    END LOOP;

    -- DECEMBER 2025 - 6 Credit Notes
    FOR i IN 1..6 LOOP
        v_cn_date := '2025-12-05'::DATE + (random() * 25)::INT;
        v_cn_id := gen_random_uuid();
        v_random_customer := v_customer_ids[1 + (random() * (array_length(v_customer_ids, 1) - 1))::INT];

        IF v_invoice_ids IS NOT NULL AND array_length(v_invoice_ids, 1) > 0 THEN
            v_random_invoice := v_invoice_ids[1 + (random() * (array_length(v_invoice_ids, 1) - 1))::INT];
        ELSE
            v_random_invoice := NULL;
        END IF;

        IF i <= 1 THEN v_status := 'draft';
        ELSIF i <= 3 THEN v_status := 'posted';
        ELSIF i <= 4 THEN v_status := 'partial';
        ELSIF i <= 5 THEN v_status := 'applied';
        ELSE v_status := 'void';
        END IF;

        v_subtotal := 0;

        INSERT INTO credit_notes (
            id, tenant_id, credit_note_number, credit_note_date,
            customer_id, invoice_id, subtotal, discount_type, discount_value,
            discount_amount, tax_amount, total_amount, amount_applied, status,
            reason, notes, created_at, updated_at
        ) VALUES (
            v_cn_id, v_tenant_id,
            'CN-2512-' || LPAD((i)::TEXT, 4, '0'),
            v_cn_date, v_random_customer, v_random_invoice, 0, 'percent', 0, 0, 0, 0, 0,
            v_status, 'Barang rusak/cacat',
            'Credit Note December ' || i,
            v_cn_date, v_cn_date
        );

        FOR j IN 1..(1 + (random() * 2)::INT) LOOP
            v_random_product := v_product_ids[1 + (random() * (array_length(v_product_ids, 1) - 1))::INT];
            v_qty := 1 + (random() * 15)::INT;

            SELECT sales_price INTO v_price FROM products WHERE id = v_random_product;
            v_price := COALESCE(v_price, 100000);

            INSERT INTO credit_note_items (
                id, credit_note_id, product_id, description,
                quantity, unit, unit_price, discount_percent,
                tax_rate, tax_amount, line_total, sort_order
            )
            SELECT
                gen_random_uuid(), v_cn_id, v_random_product, p.nama_produk,
                v_qty, p.satuan, v_price, 0,
                11, (v_qty * v_price * 11 / 100), v_qty * v_price * 111 / 100, j
            FROM products p WHERE p.id = v_random_product;

            v_subtotal := v_subtotal + (v_qty * v_price);
        END LOOP;

        v_tax_amount := (v_subtotal * 11 / 100);
        v_total_amount := v_subtotal + v_tax_amount;

        IF v_status = 'applied' THEN
            v_amount_applied := v_total_amount;
        ELSIF v_status = 'partial' THEN
            v_amount_applied := (v_total_amount * (40 + random() * 40)::INT / 100);
        ELSE
            v_amount_applied := 0;
        END IF;

        UPDATE credit_notes
        SET subtotal = v_subtotal, tax_amount = v_tax_amount,
            total_amount = v_total_amount, amount_applied = v_amount_applied
        WHERE id = v_cn_id;

        IF v_status IN ('posted', 'partial', 'applied') AND v_ar_account_id IS NOT NULL THEN
            v_journal_id := gen_random_uuid();

            INSERT INTO journal_entries (
                id, tenant_id, entry_number, entry_date, description,
                source_type, source_id, status, created_at, updated_at
            ) VALUES (
                v_journal_id, v_tenant_id,
                'JE-CN-2512-' || LPAD((i)::TEXT, 4, '0'),
                v_cn_date, 'Credit Note CN-2512-' || LPAD((i)::TEXT, 4, '0'),
                'credit_note', v_cn_id, 'POSTED', v_cn_date, v_cn_date
            );

            INSERT INTO journal_lines (id, journal_id, account_id, debit, credit, description)
            VALUES (gen_random_uuid(), v_journal_id, v_sales_return_account_id, v_subtotal, 0, 'Retur penjualan');

            INSERT INTO journal_lines (id, journal_id, account_id, debit, credit, description)
            VALUES (gen_random_uuid(), v_journal_id, v_ppn_out_account_id, v_tax_amount, 0, 'Koreksi PPN Keluaran');

            INSERT INTO journal_lines (id, journal_id, account_id, debit, credit, description)
            VALUES (gen_random_uuid(), v_journal_id, v_ar_account_id, 0, v_total_amount, 'Pengurangan piutang');
        END IF;

        v_cn_count := v_cn_count + 1;
    END LOOP;

    -- JANUARY 2026 - 5 Credit Notes
    FOR i IN 1..5 LOOP
        v_cn_date := '2026-01-02'::DATE + (random() * 14)::INT;
        v_cn_id := gen_random_uuid();
        v_random_customer := v_customer_ids[1 + (random() * (array_length(v_customer_ids, 1) - 1))::INT];

        IF v_invoice_ids IS NOT NULL AND array_length(v_invoice_ids, 1) > 0 THEN
            v_random_invoice := v_invoice_ids[1 + (random() * (array_length(v_invoice_ids, 1) - 1))::INT];
        ELSE
            v_random_invoice := NULL;
        END IF;

        IF i <= 1 THEN v_status := 'draft';
        ELSIF i <= 2 THEN v_status := 'posted';
        ELSIF i <= 3 THEN v_status := 'partial';
        ELSE v_status := 'applied';
        END IF;

        v_subtotal := 0;

        INSERT INTO credit_notes (
            id, tenant_id, credit_note_number, credit_note_date,
            customer_id, invoice_id, subtotal, discount_type, discount_value,
            discount_amount, tax_amount, total_amount, amount_applied, status,
            reason, notes, created_at, updated_at
        ) VALUES (
            v_cn_id, v_tenant_id,
            'CN-2601-' || LPAD((i)::TEXT, 4, '0'),
            v_cn_date, v_random_customer, v_random_invoice, 0, 'percent', 0, 0, 0, 0, 0,
            v_status, 'Kesalahan pengiriman',
            'Credit Note January ' || i,
            v_cn_date, v_cn_date
        );

        FOR j IN 1..(1 + (random() * 2)::INT) LOOP
            v_random_product := v_product_ids[1 + (random() * (array_length(v_product_ids, 1) - 1))::INT];
            v_qty := 1 + (random() * 10)::INT;

            SELECT sales_price INTO v_price FROM products WHERE id = v_random_product;
            v_price := COALESCE(v_price, 100000);

            INSERT INTO credit_note_items (
                id, credit_note_id, product_id, description,
                quantity, unit, unit_price, discount_percent,
                tax_rate, tax_amount, line_total, sort_order
            )
            SELECT
                gen_random_uuid(), v_cn_id, v_random_product, p.nama_produk,
                v_qty, p.satuan, v_price, 0,
                11, (v_qty * v_price * 11 / 100), v_qty * v_price * 111 / 100, j
            FROM products p WHERE p.id = v_random_product;

            v_subtotal := v_subtotal + (v_qty * v_price);
        END LOOP;

        v_tax_amount := (v_subtotal * 11 / 100);
        v_total_amount := v_subtotal + v_tax_amount;

        IF v_status = 'applied' THEN
            v_amount_applied := v_total_amount;
        ELSIF v_status = 'partial' THEN
            v_amount_applied := (v_total_amount * (40 + random() * 40)::INT / 100);
        ELSE
            v_amount_applied := 0;
        END IF;

        UPDATE credit_notes
        SET subtotal = v_subtotal, tax_amount = v_tax_amount,
            total_amount = v_total_amount, amount_applied = v_amount_applied
        WHERE id = v_cn_id;

        IF v_status IN ('posted', 'partial', 'applied') AND v_ar_account_id IS NOT NULL THEN
            v_journal_id := gen_random_uuid();

            INSERT INTO journal_entries (
                id, tenant_id, entry_number, entry_date, description,
                source_type, source_id, status, created_at, updated_at
            ) VALUES (
                v_journal_id, v_tenant_id,
                'JE-CN-2601-' || LPAD((i)::TEXT, 4, '0'),
                v_cn_date, 'Credit Note CN-2601-' || LPAD((i)::TEXT, 4, '0'),
                'credit_note', v_cn_id, 'POSTED', v_cn_date, v_cn_date
            );

            INSERT INTO journal_lines (id, journal_id, account_id, debit, credit, description)
            VALUES (gen_random_uuid(), v_journal_id, v_sales_return_account_id, v_subtotal, 0, 'Retur penjualan');

            INSERT INTO journal_lines (id, journal_id, account_id, debit, credit, description)
            VALUES (gen_random_uuid(), v_journal_id, v_ppn_out_account_id, v_tax_amount, 0, 'Koreksi PPN Keluaran');

            INSERT INTO journal_lines (id, journal_id, account_id, debit, credit, description)
            VALUES (gen_random_uuid(), v_journal_id, v_ar_account_id, 0, v_total_amount, 'Pengurangan piutang');
        END IF;

        v_cn_count := v_cn_count + 1;
    END LOOP;

    RAISE NOTICE '========================================';
    RAISE NOTICE 'Credit Notes created: %', v_cn_count;
    RAISE NOTICE '========================================';
END $$;

-- Verify Credit Notes by status
SELECT status, COUNT(*) as count, SUM(total_amount) as total_value, SUM(amount_applied) as total_applied
FROM credit_notes
WHERE tenant_id = 'evlogia'
GROUP BY status
ORDER BY status;
