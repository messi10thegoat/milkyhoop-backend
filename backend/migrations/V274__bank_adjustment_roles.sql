-- V274__bank_adjustment_roles.sql
-- (1) Bank balance adjustment gain/loss roles. /adjust was disabled (400) since 15 Sep
-- because the old path posted a net-zero same-account journal. Re-enable it with proper
-- P&L contra accounts: surplus -> BANK_ADJUSTMENT_GAIN (grapgrap 4-90000 Pendapatan
-- Lain-lain), shortfall -> BANK_ADJUSTMENT_LOSS (grapgrap 5-20900 Beban Lain-lain).
-- Owner-picked accounts. Per-tenant mapping (grapgrap dogfood only).

-- Add the two role_keys to the CHECK allowlist by appending to the EXISTING definition
-- (avoids re-listing ~78 keys; idempotent).
DO $$
DECLARE cdef text;
BEGIN
    SELECT pg_get_constraintdef(oid) INTO cdef FROM pg_constraint
     WHERE conrelid = 'account_roles'::regclass
       AND conname = 'account_roles_role_key_check';
    IF cdef IS NOT NULL AND position('BANK_ADJUSTMENT_GAIN' in cdef) = 0 THEN
        EXECUTE 'ALTER TABLE account_roles DROP CONSTRAINT account_roles_role_key_check';
        cdef := replace(cdef, ']))',
                        ', ''BANK_ADJUSTMENT_GAIN''::text, ''BANK_ADJUSTMENT_LOSS''::text]))');
        EXECUTE 'ALTER TABLE account_roles ADD CONSTRAINT account_roles_role_key_check ' || cdef;
    END IF;
END $$;

-- Per-tenant mapping (grapgrap only).
INSERT INTO account_roles (tenant_id, role_key, account_id)
SELECT 'grapgrap-manado', 'BANK_ADJUSTMENT_GAIN', id
FROM chart_of_accounts WHERE tenant_id = 'grapgrap-manado' AND account_code = '4-90000'
ON CONFLICT (tenant_id, role_key) DO NOTHING;

INSERT INTO account_roles (tenant_id, role_key, account_id)
SELECT 'grapgrap-manado', 'BANK_ADJUSTMENT_LOSS', id
FROM chart_of_accounts WHERE tenant_id = 'grapgrap-manado' AND account_code = '5-20900'
ON CONFLICT (tenant_id, role_key) DO NOTHING;
