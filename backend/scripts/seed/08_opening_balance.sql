-- =============================================
-- EVLOGIA SEED: 08_opening_balance.sql
-- Purpose: Create opening balances as of 1 November 2025
-- =============================================

DO $$
DECLARE
    v_tenant_id TEXT := current_setting('seed.tenant_id', true);
    v_opening_date DATE := '2025-11-01';
    v_wh_atput_id UUID;
    v_wh_4a_id UUID;
    v_product_id UUID;
    v_journal_id UUID;
    v_coa_inventory UUID;
    v_coa_ar UUID;
    v_coa_ap UUID;
    v_coa_bank_bca UUID;
    v_coa_bank_mandiri UUID;
    v_coa_cash UUID;
    v_coa_opening_equity UUID;
    v_total_inventory BIGINT := 0;
    v_total_ar BIGINT := 0;
    v_total_ap BIGINT := 0;
    v_total_bank BIGINT := 0;
BEGIN
    IF v_tenant_id IS NULL OR v_tenant_id = '' THEN
        v_tenant_id := 'evlogia';
    END IF;

    RAISE NOTICE 'Creating opening balances for tenant: % as of %', v_tenant_id, v_opening_date;

    -- Get warehouse IDs
    SELECT id INTO v_wh_atput_id FROM warehouses WHERE tenant_id = v_tenant_id AND code = 'WH-ATPUT';
    SELECT id INTO v_wh_4a_id FROM warehouses WHERE tenant_id = v_tenant_id AND code = 'WH-4A';

    -- Get CoA IDs
    SELECT id INTO v_coa_inventory FROM chart_of_accounts WHERE tenant_id = v_tenant_id AND account_code = '1-10400';
    SELECT id INTO v_coa_ar FROM chart_of_accounts WHERE tenant_id = v_tenant_id AND account_code = '1-10300';
    SELECT id INTO v_coa_ap FROM chart_of_accounts WHERE tenant_id = v_tenant_id AND account_code = '2-10100';
    SELECT id INTO v_coa_bank_bca FROM chart_of_accounts WHERE tenant_id = v_tenant_id AND account_code = '1-10201';
    SELECT id INTO v_coa_bank_mandiri FROM chart_of_accounts WHERE tenant_id = v_tenant_id AND account_code = '1-10202';
    SELECT id INTO v_coa_cash FROM chart_of_accounts WHERE tenant_id = v_tenant_id AND account_code = '1-10100';
    SELECT id INTO v_coa_opening_equity FROM chart_of_accounts WHERE tenant_id = v_tenant_id AND account_code = '3-50000';

    -- ==========================================
    -- 1. INVENTORY OPENING BALANCE (Persediaan)
    -- ==========================================

    -- Raw Materials - Kain (at Gudang Atput)
    INSERT INTO persediaan (id, tenant_id, product_id, warehouse_id, qty_on_hand, unit_cost, total_value, created_at, updated_at)
    SELECT
        gen_random_uuid(), v_tenant_id, p.id, v_wh_atput_id,
        CASE
            WHEN p.kode_produk LIKE 'KTN-%' THEN 250  -- 5 roll = 250 meter
            WHEN p.kode_produk LIKE 'LNN-%' THEN 120  -- 3 roll = 120 meter
            WHEN p.kode_produk LIKE 'DNM-%' THEN 90   -- 3 roll = 90 meter
            WHEN p.kode_produk LIKE 'BTK-%' THEN 75   -- 3 roll
            WHEN p.kode_produk LIKE 'TWL-%' THEN 150  -- 3 roll = 150 meter
            WHEN p.kode_produk LIKE 'FLC-%' THEN 80   -- 2 roll = 80 meter
        END,
        p.purchase_price,
        CASE
            WHEN p.kode_produk LIKE 'KTN-%' THEN 250 * p.purchase_price
            WHEN p.kode_produk LIKE 'LNN-%' THEN 120 * p.purchase_price
            WHEN p.kode_produk LIKE 'DNM-%' THEN 90 * p.purchase_price
            WHEN p.kode_produk LIKE 'BTK-%' THEN 75 * p.purchase_price
            WHEN p.kode_produk LIKE 'TWL-%' THEN 150 * p.purchase_price
            WHEN p.kode_produk LIKE 'FLC-%' THEN 80 * p.purchase_price
        END,
        v_opening_date, v_opening_date
    FROM products p
    WHERE p.tenant_id = v_tenant_id
    AND p.kategori = 'Bahan Kain';

    -- Raw Materials - Benang (at Gudang Atput)
    INSERT INTO persediaan (id, tenant_id, product_id, warehouse_id, qty_on_hand, unit_cost, total_value, created_at, updated_at)
    SELECT
        gen_random_uuid(), v_tenant_id, p.id, v_wh_atput_id,
        720,  -- 5 ball = 720 pcs (for standard) or 500 pcs (for special)
        p.purchase_price,
        720 * p.purchase_price,
        v_opening_date, v_opening_date
    FROM products p
    WHERE p.tenant_id = v_tenant_id
    AND p.kategori = 'Bahan Benang';

    -- Raw Materials - Aksesoris (at Gudang Atput)
    INSERT INTO persediaan (id, tenant_id, product_id, warehouse_id, qty_on_hand, unit_cost, total_value, created_at, updated_at)
    SELECT
        gen_random_uuid(), v_tenant_id, p.id, v_wh_atput_id,
        CASE
            WHEN p.kode_produk LIKE 'KNC-%' THEN 1440  -- 10 gross
            WHEN p.kode_produk LIKE 'RSL-%' THEN 100   -- 10 pack
            WHEN p.kode_produk LIKE 'LBL-001' THEN 2500  -- 5 roll
            WHEN p.kode_produk LIKE 'LBL-002' THEN 500   -- 5 pack
            WHEN p.kode_produk LIKE 'PKG-%' THEN 500     -- 5 pack
        END,
        p.purchase_price,
        CASE
            WHEN p.kode_produk LIKE 'KNC-%' THEN 1440 * p.purchase_price
            WHEN p.kode_produk LIKE 'RSL-%' THEN 100 * p.purchase_price
            WHEN p.kode_produk LIKE 'LBL-001' THEN 2500 * p.purchase_price
            WHEN p.kode_produk LIKE 'LBL-002' THEN 500 * p.purchase_price
            WHEN p.kode_produk LIKE 'PKG-%' THEN 500 * p.purchase_price
        END,
        v_opening_date, v_opening_date
    FROM products p
    WHERE p.tenant_id = v_tenant_id
    AND p.kategori = 'Aksesoris';

    -- FG Trading at Gudang Atput
    INSERT INTO persediaan (id, tenant_id, product_id, warehouse_id, qty_on_hand, unit_cost, total_value, created_at, updated_at)
    SELECT
        gen_random_uuid(), v_tenant_id, p.id, v_wh_atput_id,
        CASE
            WHEN p.kode_produk IN ('IMP-001', 'IMP-002', 'IMP-003') THEN 120  -- 10 lusin kaos
            WHEN p.kode_produk = 'IMP-008' THEN 60  -- 5 lusin polo
            ELSE 50  -- 50 pcs others
        END,
        p.purchase_price,
        CASE
            WHEN p.kode_produk IN ('IMP-001', 'IMP-002', 'IMP-003') THEN 120 * p.purchase_price
            WHEN p.kode_produk = 'IMP-008' THEN 60 * p.purchase_price
            ELSE 50 * p.purchase_price
        END,
        v_opening_date, v_opening_date
    FROM products p
    WHERE p.tenant_id = v_tenant_id
    AND p.kategori = 'FG Trading';

    -- FG Trading at Gudang 4A (Toko) - smaller qty
    INSERT INTO persediaan (id, tenant_id, product_id, warehouse_id, qty_on_hand, unit_cost, total_value, created_at, updated_at)
    SELECT
        gen_random_uuid(), v_tenant_id, p.id, v_wh_4a_id,
        CASE
            WHEN p.kode_produk IN ('IMP-001', 'IMP-002', 'IMP-003') THEN 36  -- 3 lusin kaos
            WHEN p.kode_produk = 'IMP-008' THEN 24  -- 2 lusin polo
            ELSE 20  -- 20 pcs others
        END,
        p.purchase_price,
        CASE
            WHEN p.kode_produk IN ('IMP-001', 'IMP-002', 'IMP-003') THEN 36 * p.purchase_price
            WHEN p.kode_produk = 'IMP-008' THEN 24 * p.purchase_price
            ELSE 20 * p.purchase_price
        END,
        v_opening_date, v_opening_date
    FROM products p
    WHERE p.tenant_id = v_tenant_id
    AND p.kategori = 'FG Trading';

    -- FG Produksi at Gudang Atput - some initial stock
    INSERT INTO persediaan (id, tenant_id, product_id, warehouse_id, qty_on_hand, unit_cost, total_value, created_at, updated_at)
    SELECT
        gen_random_uuid(), v_tenant_id, p.id, v_wh_atput_id,
        25,  -- 25 pcs each
        120000,  -- estimated production cost
        25 * 120000,
        v_opening_date, v_opening_date
    FROM products p
    WHERE p.tenant_id = v_tenant_id
    AND p.kategori = 'FG Produksi';

    -- Calculate total inventory value
    SELECT COALESCE(SUM(total_value), 0) INTO v_total_inventory
    FROM persediaan
    WHERE tenant_id = v_tenant_id;

    RAISE NOTICE 'Inventory opening balance: Rp %', v_total_inventory;

    -- ==========================================
    -- 2. BANK ACCOUNTS OPENING BALANCE
    -- ==========================================

    -- Bank BCA
    INSERT INTO bank_accounts (
        id, tenant_id, account_name, account_number, bank_name,
        coa_id, opening_balance, current_balance, account_type,
        is_active, is_default, created_at, updated_at
    ) VALUES (
        gen_random_uuid(), v_tenant_id, 'Bank BCA Evlogia', '123-456-7890', 'Bank BCA',
        v_coa_bank_bca, 500000000, 500000000, 'bank',
        true, true, v_opening_date, v_opening_date
    ) ON CONFLICT DO NOTHING;

    -- Bank Mandiri
    INSERT INTO bank_accounts (
        id, tenant_id, account_name, account_number, bank_name,
        coa_id, opening_balance, current_balance, account_type,
        is_active, is_default, created_at, updated_at
    ) VALUES (
        gen_random_uuid(), v_tenant_id, 'Bank Mandiri Evlogia', '987-654-3210', 'Bank Mandiri',
        v_coa_bank_mandiri, 250000000, 250000000, 'bank',
        true, false, v_opening_date, v_opening_date
    ) ON CONFLICT DO NOTHING;

    -- Kas Toko
    INSERT INTO bank_accounts (
        id, tenant_id, account_name, account_number, bank_name,
        coa_id, opening_balance, current_balance, account_type,
        is_active, is_default, created_at, updated_at
    ) VALUES (
        gen_random_uuid(), v_tenant_id, 'Kas Toko Evlogia', '-', 'Kas',
        v_coa_cash, 50000000, 50000000, 'cash',
        true, false, v_opening_date, v_opening_date
    ) ON CONFLICT DO NOTHING;

    v_total_bank := 500000000 + 250000000 + 50000000;  -- 800 juta
    RAISE NOTICE 'Bank opening balance: Rp %', v_total_bank;

    -- ==========================================
    -- 3. AR OPENING BALANCE (Piutang)
    -- Some customers have outstanding invoices from before go-live
    -- ==========================================

    -- This will be handled by creating "opening balance invoices"
    -- For simplicity, we assume AR opening = 0 for now
    -- Real implementation would create opening invoices

    v_total_ar := 0;  -- Will be set by opening invoices if needed
    RAISE NOTICE 'AR opening balance: Rp %', v_total_ar;

    -- ==========================================
    -- 4. AP OPENING BALANCE (Hutang)
    -- Some vendors have outstanding bills from before go-live
    -- ==========================================

    v_total_ap := 0;  -- Will be set by opening bills if needed
    RAISE NOTICE 'AP opening balance: Rp %', v_total_ap;

    -- ==========================================
    -- 5. CREATE OPENING BALANCE JOURNAL ENTRY
    -- ==========================================

    v_journal_id := gen_random_uuid();

    INSERT INTO journal_entries (
        id, tenant_id, journal_number, entry_date, journal_type,
        source_type, description, total_debit, total_credit,
        status, is_opening_balance, created_at, updated_at, posted_at
    ) VALUES (
        v_journal_id, v_tenant_id, 'JV-OB-2511-0001', v_opening_date, 'OPENING',
        'opening_balance', 'Opening Balance 1 November 2025',
        v_total_inventory + v_total_bank + v_total_ar,
        v_total_inventory + v_total_bank + v_total_ar,
        'POSTED', true, v_opening_date, v_opening_date, v_opening_date
    );

    -- Journal Lines - Debit Assets
    -- Inventory
    IF v_total_inventory > 0 THEN
        INSERT INTO journal_lines (id, tenant_id, journal_id, line_number, account_id, debit, credit, description)
        VALUES (gen_random_uuid(), v_tenant_id, v_journal_id, 1, v_coa_inventory, v_total_inventory, 0, 'Opening Inventory');
    END IF;

    -- Bank BCA
    INSERT INTO journal_lines (id, tenant_id, journal_id, line_number, account_id, debit, credit, description)
    VALUES (gen_random_uuid(), v_tenant_id, v_journal_id, 2, v_coa_bank_bca, 500000000, 0, 'Opening Bank BCA');

    -- Bank Mandiri
    INSERT INTO journal_lines (id, tenant_id, journal_id, line_number, account_id, debit, credit, description)
    VALUES (gen_random_uuid(), v_tenant_id, v_journal_id, 3, v_coa_bank_mandiri, 250000000, 0, 'Opening Bank Mandiri');

    -- Kas
    INSERT INTO journal_lines (id, tenant_id, journal_id, line_number, account_id, debit, credit, description)
    VALUES (gen_random_uuid(), v_tenant_id, v_journal_id, 4, v_coa_cash, 50000000, 0, 'Opening Kas Toko');

    -- Journal Lines - Credit Opening Equity
    INSERT INTO journal_lines (id, tenant_id, journal_id, line_number, account_id, debit, credit, description)
    VALUES (gen_random_uuid(), v_tenant_id, v_journal_id, 5, v_coa_opening_equity, 0, v_total_inventory + v_total_bank, 'Opening Balance Equity');

    RAISE NOTICE '========================================';
    RAISE NOTICE 'Opening Balance Summary:';
    RAISE NOTICE '  Inventory: Rp %', v_total_inventory;
    RAISE NOTICE '  Bank/Cash: Rp %', v_total_bank;
    RAISE NOTICE '  AR: Rp %', v_total_ar;
    RAISE NOTICE '  AP: Rp %', v_total_ap;
    RAISE NOTICE '  Total Assets: Rp %', v_total_inventory + v_total_bank + v_total_ar;
    RAISE NOTICE '========================================';
END $$;

-- Verify opening balances
SELECT 'Inventory' as category, COUNT(*) as items, SUM(total_value) as total_value
FROM persediaan WHERE tenant_id = 'evlogia'
UNION ALL
SELECT 'Bank Accounts', COUNT(*), SUM(opening_balance)
FROM bank_accounts WHERE tenant_id = 'evlogia';

-- Verify journal is balanced
SELECT
    je.journal_number,
    SUM(jl.debit) as total_debit,
    SUM(jl.credit) as total_credit,
    SUM(jl.debit) - SUM(jl.credit) as difference
FROM journal_entries je
JOIN journal_lines jl ON jl.journal_id = je.id
WHERE je.tenant_id = 'evlogia' AND je.is_opening_balance = true
GROUP BY je.journal_number;
