-- V285: borongan / piece-rate payroll. The rate belongs to the JOB, not the employee, so
-- borongan needs AD-HOC LINES per run (description + manual job_reference + qty + rate),
-- repeatable N per employee -- unlike the per-employee component rate (V282). Each line
-- carries an OPTIONAL work_order_id so the wage can later land in production cost / HPP
-- WITHOUT a rewrite; today it posts Dr Beban Gaji like any wage (manufaktur route optional).
CREATE TABLE IF NOT EXISTS payroll_run_piece_lines (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id text NOT NULL,
    payroll_id uuid NOT NULL,
    employee_id uuid NOT NULL,
    description text NOT NULL,
    job_reference text,                 -- manual free-text job number (e.g. 006-09-26)
    work_order_id uuid,                 -- optional future Manufaktur link (production_orders.id); NOT FK-enforced yet
    quantity numeric(18,4) NOT NULL,
    rate numeric(18,2) NOT NULL,
    sort_order int NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_payroll_piece_run_emp ON payroll_run_piece_lines(payroll_id, employee_id);

-- posted slip lines carry the job link too (so per-model cost is recoverable later).
ALTER TABLE payroll_slip_lines ADD COLUMN IF NOT EXISTS job_reference text;
ALTER TABLE payroll_slip_lines ADD COLUMN IF NOT EXISTS work_order_id uuid;
