-- V282: quantity x rate payroll. Slip lines expose quantity + rate (not just the product);
-- payroll_run_inputs holds per-run per-employee quantities (days_worked / overtime_hours /
-- flat amount) so calculate can compute rate x quantity reproducibly. calculation_method on
-- salary_components is the switch: 'daily' (rate x days), 'hourly'/'overtime' (rate x hours),
-- 'fixed' (flat). A daily/overtime component with a MISSING quantity fails loud (handler 400),
-- never silently 0. All math is Decimal/HALF_UP in the service.
ALTER TABLE payroll_slip_lines ADD COLUMN IF NOT EXISTS quantity numeric(18,4);
ALTER TABLE payroll_slip_lines ADD COLUMN IF NOT EXISTS rate numeric(18,2);

CREATE TABLE IF NOT EXISTS payroll_run_inputs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id text NOT NULL,
    payroll_id uuid NOT NULL,
    employee_id uuid NOT NULL,
    component_id uuid NOT NULL,
    days_worked numeric(18,4),
    overtime_hours numeric(18,4),
    amount numeric(18,2),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (payroll_id, employee_id, component_id)
);
CREATE INDEX IF NOT EXISTS idx_payroll_run_inputs_run ON payroll_run_inputs(payroll_id);
