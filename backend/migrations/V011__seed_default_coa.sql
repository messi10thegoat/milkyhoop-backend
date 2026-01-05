-- ============================================================================
-- MilkyHoop Accounting Kernel - Default Chart of Accounts
-- Migration: V011
-- Date: 2026-01-04
-- Description: Seed default CoA untuk Indonesia (SAK EMKM compliant)
-- ============================================================================

-- Function to seed CoA for a tenant
CREATE OR REPLACE FUNCTION seed_default_coa(p_tenant_id UUID)
RETURNS INT AS $$
DECLARE
    v_count INT := 0;
    v_parent_id UUID;
BEGIN
    -- ═══════════════════════════════════════════════════════════════════════
    -- ASSETS (1-xxxxx) - Normal Balance: DEBIT
    -- ═══════════════════════════════════════════════════════════════════════

    -- Root Asset
    INSERT INTO chart_of_accounts (tenant_id, code, name, type, normal_balance, is_system, is_active)
    VALUES (p_tenant_id, '1-00000', 'ASET', 'ASSET', 'DEBIT', true, true)
    ON CONFLICT (tenant_id, code) DO NOTHING;
    v_count := v_count + 1;

    -- Get parent ID for Aset
    SELECT id INTO v_parent_id FROM chart_of_accounts
    WHERE tenant_id = p_tenant_id AND code = '1-00000';

    -- Aset Lancar (Current Assets)
    INSERT INTO chart_of_accounts (tenant_id, code, name, type, normal_balance, parent_id, is_active)
    VALUES (p_tenant_id, '1-10000', 'Aset Lancar', 'ASSET', 'DEBIT', v_parent_id, true)
    ON CONFLICT (tenant_id, code) DO NOTHING;
    v_count := v_count + 1;

    SELECT id INTO v_parent_id FROM chart_of_accounts
    WHERE tenant_id = p_tenant_id AND code = '1-10000';

    -- Detail Aset Lancar
    INSERT INTO chart_of_accounts (tenant_id, code, name, type, normal_balance, parent_id, is_system, is_active) VALUES
    (p_tenant_id, '1-10100', 'Kas', 'ASSET', 'DEBIT', v_parent_id, true, true),
    (p_tenant_id, '1-10200', 'Bank', 'ASSET', 'DEBIT', v_parent_id, false, true),
    (p_tenant_id, '1-10300', 'Piutang Usaha', 'ASSET', 'DEBIT', v_parent_id, true, true),
    (p_tenant_id, '1-10400', 'Persediaan Barang', 'ASSET', 'DEBIT', v_parent_id, true, true),
    (p_tenant_id, '1-10500', 'PPN Masukan', 'ASSET', 'DEBIT', v_parent_id, false, true),
    (p_tenant_id, '1-10600', 'Biaya Dibayar Dimuka', 'ASSET', 'DEBIT', v_parent_id, false, true)
    ON CONFLICT (tenant_id, code) DO NOTHING;
    v_count := v_count + 6;

    -- Bank sub-accounts
    SELECT id INTO v_parent_id FROM chart_of_accounts
    WHERE tenant_id = p_tenant_id AND code = '1-10200';

    INSERT INTO chart_of_accounts (tenant_id, code, name, type, normal_balance, parent_id, is_active) VALUES
    (p_tenant_id, '1-10201', 'Bank BCA', 'ASSET', 'DEBIT', v_parent_id, true),
    (p_tenant_id, '1-10202', 'Bank Mandiri', 'ASSET', 'DEBIT', v_parent_id, true),
    (p_tenant_id, '1-10203', 'Bank BRI', 'ASSET', 'DEBIT', v_parent_id, true),
    (p_tenant_id, '1-10204', 'Bank BNI', 'ASSET', 'DEBIT', v_parent_id, true)
    ON CONFLICT (tenant_id, code) DO NOTHING;
    v_count := v_count + 4;

    -- Aset Tetap (Fixed Assets)
    SELECT id INTO v_parent_id FROM chart_of_accounts
    WHERE tenant_id = p_tenant_id AND code = '1-00000';

    INSERT INTO chart_of_accounts (tenant_id, code, name, type, normal_balance, parent_id, is_active)
    VALUES (p_tenant_id, '1-20000', 'Aset Tetap', 'ASSET', 'DEBIT', v_parent_id, true)
    ON CONFLICT (tenant_id, code) DO NOTHING;
    v_count := v_count + 1;

    SELECT id INTO v_parent_id FROM chart_of_accounts
    WHERE tenant_id = p_tenant_id AND code = '1-20000';

    INSERT INTO chart_of_accounts (tenant_id, code, name, type, normal_balance, parent_id, is_active) VALUES
    (p_tenant_id, '1-20100', 'Peralatan', 'ASSET', 'DEBIT', v_parent_id, true),
    (p_tenant_id, '1-20200', 'Kendaraan', 'ASSET', 'DEBIT', v_parent_id, true),
    (p_tenant_id, '1-20300', 'Bangunan', 'ASSET', 'DEBIT', v_parent_id, true),
    (p_tenant_id, '1-20900', 'Akumulasi Penyusutan', 'ASSET', 'CREDIT', v_parent_id, true)
    ON CONFLICT (tenant_id, code) DO NOTHING;
    v_count := v_count + 4;


    -- ═══════════════════════════════════════════════════════════════════════
    -- LIABILITIES (2-xxxxx) - Normal Balance: CREDIT
    -- ═══════════════════════════════════════════════════════════════════════

    INSERT INTO chart_of_accounts (tenant_id, code, name, type, normal_balance, is_system, is_active)
    VALUES (p_tenant_id, '2-00000', 'KEWAJIBAN', 'LIABILITY', 'CREDIT', true, true)
    ON CONFLICT (tenant_id, code) DO NOTHING;
    v_count := v_count + 1;

    SELECT id INTO v_parent_id FROM chart_of_accounts
    WHERE tenant_id = p_tenant_id AND code = '2-00000';

    -- Kewajiban Lancar (Current Liabilities)
    INSERT INTO chart_of_accounts (tenant_id, code, name, type, normal_balance, parent_id, is_active)
    VALUES (p_tenant_id, '2-10000', 'Kewajiban Lancar', 'LIABILITY', 'CREDIT', v_parent_id, true)
    ON CONFLICT (tenant_id, code) DO NOTHING;
    v_count := v_count + 1;

    SELECT id INTO v_parent_id FROM chart_of_accounts
    WHERE tenant_id = p_tenant_id AND code = '2-10000';

    INSERT INTO chart_of_accounts (tenant_id, code, name, type, normal_balance, parent_id, is_system, is_active) VALUES
    (p_tenant_id, '2-10100', 'Hutang Usaha', 'LIABILITY', 'CREDIT', v_parent_id, true, true),
    (p_tenant_id, '2-10200', 'Hutang Bank', 'LIABILITY', 'CREDIT', v_parent_id, false, true),
    (p_tenant_id, '2-10300', 'Hutang Gaji', 'LIABILITY', 'CREDIT', v_parent_id, false, true),
    (p_tenant_id, '2-10400', 'PPN Keluaran', 'LIABILITY', 'CREDIT', v_parent_id, false, true),
    (p_tenant_id, '2-10500', 'Hutang Pajak', 'LIABILITY', 'CREDIT', v_parent_id, false, true)
    ON CONFLICT (tenant_id, code) DO NOTHING;
    v_count := v_count + 5;

    -- Kewajiban Jangka Panjang
    SELECT id INTO v_parent_id FROM chart_of_accounts
    WHERE tenant_id = p_tenant_id AND code = '2-00000';

    INSERT INTO chart_of_accounts (tenant_id, code, name, type, normal_balance, parent_id, is_active)
    VALUES (p_tenant_id, '2-20000', 'Kewajiban Jangka Panjang', 'LIABILITY', 'CREDIT', v_parent_id, true)
    ON CONFLICT (tenant_id, code) DO NOTHING;
    v_count := v_count + 1;

    SELECT id INTO v_parent_id FROM chart_of_accounts
    WHERE tenant_id = p_tenant_id AND code = '2-20000';

    INSERT INTO chart_of_accounts (tenant_id, code, name, type, normal_balance, parent_id, is_active) VALUES
    (p_tenant_id, '2-20100', 'Hutang Bank Jangka Panjang', 'LIABILITY', 'CREDIT', v_parent_id, true)
    ON CONFLICT (tenant_id, code) DO NOTHING;
    v_count := v_count + 1;


    -- ═══════════════════════════════════════════════════════════════════════
    -- EQUITY (3-xxxxx) - Normal Balance: CREDIT
    -- ═══════════════════════════════════════════════════════════════════════

    INSERT INTO chart_of_accounts (tenant_id, code, name, type, normal_balance, is_system, is_active)
    VALUES (p_tenant_id, '3-00000', 'MODAL', 'EQUITY', 'CREDIT', true, true)
    ON CONFLICT (tenant_id, code) DO NOTHING;
    v_count := v_count + 1;

    SELECT id INTO v_parent_id FROM chart_of_accounts
    WHERE tenant_id = p_tenant_id AND code = '3-00000';

    INSERT INTO chart_of_accounts (tenant_id, code, name, type, normal_balance, parent_id, is_system, is_active) VALUES
    (p_tenant_id, '3-10000', 'Modal Disetor', 'EQUITY', 'CREDIT', v_parent_id, false, true),
    (p_tenant_id, '3-20000', 'Laba Ditahan', 'EQUITY', 'CREDIT', v_parent_id, true, true),
    (p_tenant_id, '3-30000', 'Laba Tahun Berjalan', 'EQUITY', 'CREDIT', v_parent_id, true, true),
    (p_tenant_id, '3-40000', 'Prive', 'EQUITY', 'DEBIT', v_parent_id, false, true)
    ON CONFLICT (tenant_id, code) DO NOTHING;
    v_count := v_count + 4;


    -- ═══════════════════════════════════════════════════════════════════════
    -- INCOME (4-xxxxx) - Normal Balance: CREDIT
    -- ═══════════════════════════════════════════════════════════════════════

    INSERT INTO chart_of_accounts (tenant_id, code, name, type, normal_balance, is_system, is_active)
    VALUES (p_tenant_id, '4-00000', 'PENDAPATAN', 'INCOME', 'CREDIT', true, true)
    ON CONFLICT (tenant_id, code) DO NOTHING;
    v_count := v_count + 1;

    SELECT id INTO v_parent_id FROM chart_of_accounts
    WHERE tenant_id = p_tenant_id AND code = '4-00000';

    -- Pendapatan Usaha
    INSERT INTO chart_of_accounts (tenant_id, code, name, type, normal_balance, parent_id, is_active)
    VALUES (p_tenant_id, '4-10000', 'Pendapatan Usaha', 'INCOME', 'CREDIT', v_parent_id, true)
    ON CONFLICT (tenant_id, code) DO NOTHING;
    v_count := v_count + 1;

    SELECT id INTO v_parent_id FROM chart_of_accounts
    WHERE tenant_id = p_tenant_id AND code = '4-10000';

    INSERT INTO chart_of_accounts (tenant_id, code, name, type, normal_balance, parent_id, is_system, is_active) VALUES
    (p_tenant_id, '4-10100', 'Penjualan', 'INCOME', 'CREDIT', v_parent_id, true, true),
    (p_tenant_id, '4-10200', 'Diskon Penjualan', 'INCOME', 'DEBIT', v_parent_id, false, true),
    (p_tenant_id, '4-10300', 'Retur Penjualan', 'INCOME', 'DEBIT', v_parent_id, false, true)
    ON CONFLICT (tenant_id, code) DO NOTHING;
    v_count := v_count + 3;

    -- Pendapatan Lain-lain
    SELECT id INTO v_parent_id FROM chart_of_accounts
    WHERE tenant_id = p_tenant_id AND code = '4-00000';

    INSERT INTO chart_of_accounts (tenant_id, code, name, type, normal_balance, parent_id, is_active)
    VALUES (p_tenant_id, '4-20000', 'Pendapatan Lain-lain', 'INCOME', 'CREDIT', v_parent_id, true)
    ON CONFLICT (tenant_id, code) DO NOTHING;
    v_count := v_count + 1;


    -- ═══════════════════════════════════════════════════════════════════════
    -- EXPENSES (5-xxxxx) - Normal Balance: DEBIT
    -- ═══════════════════════════════════════════════════════════════════════

    INSERT INTO chart_of_accounts (tenant_id, code, name, type, normal_balance, is_system, is_active)
    VALUES (p_tenant_id, '5-00000', 'BEBAN', 'EXPENSE', 'DEBIT', true, true)
    ON CONFLICT (tenant_id, code) DO NOTHING;
    v_count := v_count + 1;

    SELECT id INTO v_parent_id FROM chart_of_accounts
    WHERE tenant_id = p_tenant_id AND code = '5-00000';

    -- Harga Pokok Penjualan (COGS)
    INSERT INTO chart_of_accounts (tenant_id, code, name, type, normal_balance, parent_id, is_active)
    VALUES (p_tenant_id, '5-10000', 'Harga Pokok Penjualan', 'EXPENSE', 'DEBIT', v_parent_id, true)
    ON CONFLICT (tenant_id, code) DO NOTHING;
    v_count := v_count + 1;

    SELECT id INTO v_parent_id FROM chart_of_accounts
    WHERE tenant_id = p_tenant_id AND code = '5-10000';

    INSERT INTO chart_of_accounts (tenant_id, code, name, type, normal_balance, parent_id, is_system, is_active) VALUES
    (p_tenant_id, '5-10100', 'HPP Barang Dagang', 'EXPENSE', 'DEBIT', v_parent_id, true, true),
    (p_tenant_id, '5-10200', 'Diskon Pembelian', 'EXPENSE', 'CREDIT', v_parent_id, false, true),
    (p_tenant_id, '5-10300', 'Retur Pembelian', 'EXPENSE', 'CREDIT', v_parent_id, false, true)
    ON CONFLICT (tenant_id, code) DO NOTHING;
    v_count := v_count + 3;

    -- Beban Operasional
    SELECT id INTO v_parent_id FROM chart_of_accounts
    WHERE tenant_id = p_tenant_id AND code = '5-00000';

    INSERT INTO chart_of_accounts (tenant_id, code, name, type, normal_balance, parent_id, is_active)
    VALUES (p_tenant_id, '5-20000', 'Beban Operasional', 'EXPENSE', 'DEBIT', v_parent_id, true)
    ON CONFLICT (tenant_id, code) DO NOTHING;
    v_count := v_count + 1;

    SELECT id INTO v_parent_id FROM chart_of_accounts
    WHERE tenant_id = p_tenant_id AND code = '5-20000';

    INSERT INTO chart_of_accounts (tenant_id, code, name, type, normal_balance, parent_id, is_active) VALUES
    (p_tenant_id, '5-20100', 'Beban Gaji', 'EXPENSE', 'DEBIT', v_parent_id, true),
    (p_tenant_id, '5-20200', 'Beban Sewa', 'EXPENSE', 'DEBIT', v_parent_id, true),
    (p_tenant_id, '5-20300', 'Beban Listrik & Air', 'EXPENSE', 'DEBIT', v_parent_id, true),
    (p_tenant_id, '5-20400', 'Beban Telepon & Internet', 'EXPENSE', 'DEBIT', v_parent_id, true),
    (p_tenant_id, '5-20500', 'Beban Pengiriman', 'EXPENSE', 'DEBIT', v_parent_id, true),
    (p_tenant_id, '5-20600', 'Beban Perlengkapan', 'EXPENSE', 'DEBIT', v_parent_id, true),
    (p_tenant_id, '5-20700', 'Beban Penyusutan', 'EXPENSE', 'DEBIT', v_parent_id, true),
    (p_tenant_id, '5-20800', 'Beban Administrasi', 'EXPENSE', 'DEBIT', v_parent_id, true),
    (p_tenant_id, '5-20900', 'Beban Lain-lain', 'EXPENSE', 'DEBIT', v_parent_id, true)
    ON CONFLICT (tenant_id, code) DO NOTHING;
    v_count := v_count + 9;

    -- Beban Non-Operasional
    SELECT id INTO v_parent_id FROM chart_of_accounts
    WHERE tenant_id = p_tenant_id AND code = '5-00000';

    INSERT INTO chart_of_accounts (tenant_id, code, name, type, normal_balance, parent_id, is_active)
    VALUES (p_tenant_id, '5-30000', 'Beban Non-Operasional', 'EXPENSE', 'DEBIT', v_parent_id, true)
    ON CONFLICT (tenant_id, code) DO NOTHING;
    v_count := v_count + 1;

    SELECT id INTO v_parent_id FROM chart_of_accounts
    WHERE tenant_id = p_tenant_id AND code = '5-30000';

    INSERT INTO chart_of_accounts (tenant_id, code, name, type, normal_balance, parent_id, is_active) VALUES
    (p_tenant_id, '5-30100', 'Beban Bunga', 'EXPENSE', 'DEBIT', v_parent_id, true),
    (p_tenant_id, '5-30200', 'Beban Pajak', 'EXPENSE', 'DEBIT', v_parent_id, true)
    ON CONFLICT (tenant_id, code) DO NOTHING;
    v_count := v_count + 2;

    RETURN v_count;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION seed_default_coa IS 'Seed default Chart of Accounts untuk tenant baru';


-- ============================================================================
-- Seed CoA for existing tenants
-- ============================================================================

DO $$
DECLARE
    v_tenant RECORD;
    v_count INT;
BEGIN
    -- Get all existing tenants
    FOR v_tenant IN
        SELECT DISTINCT tenant_id FROM "User" WHERE tenant_id IS NOT NULL
    LOOP
        -- Seed CoA for each tenant
        SELECT seed_default_coa(v_tenant.tenant_id::UUID) INTO v_count;
        RAISE NOTICE 'Seeded % accounts for tenant %', v_count, v_tenant.tenant_id;
    END LOOP;
END $$;


-- ============================================================================
-- MIGRATION COMPLETE
-- ============================================================================

DO $$
BEGIN
    RAISE NOTICE 'Migration V011: Default Chart of Accounts seeded successfully';
    RAISE NOTICE 'Total accounts per tenant: ~50 accounts (5 categories)';
    RAISE NOTICE 'Use seed_default_coa(tenant_id) for new tenants';
END $$;
