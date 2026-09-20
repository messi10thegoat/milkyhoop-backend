-- V281: Employee advances (KASBON) — foundation.
--
-- Models employee cash advances the owner runs on their payroll sheet: a principal
-- granted, deducted a bit each payroll run, with a running remaining balance (SISA)
-- carried across months. Multiple concurrent advances per employee are supported; a
-- run's deduction is applied FIFO (oldest granted_date first), cascading to the next
-- advance if it exceeds the oldest's remaining (decided here; endpoints enforce it).
--
-- BALANCE IS DERIVED, never stored: remaining = SUM(employee_advance_movements.amount).
-- Sign convention (matches the GL asset EMPLOYEE_ADVANCE, debit-normal):
--   grant     -> +principal  (Dr EMPLOYEE_ADVANCE / Cr cash)
--   deduction -> -amount     (Cr EMPLOYEE_ADVANCE, an extra leg on the PAYROLL run journal)
--   reversal  -> opposite sign of the movement it reverses
-- So SUM(movements) == GL net (debit-credit) of the EMPLOYEE_ADVANCE account — the
-- reconciliation invariant enforced by verify_employee_advance_reconciliation_all().
--
-- employee_advance_movements is APPEND-ONLY, exactly like inventory_ledger: NO UPDATE,
-- NO DELETE. Corrections are NEW movements (a reversal), never an edit. A trigger
-- enforces this so a future session does not "fix" a row and silently break the ledger.

-- 1) New CoA role_key EMPLOYEE_ADVANCE (asset). Extend the enumerated CHECK (V274 pattern).
DO $$
DECLARE cdef text;
BEGIN
    SELECT pg_get_constraintdef(oid) INTO cdef
    FROM pg_constraint WHERE conname = 'account_roles_role_key_check' AND conrelid = 'account_roles'::regclass;
    IF cdef IS NULL THEN RAISE EXCEPTION 'account_roles_role_key_check not found'; END IF;
    IF position('EMPLOYEE_ADVANCE' IN cdef) = 0 THEN
        cdef := replace(cdef, ']))', ', ''EMPLOYEE_ADVANCE''::text]))');
        EXECUTE 'ALTER TABLE account_roles DROP CONSTRAINT account_roles_role_key_check';
        EXECUTE 'ALTER TABLE account_roles ADD CONSTRAINT account_roles_role_key_check ' || cdef;
    END IF;
END $$;

-- 2) Register journal source types (Law 6, FK-enforced).
INSERT INTO journal_source_types (source_type, description, is_active) VALUES
    ('EMPLOYEE_ADVANCE_GRANT', 'Kasbon: pemberian uang muka karyawan (Dr Piutang Karyawan / Cr Kas-Bank)', true),
    ('EMPLOYEE_ADVANCE_GRANT_REVERSAL', 'Kasbon: pembalikan pemberian uang muka karyawan', true)
ON CONFLICT (source_type) DO NOTHING;

-- 3) Tables.
CREATE TABLE IF NOT EXISTS employee_advances (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id text NOT NULL,
    employee_id uuid NOT NULL,
    principal numeric(18,2) NOT NULL CHECK (principal > 0),
    granted_date date NOT NULL,
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('active','settled','void')),
    grant_journal_id uuid,
    source_account_id uuid,          -- the cash/bank CoA the grant was paid from
    notes text,
    created_at timestamptz NOT NULL DEFAULT now(),
    created_by uuid,
    voided_at timestamptz,
    voided_by uuid,
    void_reason text
);
CREATE INDEX IF NOT EXISTS idx_emp_adv_tenant_emp ON employee_advances(tenant_id, employee_id, status);

-- APPEND-ONLY ledger (see header). No UPDATE, no DELETE — corrections are new movements.
CREATE TABLE IF NOT EXISTS employee_advance_movements (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id text NOT NULL,
    advance_id uuid NOT NULL REFERENCES employee_advances(id),
    employee_id uuid NOT NULL,
    movement_type text NOT NULL CHECK (movement_type IN ('grant','deduction','reversal')),
    amount numeric(18,2) NOT NULL,
    payroll_id uuid,
    journal_id uuid,
    reverses_movement_id uuid REFERENCES employee_advance_movements(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    created_by uuid
);
CREATE INDEX IF NOT EXISTS idx_emp_adv_mov_advance ON employee_advance_movements(advance_id);
CREATE INDEX IF NOT EXISTS idx_emp_adv_mov_tenant_emp ON employee_advance_movements(tenant_id, employee_id);

-- 4) Append-only enforcement.
CREATE OR REPLACE FUNCTION guard_emp_adv_mov_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'employee_advance_movements is append-only (like inventory_ledger): no UPDATE/DELETE; corrections are new movements';
END; $$;
DROP TRIGGER IF EXISTS trg_emp_adv_mov_append_only ON employee_advance_movements;
CREATE TRIGGER trg_emp_adv_mov_append_only
    BEFORE UPDATE OR DELETE ON employee_advance_movements
    FOR EACH ROW EXECUTE FUNCTION guard_emp_adv_mov_append_only();

-- 5) Derived-balance helpers (remaining = SUM of signed movements).
CREATE OR REPLACE FUNCTION employee_advance_balance(p_advance_id uuid)
RETURNS numeric LANGUAGE sql STABLE AS $$
    SELECT COALESCE(SUM(amount), 0)::numeric(18,2)
    FROM employee_advance_movements WHERE advance_id = p_advance_id;
$$;

CREATE OR REPLACE FUNCTION employee_advance_balance_by_employee(p_tenant text, p_employee uuid)
RETURNS numeric LANGUAGE sql STABLE AS $$
    SELECT COALESCE(SUM(amount), 0)::numeric(18,2)
    FROM employee_advance_movements WHERE tenant_id = p_tenant AND employee_id = p_employee;
$$;

-- 6) Reconciliation invariant: per tenant, SUM(movements) == GL net of the EMPLOYEE_ADVANCE
--    role account (POSTED, is_effective). Mirrors check_15/16/17. Can go RED (a movement
--    without its journal leg, or vice versa).
CREATE OR REPLACE FUNCTION verify_employee_advance_reconciliation_all()
RETURNS TABLE(tenant_id text, movements_total numeric, gl_value numeric, drift numeric, verdict text)
LANGUAGE plpgsql STABLE AS $$
BEGIN
    IF NOT COALESCE((SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user), false)
       AND (current_setting('app.tenant_id', true) IS NULL OR current_setting('app.tenant_id', true) = '')
    THEN
        RAISE EXCEPTION 'verify_employee_advance_reconciliation_all: no tenant scope (not BYPASSRLS, app.tenant_id unset) -- refusing to return false-GREEN';
    END IF;
    RETURN QUERY
    WITH tenants AS (
        SELECT DISTINCT m.tenant_id AS tid FROM employee_advance_movements m
        UNION
        SELECT DISTINCT ar.tenant_id FROM account_roles ar WHERE ar.role_key = 'EMPLOYEE_ADVANCE'
    ),
    mov AS (
        SELECT t.tid, COALESCE(SUM(m.amount), 0)::numeric(18,2) AS mv
        FROM tenants t LEFT JOIN employee_advance_movements m ON m.tenant_id = t.tid
        GROUP BY t.tid
    ),
    gl AS (
        SELECT je.tenant_id AS tid, COALESCE(SUM(jl.debit - jl.credit), 0)::numeric(18,2) AS glv
        FROM journal_entries je
        JOIN journal_lines jl ON jl.journal_id = je.id
        JOIN account_roles ar ON ar.tenant_id = je.tenant_id AND ar.role_key = 'EMPLOYEE_ADVANCE' AND ar.account_id = jl.account_id
        WHERE is_effective_journal(je.id)
        GROUP BY je.tenant_id
    ),
    per AS (
        SELECT t.tid,
               COALESCE(m.mv, 0)::numeric(18,2) AS mv,
               COALESCE(g.glv, 0)::numeric(18,2) AS glv,
               (COALESCE(m.mv, 0) - COALESCE(g.glv, 0))::numeric(18,2) AS drift
        FROM tenants t LEFT JOIN mov m ON m.tid = t.tid LEFT JOIN gl g ON g.tid = t.tid
    )
    SELECT p.tid, p.mv, p.glv, p.drift,
           CASE WHEN ABS(p.drift) <= 0.01 THEN 'PASS' ELSE 'FAIL' END
    FROM per p
    ORDER BY (ABS(p.drift) > 0.01) DESC, p.tid;
END; $$;

-- 7) grapgrap (dogfood) CoA account + role mapping. Other tenants map via the app / 422 when unmapped.
INSERT INTO chart_of_accounts (id, tenant_id, account_code, name, account_type, normal_balance, level, is_header, is_cash)
SELECT gen_random_uuid(), 'grapgrap-manado', '1-10450', 'Piutang Karyawan (Kasbon)', 'ASSET', 'DEBIT',
       (SELECT level FROM chart_of_accounts WHERE tenant_id = 'grapgrap-manado' AND account_code = '1-10500'),
       false, false
WHERE NOT EXISTS (SELECT 1 FROM chart_of_accounts WHERE tenant_id = 'grapgrap-manado' AND account_code = '1-10450');

INSERT INTO account_roles (id, tenant_id, role_key, account_id, is_interim, notes)
SELECT gen_random_uuid(), 'grapgrap-manado', 'EMPLOYEE_ADVANCE',
       (SELECT id FROM chart_of_accounts WHERE tenant_id = 'grapgrap-manado' AND account_code = '1-10450'),
       false, 'Kasbon / uang muka karyawan (V281)'
WHERE NOT EXISTS (SELECT 1 FROM account_roles WHERE tenant_id = 'grapgrap-manado' AND role_key = 'EMPLOYEE_ADVANCE');
