-- V286: per-component payroll expense-account mapping (the "works without Manufaktur" HPP path).
-- Each salary component may point its earning at a CoA ROLE (Law 27); NULL = the current
-- SALARY_EXPENSE default, so nothing changes for a tenant that never configures it (same shape
-- as the item account override). New role PRODUCTION_WAGE_EXPENSE for direct sewing labour so a
-- production wage lands in the COGS/HPP group (above gross profit) instead of Beban Gaji.
ALTER TABLE salary_components ADD COLUMN IF NOT EXISTS expense_role text;

-- Register PRODUCTION_WAGE_EXPENSE in the account_roles CHECK (V274 dynamic-replace pattern).
DO $$
DECLARE cdef text;
BEGIN
    SELECT pg_get_constraintdef(oid) INTO cdef FROM pg_constraint
     WHERE conname='account_roles_role_key_check' AND conrelid='account_roles'::regclass;
    IF cdef IS NULL THEN RAISE EXCEPTION 'account_roles_role_key_check not found'; END IF;
    IF position('PRODUCTION_WAGE_EXPENSE' IN cdef) = 0 THEN
        cdef := replace(cdef, ']))', ', ''PRODUCTION_WAGE_EXPENSE''::text]))');
        EXECUTE 'ALTER TABLE account_roles DROP CONSTRAINT account_roles_role_key_check';
        EXECUTE 'ALTER TABLE account_roles ADD CONSTRAINT account_roles_role_key_check ' || cdef;
    END IF;
END $$;

-- grapgrap: map PRODUCTION_WAGE_EXPENSE -> existing unused COGS direct-labour account
-- 5-10020 "Biaya Tenaga Kerja Langsung" (0 journal lines, no role) -- no redundant account created.
INSERT INTO account_roles (id, tenant_id, role_key, account_id, is_interim, notes)
SELECT gen_random_uuid(), 'grapgrap-manado', 'PRODUCTION_WAGE_EXPENSE',
       (SELECT id FROM chart_of_accounts WHERE tenant_id='grapgrap-manado' AND account_code='5-10020'),
       false, 'Upah produksi (borongan/harian jahit) -> COGS/HPP (V286)'
WHERE NOT EXISTS (SELECT 1 FROM account_roles WHERE tenant_id='grapgrap-manado' AND role_key='PRODUCTION_WAGE_EXPENSE');

-- grapgrap: point the production wage components at it (office/admin components stay NULL = Beban Gaji).
UPDATE salary_components SET expense_role='PRODUCTION_WAGE_EXPENSE'
 WHERE tenant_id='grapgrap-manado' AND code IN ('UPAH-HARIAN','LEMBUR-JAM','LEMBUR-LIBUR');
