-- V283: payroll run roster. The run's employee set was never persisted -- calculate
-- inferred it from the slip lines seeded at create-time, which only covered is_fixed=true
-- earnings. A worker paid PURELY by daily/hourly rate (no fixed component) got no seed line
-- and was SILENTLY DROPPED from the run -- exactly the harian/borongan case the quantity x
-- rate feature (V282) was built for. Persist the roster so calculate sources its employees
-- from who was actually requested, and can name anyone who yields no lines (fail loud).
CREATE TABLE IF NOT EXISTS payroll_run_employees (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id text NOT NULL,
    payroll_id uuid NOT NULL,
    employee_id uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (payroll_id, employee_id)
);
CREATE INDEX IF NOT EXISTS idx_payroll_run_employees_run ON payroll_run_employees(payroll_id);

-- Backfill existing runs from their current slip lines so recompute keeps working
-- (runs created before V283 have no roster rows otherwise).
INSERT INTO payroll_run_employees (tenant_id, payroll_id, employee_id)
SELECT DISTINCT tenant_id, payroll_id, employee_id FROM payroll_slip_lines
ON CONFLICT (payroll_id, employee_id) DO NOTHING;
