-- =============================================================================
-- V149 — Account Role Mapping Layer (Fase B)
-- =============================================================================
-- Purpose: introduce account_roles table — semantic role → CoA account_id
-- mapping per tenant. Infrastructure ONLY. Posting code unchanged in Fase B.
--
-- Source of truth: /root/milkyhoop-dev/docs/MAPPING-ROLE-AKUN-LOCKED.md
-- Seeds: TIER 1 + TIER 2 only. TIER 3 is PENDING — NOT seeded here.
-- VAT_OUTPUT → 2-10300 with is_interim=true (split in Fase D).
--
-- Iron Laws:
--   Law 18 — block mapping to is_header=true accounts (trigger)
--   Law 24 — RLS + FORCE on account_roles, tenant_isolation policy
--   Law 27 — runtime resolution via resolve_account_id_by_role (Python helper)
--   Law 32 — FK ON DELETE RESTRICT on account_id (protect posting)
-- =============================================================================

BEGIN;

-- -----------------------------------------------------------------------------
-- 1. account_roles table
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS account_roles (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    TEXT NOT NULL REFERENCES "Tenant"(id) ON DELETE CASCADE,
    role_key     TEXT NOT NULL,
    account_id   UUID NOT NULL REFERENCES chart_of_accounts(id) ON DELETE RESTRICT,
    is_interim   BOOLEAN NOT NULL DEFAULT false,
    notes        TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT account_roles_unique UNIQUE (tenant_id, role_key),
    CONSTRAINT account_roles_role_key_check CHECK (role_key IN (
        -- TIER 1 (CONFIRMED, seeded)
        'CASH_GENERAL',
        'BANK_OPERATIONAL',
        'AR_TRADE',
        'AR_OTHER',
        'INVENTORY_MERCHANDISE',
        'AP_TRADE',
        'CUSTOMER_DEPOSIT_LIABILITY',
        'EQUITY_OPENING_BALANCE',
        'REVENUE_SALES_GOODS',
        'REVENUE_SALES_RETURN',
        'COGS_SALES',
        'COGS_PURCHASE_RETURN',
        -- TIER 2 (CORRECTED, seeded)
        'CASH_PETTY',
        -- TIER 2 INTERIM (seeded with is_interim=true)
        'VAT_OUTPUT',
        -- TIER 3 (PENDING — catalog reservation, NOT seeded in Fase B)
        'VAT_INPUT',
        'VAT_INPUT_NONCREDITABLE',
        'VAT_PAYABLE_NET',
        'WHT_PPH21',
        'WHT_PPH23',
        'WHT_PPH4_2',
        'WHT_PPH22',
        'ACCUMULATED_DEPRECIATION',
        'IC_SALES',
        'BRANCH_AR',
        'BRANCH_AP',
        -- FUTURE RESERVATION (not seeded, allowed for forward-compat mapping)
        'AR_ALLOWANCE',
        'AP_ACCRUED',
        'INVENTORY_RAW',
        'INVENTORY_WIP',
        'INVENTORY_FINISHED',
        'INVENTORY_PACKAGING',
        'INVENTORY_WRITEOFF_DAMAGE',
        'INVENTORY_WRITEOFF_EXPIRED',
        'INVENTORY_WRITEOFF_SHRINKAGE',
        'INVENTORY_RECALL_LOSS',
        'MFG_DIRECT_LABOR',
        'MFG_OVERHEAD_INDIRECT_MATERIAL',
        'MFG_OVERHEAD_INDIRECT_LABOR',
        'MFG_OVERHEAD_UTILITIES',
        'MFG_OVERHEAD_DEPRECIATION',
        'MFG_OVERHEAD_APPLIED',
        'REVENUE_SALES_SERVICE',
        'REVENUE_SALES_DISCOUNT',
        'REVENUE_DEFERRED',
        'REVENUE_UNBILLED',
        'COGS_PRODUCTION',
        'COGS_SERVICE',
        'COGS_VARIANCE_MATERIAL',
        'COGS_VARIANCE_LABOR',
        'COGS_VARIANCE_OVERHEAD',
        'CURRENCY_GAIN',
        'CURRENCY_LOSS',
        'CURRENCY_UNREALIZED_FX'
    ))
);

CREATE INDEX IF NOT EXISTS idx_account_roles_tenant      ON account_roles(tenant_id);
CREATE INDEX IF NOT EXISTS idx_account_roles_tenant_role ON account_roles(tenant_id, role_key);
CREATE INDEX IF NOT EXISTS idx_account_roles_account     ON account_roles(account_id);

-- -----------------------------------------------------------------------------
-- 2. updated_at trigger
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION account_roles_set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at := NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_account_roles_updated_at ON account_roles;
CREATE TRIGGER trg_account_roles_updated_at
    BEFORE UPDATE ON account_roles
    FOR EACH ROW
    EXECUTE FUNCTION account_roles_set_updated_at();

-- -----------------------------------------------------------------------------
-- 3. Law 18 guard — block mapping to is_header=true accounts
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION account_roles_block_header_account()
RETURNS TRIGGER AS $$
DECLARE
    v_is_header BOOLEAN;
    v_tenant    TEXT;
    v_code      TEXT;
BEGIN
    SELECT is_header, tenant_id, account_code
      INTO v_is_header, v_tenant, v_code
      FROM chart_of_accounts
     WHERE id = NEW.account_id;

    IF v_is_header IS NULL THEN
        RAISE EXCEPTION 'account_roles: account_id % not found in chart_of_accounts', NEW.account_id;
    END IF;

    IF v_tenant <> NEW.tenant_id THEN
        RAISE EXCEPTION 'account_roles: tenant_id mismatch — role tenant=%, account tenant=% (code=%)',
            NEW.tenant_id, v_tenant, v_code;
    END IF;

    IF v_is_header THEN
        RAISE EXCEPTION 'account_roles: cannot map role_key=% to header account (code=%, Law 18)',
            NEW.role_key, v_code;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_account_roles_block_header ON account_roles;
CREATE TRIGGER trg_account_roles_block_header
    BEFORE INSERT OR UPDATE ON account_roles
    FOR EACH ROW
    EXECUTE FUNCTION account_roles_block_header_account();

-- -----------------------------------------------------------------------------
-- 4. RLS (Law 24)
-- -----------------------------------------------------------------------------
ALTER TABLE account_roles ENABLE ROW LEVEL SECURITY;
ALTER TABLE account_roles FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS rls_account_roles ON account_roles;
CREATE POLICY rls_account_roles ON account_roles
    USING ((tenant_id)::text = current_setting('app.tenant_id'::text, true))
    WITH CHECK ((tenant_id)::text = current_setting('app.tenant_id'::text, true));

-- -----------------------------------------------------------------------------
-- 5. Idempotent seed function — TIER 1 + TIER 2 only
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION seed_default_account_roles(p_tenant_id TEXT)
RETURNS INTEGER AS $$
DECLARE
    v_inserted INTEGER := 0;
    v_account_id UUID;
    v_is_header  BOOLEAN;
    -- Mapping: role_key, account_code, is_interim, notes
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

-- -----------------------------------------------------------------------------
-- 6. Backfill — seed all existing tenants
-- -----------------------------------------------------------------------------
DO $$
DECLARE
    v_tenant RECORD;
    v_count  INTEGER;
BEGIN
    FOR v_tenant IN SELECT id FROM "Tenant" LOOP
        v_count := seed_default_account_roles(v_tenant.id);
        RAISE NOTICE 'Backfill tenant=%: inserted % role mappings', v_tenant.id, v_count;
    END LOOP;
END $$;

COMMIT;
