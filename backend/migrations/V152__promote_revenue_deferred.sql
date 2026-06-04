-- =============================================================================
-- V152 — Promote REVENUE_DEFERRED to TIER 1 (account_roles mapping)
-- =============================================================================
-- Purpose (Fase C1.1 addendum):
--   REVENUE_DEFERRED is the core contract liability for the 3-event PSAK 72
--   model (V137). Owner decision: promote from FUTURE RESERVATION to TIER 1
--   so sales_invoices.py can resolve it via role_resolver instead of the
--   hardcoded '2-10750' literal.
--
--   This migration:
--     1. Patches seed_default_account_roles() to include the
--        REVENUE_DEFERRED -> 2-10750 mapping for new tenants.
--     2. Re-runs seed_default_account_roles() per existing tenant to backfill
--        the new mapping (idempotent — ON CONFLICT DO NOTHING).
--
--   Pairs with V151 which guarantees 2-10750 exists in every tenant CoA.
--
-- Scope:
--   - NO posting code touched (sales_invoices.py edit is in a separate commit).
--   - Idempotent (re-runnable).
--   - Law 18 honored (2-10750 is a leaf, is_header=false).
--   - Law 27 honored (role-based resolution; no UUID hardcoding).
--
-- Source of truth: /root/milkyhoop-dev/docs/MAPPING-ROLE-AKUN-LOCKED.md
-- =============================================================================

BEGIN;

-- -----------------------------------------------------------------------------
-- 1. Patch seed_default_account_roles() to include REVENUE_DEFERRED in TIER 1
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
            -- TIER 2 INTERIM
            ('VAT_OUTPUT',                 '2-10300', true,  'Interim Fase B — split di Fase D (currently shared with WHT_PPH on 2-10300)')
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
    'Idempotently seed TIER 1 + TIER 2 account_roles mappings. V152: added REVENUE_DEFERRED -> 2-10750 (PSAK 72 contract liability).';

-- -----------------------------------------------------------------------------
-- 2. Backfill REVENUE_DEFERRED mapping for all existing tenants
-- -----------------------------------------------------------------------------
DO $$
DECLARE
    v_tenant RECORD;
    v_n      INTEGER;
BEGIN
    FOR v_tenant IN SELECT id FROM "Tenant" LOOP
        v_n := seed_default_account_roles(v_tenant.id);
        RAISE NOTICE 'V152 backfill tenant=%: % new role mappings', v_tenant.id, v_n;
    END LOOP;
END $$;

COMMIT;
