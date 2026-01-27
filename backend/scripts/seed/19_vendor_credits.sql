-- =============================================
-- EVLOGIA SEED: 19_vendor_credits.sql
-- Purpose: Create 10+ Vendor Credits (Debit Notes / Purchase Returns) with journal entries
-- Posted vendor credits CREATE journal entries:
--   Dr. Hutang Usaha (2-10100)
--   Cr. Retur Pembelian (5-10200)
--   Cr. PPN Masukan (1-10500)
-- =============================================

DO $$
DECLARE
    v_tenant_id TEXT := current_setting('seed.tenant_id', true);
    v_vendor_ids UUID[];
    v_product_ids UUID[];
    v_bill_ids UUID[];
    v_vc_id UUID;
    v_vc_count INT := 0;
    v_random_vendor UUID;
    v_random_product UUID;
    v_random_bill UUID;
    v_qty INT;
    v_price BIGINT;
    v_subtotal BIGINT;
    v_tax_amount BIGINT;
    v_total_amount BIGINT;
    v_vc_date DATE;
    v_status TEXT;
    v_amount_applied BIGINT;
    -- Journal Entry
    v_journal_id UUID;
    v_ap_account_id UUID;
    v_purchase_return_account_id UUID;
    v_ppn_in_account_id UUID;
BEGIN
    IF v_tenant_id IS NULL OR v_tenant_id = '' THEN
        v_tenant_id := 'evlogia';
    END IF;

    RAISE NOTICE 'Creating vendor credits for tenant: %', v_tenant_id;

    -- Get account IDs for journal entries
    SELECT id INTO v_ap_account_id FROM chart_of_accounts
    WHERE tenant_id = v_tenant_id AND account_code = '2-10100';

    SELECT id INTO v_purchase_return_account_id FROM chart_of_accounts
    WHERE tenant_id = v_tenant_id AND account_code = '5-10200';

    SELECT id INTO v_ppn_in_account_id FROM chart_of_accounts
    WHERE tenant_id = v_tenant_id AND account_code = '1-10500';

    -- Get vendor IDs
    SELECT array_agg(id) INTO v_vendor_ids
    FROM vendors
    WHERE tenant_id = v_tenant_id;

    -- Get product IDs
    SELECT array_agg(id) INTO v_product_ids
    FROM products
    WHERE tenant_id = v_tenant_id
    AND kode_produk NOT LIKE 'SVC-%';

    -- Get posted bill IDs for reference
    SELECT array_agg(id) INTO v_bill_ids
    FROM bills
    WHERE tenant_id = v_tenant_id
    AND status IN ('received', 'partial', 'paid');

    -- ==========================================
    -- Generate 10+ Vendor Credits
    -- Distribution: Nov (3), Dec (4), Jan (3)
    -- Status: draft(2), posted(3), partial(2), applied(2), void(1)
    -- ==========================================

    -- NOVEMBER 2025 - 3 Vendor Credits
    FOR i IN 1..3 LOOP
        v_vc_date := '2025-11-05'::DATE + (random() * 25)::INT;
        v_vc_id := gen_random_uuid();
        v_random_vendor := v_vendor_ids[1 + (random() * (array_length(v_vendor_ids, 1) - 1))::INT];

        IF v_bill_ids IS NOT NULL AND array_length(v_bill_ids, 1) > 0 THEN
            v_random_bill := v_bill_ids[1 + (random() * (array_length(v_bill_ids, 1) - 1))::INT];
        ELSE
            v_random_bill := NULL;
        END IF;

        IF i <= 1 THEN v_status := 'draft';
        ELSIF i <= 2 THEN v_status := 'posted';
        ELSE v_status := 'applied';
        END IF;

        v_subtotal := 0;

        INSERT INTO vendor_credits (
            id, tenant_id, credit_number, credit_date,
            vendor_id, bill_id, subtotal, discount_type, discount_value,
            discount_amount, tax_amount, total_amount, amount_applied, status,
            reason, notes, created_at, updated_at
        ) VALUES (
            v_vc_id, v_tenant_id,
            'VC-2511-' || LPAD((i)::TEXT, 4, '0'),
            v_vc_date, v_random_vendor, v_random_bill, 0, 'percent', 0, 0, 0, 0, 0,
            v_status, 'Barang tidak sesuai spesifikasi',
            'Vendor Credit November ' || i,
            v_vc_date, v_vc_date
        );

        -- Insert 1-3 line items
        FOR j IN 1..(1 + (random() * 2)::INT) LOOP
            v_random_product := v_product_ids[1 + (random() * (array_length(v_product_ids, 1) - 1))::INT];
            v_qty := 1 + (random() * 10)::INT;

            SELECT purchase_price INTO v_price FROM products WHERE id = v_random_product;
            v_price := COALESCE(v_price, 50000);

            INSERT INTO vendor_credit_items (
                id, vendor_credit_id, product_id, description,
                quantity, unit, unit_price, discount_percent,
                tax_rate, tax_amount, line_total, sort_order
            )
            SELECT
                gen_random_uuid(), v_vc_id, v_random_product, p.nama_produk,
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

        UPDATE vendor_credits
        SET subtotal = v_subtotal, tax_amount = v_tax_amount,
            total_amount = v_total_amount, amount_applied = v_amount_applied
        WHERE id = v_vc_id;

        -- Create journal entry for posted/partial/applied vendor credits
        IF v_status IN ('posted', 'partial', 'applied') AND v_ap_account_id IS NOT NULL THEN
            v_journal_id := gen_random_uuid();

            INSERT INTO journal_entries (
                id, tenant_id, entry_number, entry_date, description,
                source_type, source_id, status, created_at, updated_at
            ) VALUES (
                v_journal_id, v_tenant_id,
                'JE-VC-2511-' || LPAD((i)::TEXT, 4, '0'),
                v_vc_date, 'Vendor Credit VC-2511-' || LPAD((i)::TEXT, 4, '0'),
                'vendor_credit', v_vc_id, 'POSTED', v_vc_date, v_vc_date
            );

            -- Dr. Hutang Usaha
            INSERT INTO journal_lines (id, journal_id, account_id, debit, credit, description)
            VALUES (gen_random_uuid(), v_journal_id, v_ap_account_id, v_total_amount, 0, 'Pengurangan hutang');

            -- Cr. Retur Pembelian
            INSERT INTO journal_lines (id, journal_id, account_id, debit, credit, description)
            VALUES (gen_random_uuid(), v_journal_id, v_purchase_return_account_id, 0, v_subtotal, 'Retur pembelian');

            -- Cr. PPN Masukan
            INSERT INTO journal_lines (id, journal_id, account_id, debit, credit, description)
            VALUES (gen_random_uuid(), v_journal_id, v_ppn_in_account_id, 0, v_tax_amount, 'Koreksi PPN Masukan');
        END IF;

        v_vc_count := v_vc_count + 1;
    END LOOP;

    -- DECEMBER 2025 - 4 Vendor Credits
    FOR i IN 1..4 LOOP
        v_vc_date := '2025-12-05'::DATE + (random() * 25)::INT;
        v_vc_id := gen_random_uuid();
        v_random_vendor := v_vendor_ids[1 + (random() * (array_length(v_vendor_ids, 1) - 1))::INT];

        IF v_bill_ids IS NOT NULL AND array_length(v_bill_ids, 1) > 0 THEN
            v_random_bill := v_bill_ids[1 + (random() * (array_length(v_bill_ids, 1) - 1))::INT];
        ELSE
            v_random_bill := NULL;
        END IF;

        IF i <= 1 THEN v_status := 'draft';
        ELSIF i <= 2 THEN v_status := 'posted';
        ELSIF i <= 3 THEN v_status := 'partial';
        ELSE v_status := 'void';
        END IF;

        v_subtotal := 0;

        INSERT INTO vendor_credits (
            id, tenant_id, credit_number, credit_date,
            vendor_id, bill_id, subtotal, discount_type, discount_value,
            discount_amount, tax_amount, total_amount, amount_applied, status,
            reason, notes, created_at, updated_at
        ) VALUES (
            v_vc_id, v_tenant_id,
            'VC-2512-' || LPAD((i)::TEXT, 4, '0'),
            v_vc_date, v_random_vendor, v_random_bill, 0, 'percent', 0, 0, 0, 0, 0,
            v_status, 'Barang rusak/defect',
            'Vendor Credit December ' || i,
            v_vc_date, v_vc_date
        );

        FOR j IN 1..(1 + (random() * 2)::INT) LOOP
            v_random_product := v_product_ids[1 + (random() * (array_length(v_product_ids, 1) - 1))::INT];
            v_qty := 1 + (random() * 15)::INT;

            SELECT purchase_price INTO v_price FROM products WHERE id = v_random_product;
            v_price := COALESCE(v_price, 50000);

            INSERT INTO vendor_credit_items (
                id, vendor_credit_id, product_id, description,
                quantity, unit, unit_price, discount_percent,
                tax_rate, tax_amount, line_total, sort_order
            )
            SELECT
                gen_random_uuid(), v_vc_id, v_random_product, p.nama_produk,
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

        UPDATE vendor_credits
        SET subtotal = v_subtotal, tax_amount = v_tax_amount,
            total_amount = v_total_amount, amount_applied = v_amount_applied
        WHERE id = v_vc_id;

        IF v_status IN ('posted', 'partial', 'applied') AND v_ap_account_id IS NOT NULL THEN
            v_journal_id := gen_random_uuid();

            INSERT INTO journal_entries (
                id, tenant_id, entry_number, entry_date, description,
                source_type, source_id, status, created_at, updated_at
            ) VALUES (
                v_journal_id, v_tenant_id,
                'JE-VC-2512-' || LPAD((i)::TEXT, 4, '0'),
                v_vc_date, 'Vendor Credit VC-2512-' || LPAD((i)::TEXT, 4, '0'),
                'vendor_credit', v_vc_id, 'POSTED', v_vc_date, v_vc_date
            );

            INSERT INTO journal_lines (id, journal_id, account_id, debit, credit, description)
            VALUES (gen_random_uuid(), v_journal_id, v_ap_account_id, v_total_amount, 0, 'Pengurangan hutang');

            INSERT INTO journal_lines (id, journal_id, account_id, debit, credit, description)
            VALUES (gen_random_uuid(), v_journal_id, v_purchase_return_account_id, 0, v_subtotal, 'Retur pembelian');

            INSERT INTO journal_lines (id, journal_id, account_id, debit, credit, description)
            VALUES (gen_random_uuid(), v_journal_id, v_ppn_in_account_id, 0, v_tax_amount, 'Koreksi PPN Masukan');
        END IF;

        v_vc_count := v_vc_count + 1;
    END LOOP;

    -- JANUARY 2026 - 3 Vendor Credits
    FOR i IN 1..3 LOOP
        v_vc_date := '2026-01-02'::DATE + (random() * 14)::INT;
        v_vc_id := gen_random_uuid();
        v_random_vendor := v_vendor_ids[1 + (random() * (array_length(v_vendor_ids, 1) - 1))::INT];

        IF v_bill_ids IS NOT NULL AND array_length(v_bill_ids, 1) > 0 THEN
            v_random_bill := v_bill_ids[1 + (random() * (array_length(v_bill_ids, 1) - 1))::INT];
        ELSE
            v_random_bill := NULL;
        END IF;

        IF i <= 1 THEN v_status := 'posted';
        ELSIF i <= 2 THEN v_status := 'partial';
        ELSE v_status := 'applied';
        END IF;

        v_subtotal := 0;

        INSERT INTO vendor_credits (
            id, tenant_id, credit_number, credit_date,
            vendor_id, bill_id, subtotal, discount_type, discount_value,
            discount_amount, tax_amount, total_amount, amount_applied, status,
            reason, notes, created_at, updated_at
        ) VALUES (
            v_vc_id, v_tenant_id,
            'VC-2601-' || LPAD((i)::TEXT, 4, '0'),
            v_vc_date, v_random_vendor, v_random_bill, 0, 'percent', 0, 0, 0, 0, 0,
            v_status, 'Kuantitas tidak sesuai',
            'Vendor Credit January ' || i,
            v_vc_date, v_vc_date
        );

        FOR j IN 1..(1 + (random() * 2)::INT) LOOP
            v_random_product := v_product_ids[1 + (random() * (array_length(v_product_ids, 1) - 1))::INT];
            v_qty := 1 + (random() * 10)::INT;

            SELECT purchase_price INTO v_price FROM products WHERE id = v_random_product;
            v_price := COALESCE(v_price, 50000);

            INSERT INTO vendor_credit_items (
                id, vendor_credit_id, product_id, description,
                quantity, unit, unit_price, discount_percent,
                tax_rate, tax_amount, line_total, sort_order
            )
            SELECT
                gen_random_uuid(), v_vc_id, v_random_product, p.nama_produk,
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

        UPDATE vendor_credits
        SET subtotal = v_subtotal, tax_amount = v_tax_amount,
            total_amount = v_total_amount, amount_applied = v_amount_applied
        WHERE id = v_vc_id;

        IF v_status IN ('posted', 'partial', 'applied') AND v_ap_account_id IS NOT NULL THEN
            v_journal_id := gen_random_uuid();

            INSERT INTO journal_entries (
                id, tenant_id, entry_number, entry_date, description,
                source_type, source_id, status, created_at, updated_at
            ) VALUES (
                v_journal_id, v_tenant_id,
                'JE-VC-2601-' || LPAD((i)::TEXT, 4, '0'),
                v_vc_date, 'Vendor Credit VC-2601-' || LPAD((i)::TEXT, 4, '0'),
                'vendor_credit', v_vc_id, 'POSTED', v_vc_date, v_vc_date
            );

            INSERT INTO journal_lines (id, journal_id, account_id, debit, credit, description)
            VALUES (gen_random_uuid(), v_journal_id, v_ap_account_id, v_total_amount, 0, 'Pengurangan hutang');

            INSERT INTO journal_lines (id, journal_id, account_id, debit, credit, description)
            VALUES (gen_random_uuid(), v_journal_id, v_purchase_return_account_id, 0, v_subtotal, 'Retur pembelian');

            INSERT INTO journal_lines (id, journal_id, account_id, debit, credit, description)
            VALUES (gen_random_uuid(), v_journal_id, v_ppn_in_account_id, 0, v_tax_amount, 'Koreksi PPN Masukan');
        END IF;

        v_vc_count := v_vc_count + 1;
    END LOOP;

    RAISE NOTICE '========================================';
    RAISE NOTICE 'Vendor Credits created: %', v_vc_count;
    RAISE NOTICE '========================================';
END $$;

-- Verify Vendor Credits by status
SELECT status, COUNT(*) as count, SUM(total_amount) as total_value, SUM(amount_applied) as total_applied
FROM vendor_credits
WHERE tenant_id = 'evlogia'
GROUP BY status
ORDER BY status;
