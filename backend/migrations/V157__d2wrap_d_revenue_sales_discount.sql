-- =============================================================================
-- V157__d2wrap_d_revenue_sales_discount.sql
-- =============================================================================
-- Fase D2-wrap (D): seed REVENUE_SALES_DISCOUNT role mapping → 4-10200
--                   "Diskon Penjualan" (REVENUE, contra-revenue).
--
-- Context: receive_payments.py:1602 used hardcoded fallback literal '6-10100'
-- which does NOT exist in any tenant CoA (verified 5/5). This was effectively
-- a silent-skip bug: when discount_amount > 0 and discount_account_id not
-- user-picked, resolve_account_id('6-10100') raised ValueError, discount line
-- was dropped (Law 4 risk on journal balance).
--
-- Verified pre-flight:
--   SELECT tenant_id, account_code, name FROM chart_of_accounts
--    WHERE account_code='4-10200';
--   → 5/5 tenants: anthonius-iwan, grapgrap, milkytest, ponte-publishing,
--     potus-id  (name='Diskon Penjualan', account_type=REVENUE)
--
-- Role 'REVENUE_SALES_DISCOUNT' already in account_roles CHECK constraint
-- (V149:81, V155:89) and in AccountRole class
-- (role_resolver.py:109,176). Catalog OK — only seed missing.
--
-- This migration:
--   1. Updates seed_default_account_roles() to include REVENUE_SALES_DISCOUNT
--      → '4-10200' for fresh tenant onboarding.
--   2. Backfills the mapping for all 5 existing tenants (ON CONFLICT DO NOTHING
--      → idempotent).
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 1. CREATE OR REPLACE seed_default_account_roles() — add REVENUE_SALES_DISCOUNT
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION seed_default_account_roles(p_tenant_id TEXT)
RETURNS INTEGER AS $$
DECLARE
    v_inserted INTEGER := 0;
    v_account_id UUID;
    v_is_header  BOOLEAN;
    v_map RECORD;
BEGIN
    FOR v_map IN
        SELECT * FROM (VALUES
            -- TIER 1
            ('CASH_GENERAL',               '1-10100', false, NULL::TEXT),
            ('BANK_OPERATIONAL',           '1-10200', false, NULL),
            ('AR_TRADE',                   '1-10400', false, NULL),
            ('AR_OTHER',                   '1-10500', false, NULL),
            ('INVENTORY_MERCHANDISE',      '1-10600', false, NULL),
            ('AP_TRADE',                   '2-10100', false, NULL),
            ('CUSTOMER_DEPOSIT_LIABILITY', '2-10500', false, NULL),
            ('REVENUE_DEFERRED',           '2-10750', false, 'V152 promote: PSAK 72 contract liability — billing event credits this; revenue event debits this.'),
            ('EQUITY_OPENING_BALANCE',     '3-50000', false, NULL),
            ('REVENUE_SALES_GOODS',        '4-10100', false, NULL),
            ('REVENUE_SALES_RETURN',       '4-10300', false, NULL),
            ('COGS_SALES',                 '5-10100', false, NULL),
            ('COGS_PURCHASE_RETURN',       '5-10300', false, NULL),
            -- TIER 2
            ('CASH_PETTY',                 '1-10300', false, 'Kas Kecil (corrected from agen inventaris error)'),
            -- V155 TIER 1 promote (tax split Fase D1)
            ('VAT_OUTPUT',                 '2-10600', false, 'V155 Fase D1: repointed from interim 2-10300 to dedicated PPN Keluaran (LIABILITY).'),
            ('VAT_INPUT',                  '1-10800', false, 'V155 Fase D1: dedicated PPN Masukan (ASSET).'),
            ('WHT_PPH_PAYABLE',            '2-10320', false, 'V155 Fase D1: PPh 23/22/4(2) AP-transaction withholding (LIABILITY). NOT payroll — 2-10310 stays payroll-exclusive.'),
            ('WHT_PPH_PREPAID',            '1-10820', false, 'V155 Fase D1: PPh Dibayar Dimuka (ASSET) — customer withholding from our income = tax credit.'),
            -- V157 TIER 1 promote (Fase D2-wrap D)
            ('REVENUE_SALES_DISCOUNT',     '4-10200', false, 'V157 Fase D2-wrap D: Diskon Penjualan (contra-revenue). Flips receive_payments.py:1602 hardcoded fallback (was non-existent literal 6-10100).')
        ) AS t(role_key, account_code, is_interim, notes)
    LOOP
        SELECT id, is_header INTO v_account_id, v_is_header
          FROM chart_of_accounts
         WHERE tenant_id = p_tenant_id
           AND account_code = v_map.account_code;

        IF v_account_id IS NULL THEN
            RAISE NOTICE 'seed_default_account_roles[%]: skipping role % — account_code % not found',
                p_tenant_id, v_map.role_key, v_map.account_code;
            CONTINUE;
        END IF;

        IF v_is_header THEN
            RAISE NOTICE 'seed_default_account_roles[%]: skipping role % — account_code % is_header=true (Law 18)',
                p_tenant_id, v_map.role_key, v_map.account_code;
            CONTINUE;
        END IF;

        INSERT INTO account_roles (tenant_id, role_key, account_id, is_interim, notes)
        VALUES (p_tenant_id, v_map.role_key, v_account_id, v_map.is_interim, v_map.notes)
        ON CONFLICT (tenant_id, role_key) DO NOTHING;

        IF FOUND THEN
            v_inserted := v_inserted + 1;
        END IF;
    END LOOP;

    RETURN v_inserted;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION seed_default_account_roles(text) IS
    'Idempotently seed TIER 1 + TIER 2 account_roles mappings. V152: REVENUE_DEFERRED. V155 Fase D1: VAT_OUTPUT repoint to 2-10600 + VAT_INPUT + WHT_PPH_PAYABLE + WHT_PPH_PREPAID. V157 Fase D2-wrap D: REVENUE_SALES_DISCOUNT.';

-- -----------------------------------------------------------------------------
-- 2. Backfill existing tenants
-- -----------------------------------------------------------------------------
DO $$
DECLARE
    v_tenant RECORD;
    v_account_id UUID;
    v_is_header  BOOLEAN;
    v_inserted   INTEGER := 0;
BEGIN
    FOR v_tenant IN SELECT id FROM "Tenant" LOOP
        SELECT id, is_header INTO v_account_id, v_is_header
          FROM chart_of_accounts
         WHERE tenant_id = v_tenant.id
           AND account_code = '4-10200';

        IF v_account_id IS NULL THEN
            RAISE NOTICE 'V157 backfill tenant=%: account_code 4-10200 not found — skipping', v_tenant.id;
            CONTINUE;
        END IF;

        IF v_is_header THEN
            RAISE NOTICE 'V157 backfill tenant=%: account_code 4-10200 is_header=true — skipping (Law 18)', v_tenant.id;
            CONTINUE;
        END IF;

        INSERT INTO account_roles (tenant_id, role_key, account_id, is_interim, notes)
        VALUES (
            v_tenant.id,
            'REVENUE_SALES_DISCOUNT',
            v_account_id,
            false,
            'V157 Fase D2-wrap D: Diskon Penjualan (contra-revenue). Flips receive_payments.py:1602 hardcoded fallback (was non-existent literal 6-10100).'
        )
        ON CONFLICT (tenant_id, role_key) DO NOTHING;

        IF FOUND THEN
            v_inserted := v_inserted + 1;
            RAISE NOTICE 'V157 backfill tenant=%: REVENUE_SALES_DISCOUNT -> 4-10200 seeded', v_tenant.id;
        ELSE
            RAISE NOTICE 'V157 backfill tenant=%: REVENUE_SALES_DISCOUNT already mapped — skipping', v_tenant.id;
        END IF;
    END LOOP;

    RAISE NOTICE 'V157: % new REVENUE_SALES_DISCOUNT mappings inserted', v_inserted;
END $$;
