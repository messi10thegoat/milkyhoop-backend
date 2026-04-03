-- V129: Payroll Module Foundation
-- Expand employees, new tables, triggers, seed data, RLS
-- Date: 2026-03-30

-- ============================================================================
-- 1. EXPAND employees TABLE
-- ============================================================================

ALTER TABLE employees ADD COLUMN IF NOT EXISTS nik VARCHAR(16);
ALTER TABLE employees ADD COLUMN IF NOT EXISTS npwp VARCHAR(20);
ALTER TABLE employees ADD COLUMN IF NOT EXISTS ptkp_status VARCHAR(4) NOT NULL DEFAULT 'TK0';
ALTER TABLE employees ADD COLUMN IF NOT EXISTS tax_method VARCHAR(10) NOT NULL DEFAULT 'gross';
ALTER TABLE employees ADD COLUMN IF NOT EXISTS marital_status VARCHAR(10);
ALTER TABLE employees ADD COLUMN IF NOT EXISTS date_of_birth DATE;
ALTER TABLE employees ADD COLUMN IF NOT EXISTS gender VARCHAR(1);
ALTER TABLE employees ADD COLUMN IF NOT EXISTS religion VARCHAR(20);
ALTER TABLE employees ADD COLUMN IF NOT EXISTS join_date DATE;
ALTER TABLE employees ADD COLUMN IF NOT EXISTS resign_date DATE;
ALTER TABLE employees ADD COLUMN IF NOT EXISTS employee_type VARCHAR(12) NOT NULL DEFAULT 'tetap';
ALTER TABLE employees ADD COLUMN IF NOT EXISTS bpjs_kes_number VARCHAR(20);
ALTER TABLE employees ADD COLUMN IF NOT EXISTS bpjs_tk_number VARCHAR(20);
ALTER TABLE employees ADD COLUMN IF NOT EXISTS bank_name VARCHAR(50);
ALTER TABLE employees ADD COLUMN IF NOT EXISTS bank_account_number VARCHAR(30);
ALTER TABLE employees ADD COLUMN IF NOT EXISTS bank_account_name VARCHAR(100);
ALTER TABLE employees ADD COLUMN IF NOT EXISTS jkk_risk_level SMALLINT NOT NULL DEFAULT 1;
ALTER TABLE employees ADD COLUMN IF NOT EXISTS is_bpjs_kes BOOLEAN NOT NULL DEFAULT true;
ALTER TABLE employees ADD COLUMN IF NOT EXISTS is_bpjs_jht BOOLEAN NOT NULL DEFAULT true;
ALTER TABLE employees ADD COLUMN IF NOT EXISTS is_bpjs_jp BOOLEAN NOT NULL DEFAULT true;
ALTER TABLE employees ADD COLUMN IF NOT EXISTS phone VARCHAR(20);
ALTER TABLE employees ADD COLUMN IF NOT EXISTS address TEXT;

DO $$ BEGIN
  ALTER TABLE employees ADD CONSTRAINT chk_employees_ptkp
    CHECK (ptkp_status IN ('TK0','TK1','TK2','TK3','K0','K1','K2','K3','KI0','KI1','KI2','KI3'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
  ALTER TABLE employees ADD CONSTRAINT chk_employees_tax_method
    CHECK (tax_method IN ('gross', 'nett'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
  ALTER TABLE employees ADD CONSTRAINT chk_employees_type
    CHECK (employee_type IN ('tetap', 'tidak_tetap'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
  ALTER TABLE employees ADD CONSTRAINT chk_employees_jkk
    CHECK (jkk_risk_level BETWEEN 1 AND 5);
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- ============================================================================
-- 2. salary_components TABLE
-- ============================================================================

CREATE TABLE IF NOT EXISTS salary_components (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id         TEXT NOT NULL,
  code              VARCHAR(30) NOT NULL,
  name              VARCHAR(100) NOT NULL,
  type              VARCHAR(15) NOT NULL CHECK (type IN ('earning', 'deduction', 'employer_cost')),
  category          VARCHAR(30) NOT NULL,
  is_taxable        BOOLEAN NOT NULL DEFAULT true,
  is_fixed          BOOLEAN NOT NULL DEFAULT true,
  default_amount    NUMERIC(18,2) DEFAULT 0,
  calculation_method VARCHAR(10) NOT NULL DEFAULT 'fixed' CHECK (calculation_method IN ('fixed', 'percentage')),
  percentage_base   VARCHAR(30),
  sort_order        INTEGER NOT NULL DEFAULT 0,
  is_active         BOOLEAN NOT NULL DEFAULT true,
  is_system         BOOLEAN NOT NULL DEFAULT false,
  created_at        TIMESTAMPTZ DEFAULT now(),
  updated_at        TIMESTAMPTZ DEFAULT now(),
  UNIQUE(tenant_id, code)
);

-- Trigger: protect system salary components (DB-level enforcement)
CREATE OR REPLACE FUNCTION trg_protect_system_salary_components()
RETURNS TRIGGER AS $$
BEGIN
  IF TG_OP = 'DELETE' AND OLD.is_system = true THEN
    RAISE EXCEPTION 'Cannot delete system salary component: %', OLD.code;
  END IF;
  IF TG_OP = 'UPDATE' AND OLD.is_system = true THEN
    IF NEW.code != OLD.code OR NEW.type != OLD.type
       OR NEW.category != OLD.category OR NEW.is_system != OLD.is_system THEN
      RAISE EXCEPTION 'Cannot modify protected fields on system salary component: %', OLD.code;
    END IF;
  END IF;
  RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_protect_system_sc ON salary_components;
CREATE TRIGGER trg_protect_system_sc
  BEFORE UPDATE OR DELETE ON salary_components
  FOR EACH ROW EXECUTE FUNCTION trg_protect_system_salary_components();

-- ============================================================================
-- 3. employee_salary_config TABLE
-- ============================================================================

CREATE TABLE IF NOT EXISTS employee_salary_config (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       TEXT NOT NULL,
  employee_id     UUID NOT NULL REFERENCES employees(id),
  component_id    UUID NOT NULL REFERENCES salary_components(id),
  amount          NUMERIC(18,2) NOT NULL DEFAULT 0,
  percentage      NUMERIC(10,4),
  effective_date  DATE NOT NULL,
  end_date        DATE,
  created_at      TIMESTAMPTZ DEFAULT now(),
  updated_at      TIMESTAMPTZ DEFAULT now(),
  UNIQUE(tenant_id, employee_id, component_id, effective_date)
);

-- ============================================================================
-- 4. payroll_slip_lines TABLE
-- ============================================================================

CREATE TABLE IF NOT EXISTS payroll_slip_lines (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id           TEXT NOT NULL,
  payroll_id          UUID NOT NULL REFERENCES payroll_runs(id),
  employee_id         UUID NOT NULL REFERENCES employees(id),
  component_id        UUID REFERENCES salary_components(id),
  component_name      VARCHAR(100) NOT NULL,
  component_type      VARCHAR(15) NOT NULL,
  component_category  VARCHAR(30) NOT NULL,
  amount              NUMERIC(18,2) NOT NULL DEFAULT 0,
  is_taxable          BOOLEAN NOT NULL DEFAULT true,
  sort_order          INTEGER NOT NULL DEFAULT 0,
  created_at          TIMESTAMPTZ DEFAULT now()
);

-- ============================================================================
-- 5. ter_rates TABLE (global, no tenant_id)
-- ============================================================================

CREATE TABLE IF NOT EXISTS ter_rates (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  category        CHAR(1) NOT NULL,
  income_from     NUMERIC(18,2) NOT NULL,
  income_to       NUMERIC(18,2),
  rate            NUMERIC(10,4) NOT NULL,
  effective_year  INTEGER NOT NULL DEFAULT 2024,
  UNIQUE(category, income_from, effective_year)
);

-- ============================================================================
-- 6. ptkp_rates TABLE (global)
-- ============================================================================

CREATE TABLE IF NOT EXISTS ptkp_rates (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  status          VARCHAR(4) NOT NULL,
  annual_amount   NUMERIC(18,2) NOT NULL,
  effective_year  INTEGER NOT NULL DEFAULT 2016,
  UNIQUE(status, effective_year)
);

-- ============================================================================
-- 7. pasal17_brackets TABLE (global)
-- ============================================================================

CREATE TABLE IF NOT EXISTS pasal17_brackets (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  income_from     NUMERIC(18,2) NOT NULL,
  income_to       NUMERIC(18,2),
  rate            NUMERIC(10,4) NOT NULL,
  effective_year  INTEGER NOT NULL DEFAULT 2024,
  UNIQUE(income_from, effective_year)
);

-- ============================================================================
-- 8. bpjs_config TABLE
-- ============================================================================

CREATE TABLE IF NOT EXISTS bpjs_config (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       TEXT NOT NULL,
  component       VARCHAR(10) NOT NULL CHECK (component IN ('kes', 'jkk', 'jkm', 'jht', 'jp')),
  employer_rate   NUMERIC(10,4) NOT NULL,
  employee_rate   NUMERIC(10,4) NOT NULL DEFAULT 0,
  ceiling_amount  NUMERIC(18,2),
  effective_date  DATE NOT NULL,
  is_active       BOOLEAN NOT NULL DEFAULT true,
  UNIQUE(tenant_id, component, effective_date)
);

-- ============================================================================
-- 9. payroll_payments TABLE
-- ============================================================================

CREATE TABLE IF NOT EXISTS payroll_payments (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id         TEXT NOT NULL,
  payroll_id        UUID NOT NULL REFERENCES payroll_runs(id),
  payment_type      VARCHAR(20) NOT NULL CHECK (payment_type IN ('salary', 'pph21', 'bpjs')),
  payment_date      DATE NOT NULL,
  amount            NUMERIC(18,2) NOT NULL,
  bank_account_id   UUID,
  journal_id        UUID,
  reference_number  VARCHAR(50),
  notes             TEXT,
  status            VARCHAR(10) NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'posted', 'voided')),
  created_at        TIMESTAMPTZ DEFAULT now(),
  created_by        UUID,
  posted_at         TIMESTAMPTZ,
  posted_by         UUID,
  voided_at         TIMESTAMPTZ,
  voided_by         UUID,
  void_reason       TEXT
);

-- ============================================================================
-- 10. PERIOD LOCKING
-- ============================================================================

CREATE UNIQUE INDEX IF NOT EXISTS idx_payroll_unique_posted_period
  ON payroll_runs (tenant_id, EXTRACT(YEAR FROM period_start), EXTRACT(MONTH FROM period_start))
  WHERE status IN ('posted', 'approved', 'pending_approval');

-- ============================================================================
-- 11. RLS POLICIES
-- ============================================================================

ALTER TABLE salary_components ENABLE ROW LEVEL SECURITY;
ALTER TABLE salary_components FORCE ROW LEVEL SECURITY;
DO $$ BEGIN
  CREATE POLICY salary_components_tenant ON salary_components
    USING (tenant_id = current_setting('app.tenant_id', true));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

ALTER TABLE employee_salary_config ENABLE ROW LEVEL SECURITY;
ALTER TABLE employee_salary_config FORCE ROW LEVEL SECURITY;
DO $$ BEGIN
  CREATE POLICY esc_tenant ON employee_salary_config
    USING (tenant_id = current_setting('app.tenant_id', true));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

ALTER TABLE payroll_slip_lines ENABLE ROW LEVEL SECURITY;
ALTER TABLE payroll_slip_lines FORCE ROW LEVEL SECURITY;
DO $$ BEGIN
  CREATE POLICY psl_tenant ON payroll_slip_lines
    USING (tenant_id = current_setting('app.tenant_id', true));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

ALTER TABLE bpjs_config ENABLE ROW LEVEL SECURITY;
ALTER TABLE bpjs_config FORCE ROW LEVEL SECURITY;
DO $$ BEGIN
  CREATE POLICY bpjs_config_tenant ON bpjs_config
    USING (tenant_id = current_setting('app.tenant_id', true));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

ALTER TABLE payroll_payments ENABLE ROW LEVEL SECURITY;
ALTER TABLE payroll_payments FORCE ROW LEVEL SECURITY;
DO $$ BEGIN
  CREATE POLICY payroll_payments_tenant ON payroll_payments
    USING (tenant_id = current_setting('app.tenant_id', true));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- ============================================================================
-- 12. SEED: TER RATES — PP 58/2023
-- ============================================================================

-- Category A (TK/0, TK/1)
INSERT INTO ter_rates (category, income_from, income_to, rate, effective_year) VALUES
('A', 0, 5400000, 0, 2024),
('A', 5400000, 5650000, 0.0025, 2024),
('A', 5650000, 5950000, 0.0050, 2024),
('A', 5950000, 6300000, 0.0075, 2024),
('A', 6300000, 6750000, 0.0100, 2024),
('A', 6750000, 7500000, 0.0125, 2024),
('A', 7500000, 8550000, 0.0150, 2024),
('A', 8550000, 9650000, 0.0175, 2024),
('A', 9650000, 10050000, 0.0200, 2024),
('A', 10050000, 10350000, 0.0225, 2024),
('A', 10350000, 10700000, 0.0250, 2024),
('A', 10700000, 11050000, 0.0300, 2024),
('A', 11050000, 11600000, 0.0350, 2024),
('A', 11600000, 12500000, 0.0400, 2024),
('A', 12500000, 13750000, 0.0450, 2024),
('A', 13750000, 15100000, 0.0500, 2024),
('A', 15100000, 16950000, 0.0600, 2024),
('A', 16950000, 19750000, 0.0700, 2024),
('A', 19750000, 24150000, 0.0800, 2024),
('A', 24150000, 26450000, 0.0900, 2024),
('A', 26450000, 28000000, 0.1000, 2024),
('A', 28000000, 30050000, 0.1100, 2024),
('A', 30050000, 32400000, 0.1200, 2024),
('A', 32400000, 35400000, 0.1300, 2024),
('A', 35400000, 39100000, 0.1400, 2024),
('A', 39100000, 43850000, 0.1500, 2024),
('A', 43850000, 47800000, 0.1600, 2024),
('A', 47800000, 53800000, 0.1700, 2024),
('A', 53800000, 62000000, 0.1800, 2024),
('A', 62000000, 66700000, 0.1900, 2024),
('A', 66700000, 74500000, 0.2000, 2024),
('A', 74500000, 83200000, 0.2100, 2024),
('A', 83200000, 95000000, 0.2200, 2024),
('A', 95000000, 110000000, 0.2300, 2024),
('A', 110000000, 134000000, 0.2400, 2024),
('A', 134000000, 169000000, 0.2500, 2024),
('A', 169000000, 221000000, 0.2600, 2024),
('A', 221000000, 390000000, 0.2800, 2024),
('A', 390000000, 615000000, 0.3000, 2024),
('A', 615000000, 999000000, 0.3200, 2024),
('A', 999000000, NULL, 0.3400, 2024)
ON CONFLICT DO NOTHING;

-- Category B (TK/2, TK/3, K/0, K/1)
INSERT INTO ter_rates (category, income_from, income_to, rate, effective_year) VALUES
('B', 0, 6200000, 0, 2024),
('B', 6200000, 6500000, 0.0025, 2024),
('B', 6500000, 6850000, 0.0050, 2024),
('B', 6850000, 7300000, 0.0075, 2024),
('B', 7300000, 9200000, 0.0100, 2024),
('B', 9200000, 10750000, 0.0150, 2024),
('B', 10750000, 11250000, 0.0200, 2024),
('B', 11250000, 11600000, 0.0250, 2024),
('B', 11600000, 12500000, 0.0300, 2024),
('B', 12500000, 13750000, 0.0350, 2024),
('B', 13750000, 15100000, 0.0400, 2024),
('B', 15100000, 16950000, 0.0500, 2024),
('B', 16950000, 19750000, 0.0600, 2024),
('B', 19750000, 24150000, 0.0700, 2024),
('B', 24150000, 26450000, 0.0800, 2024),
('B', 26450000, 28000000, 0.0900, 2024),
('B', 28000000, 30050000, 0.1000, 2024),
('B', 30050000, 32400000, 0.1100, 2024),
('B', 32400000, 35400000, 0.1200, 2024),
('B', 35400000, 39100000, 0.1300, 2024),
('B', 39100000, 43850000, 0.1400, 2024),
('B', 43850000, 47800000, 0.1500, 2024),
('B', 47800000, 53800000, 0.1600, 2024),
('B', 53800000, 62000000, 0.1700, 2024),
('B', 62000000, 66700000, 0.1800, 2024),
('B', 66700000, 74500000, 0.1900, 2024),
('B', 74500000, 83200000, 0.2000, 2024),
('B', 83200000, 95000000, 0.2100, 2024),
('B', 95000000, 110000000, 0.2200, 2024),
('B', 110000000, 134000000, 0.2300, 2024),
('B', 134000000, 169000000, 0.2400, 2024),
('B', 169000000, 221000000, 0.2500, 2024),
('B', 221000000, 390000000, 0.2700, 2024),
('B', 390000000, 615000000, 0.2900, 2024),
('B', 615000000, 999000000, 0.3100, 2024),
('B', 999000000, NULL, 0.3300, 2024)
ON CONFLICT DO NOTHING;

-- Category C (K/2, K/3)
INSERT INTO ter_rates (category, income_from, income_to, rate, effective_year) VALUES
('C', 0, 6600000, 0, 2024),
('C', 6600000, 6950000, 0.0025, 2024),
('C', 6950000, 7350000, 0.0050, 2024),
('C', 7350000, 7800000, 0.0075, 2024),
('C', 7800000, 8850000, 0.0100, 2024),
('C', 8850000, 9800000, 0.0125, 2024),
('C', 9800000, 10950000, 0.0150, 2024),
('C', 10950000, 11200000, 0.0175, 2024),
('C', 11200000, 12050000, 0.0200, 2024),
('C', 12050000, 12950000, 0.0250, 2024),
('C', 12950000, 14150000, 0.0300, 2024),
('C', 14150000, 15550000, 0.0350, 2024),
('C', 15550000, 17050000, 0.0400, 2024),
('C', 17050000, 19500000, 0.0500, 2024),
('C', 19500000, 22700000, 0.0600, 2024),
('C', 22700000, 26600000, 0.0700, 2024),
('C', 26600000, 28100000, 0.0800, 2024),
('C', 28100000, 30100000, 0.0900, 2024),
('C', 30100000, 32600000, 0.1000, 2024),
('C', 32600000, 35400000, 0.1100, 2024),
('C', 35400000, 38900000, 0.1200, 2024),
('C', 38900000, 43000000, 0.1300, 2024),
('C', 43000000, 47400000, 0.1400, 2024),
('C', 47400000, 51200000, 0.1500, 2024),
('C', 51200000, 56300000, 0.1600, 2024),
('C', 56300000, 62200000, 0.1700, 2024),
('C', 62200000, 68600000, 0.1800, 2024),
('C', 68600000, 77500000, 0.1900, 2024),
('C', 77500000, 89000000, 0.2000, 2024),
('C', 89000000, 103000000, 0.2100, 2024),
('C', 103000000, 125000000, 0.2200, 2024),
('C', 125000000, 157000000, 0.2300, 2024),
('C', 157000000, 206000000, 0.2400, 2024),
('C', 206000000, 337000000, 0.2500, 2024),
('C', 337000000, 454000000, 0.2600, 2024),
('C', 454000000, 550000000, 0.2800, 2024),
('C', 550000000, 695000000, 0.3000, 2024),
('C', 695000000, 910000000, 0.3100, 2024),
('C', 910000000, NULL, 0.3200, 2024)
ON CONFLICT DO NOTHING;

-- ============================================================================
-- 13. SEED: PTKP RATES
-- ============================================================================

INSERT INTO ptkp_rates (status, annual_amount, effective_year) VALUES
('TK0', 54000000, 2016), ('TK1', 58500000, 2016), ('TK2', 63000000, 2016), ('TK3', 67500000, 2016),
('K0', 58500000, 2016), ('K1', 63000000, 2016), ('K2', 67500000, 2016), ('K3', 72000000, 2016),
('KI0', 112500000, 2016), ('KI1', 117000000, 2016), ('KI2', 121500000, 2016), ('KI3', 126000000, 2016)
ON CONFLICT DO NOTHING;

-- ============================================================================
-- 14. SEED: PASAL 17 BRACKETS
-- ============================================================================

INSERT INTO pasal17_brackets (income_from, income_to, rate, effective_year) VALUES
(0, 60000000, 0.05, 2024),
(60000000, 250000000, 0.15, 2024),
(250000000, 500000000, 0.25, 2024),
(500000000, 5000000000, 0.30, 2024),
(5000000000, NULL, 0.35, 2024)
ON CONFLICT DO NOTHING;

-- ============================================================================
-- 15. SEED: BPJS CONFIG per tenant (2025 rates)
-- ============================================================================

INSERT INTO bpjs_config (tenant_id, component, employer_rate, employee_rate, ceiling_amount, effective_date)
SELECT t.tenant_id, v.component, v.er, v.ee, v.ceiling, '2025-01-01'::date
FROM (SELECT DISTINCT tenant_id FROM chart_of_accounts) t
CROSS JOIN (VALUES
  ('kes', 0.0400, 0.0100, 12000000.00),
  ('jkk', 0.0024, 0.0000, NULL::numeric),
  ('jkm', 0.0030, 0.0000, NULL::numeric),
  ('jht', 0.0370, 0.0200, NULL::numeric),
  ('jp',  0.0200, 0.0100, 10042300.00)
) AS v(component, er, ee, ceiling)
ON CONFLICT DO NOTHING;

-- ============================================================================
-- 16. SEED: SYSTEM SALARY COMPONENTS per tenant
-- ============================================================================

INSERT INTO salary_components (tenant_id, code, name, type, category, is_taxable, is_fixed, sort_order, is_system)
SELECT t.tenant_id, v.code, v.name, v.type, v.category, v.is_taxable, v.is_fixed, v.sort_order, true
FROM (SELECT DISTINCT tenant_id FROM chart_of_accounts) t
CROSS JOIN (VALUES
  ('GAJI_POKOK', 'Gaji Pokok', 'earning', 'gaji_pokok', true, true, 1),
  ('TUNJ_TRANSPORT', 'Tunjangan Transportasi', 'earning', 'tunjangan_tetap', true, true, 10),
  ('TUNJ_MAKAN', 'Tunjangan Makan', 'earning', 'tunjangan_tetap', true, true, 11),
  ('TUNJ_JABATAN', 'Tunjangan Jabatan', 'earning', 'tunjangan_tetap', true, true, 12),
  ('LEMBUR', 'Lembur', 'earning', 'lembur', true, false, 20),
  ('BONUS', 'Bonus', 'earning', 'bonus', true, false, 21),
  ('THR', 'Tunjangan Hari Raya', 'earning', 'thr', true, false, 22),
  ('POT_BPJS_KES', 'BPJS Kesehatan', 'deduction', 'bpjs_kes_ee', false, true, 100),
  ('POT_BPJS_JHT', 'BPJS JHT', 'deduction', 'bpjs_jht_ee', false, true, 101),
  ('POT_BPJS_JP', 'BPJS JP', 'deduction', 'bpjs_jp_ee', false, true, 102),
  ('POT_PPH21', 'PPh 21', 'deduction', 'pph21', false, true, 110),
  ('BPJS_KES_ER', 'BPJS Kesehatan (Perusahaan)', 'employer_cost', 'bpjs_kes_er', false, true, 200),
  ('BPJS_JHT_ER', 'BPJS JHT (Perusahaan)', 'employer_cost', 'bpjs_jht_er', false, true, 201),
  ('BPJS_JP_ER', 'BPJS JP (Perusahaan)', 'employer_cost', 'bpjs_jp_er', false, true, 202),
  ('BPJS_JKK', 'BPJS JKK (Perusahaan)', 'employer_cost', 'bpjs_jkk', false, true, 203),
  ('BPJS_JKM', 'BPJS JKM (Perusahaan)', 'employer_cost', 'bpjs_jkm', false, true, 204)
) AS v(code, name, type, category, is_taxable, is_fixed, sort_order)
ON CONFLICT DO NOTHING;

-- ============================================================================
-- 17. SEED: CoA ACCOUNTS for payroll
-- Use existing accounts where possible:
--   5-20100 = Beban Gaji (EXISTS)
--   2-10400 = Utang Gaji (EXISTS)
-- New accounts needed:
--   5-20150 = Beban BPJS Perusahaan
--   5-80100 = Beban PPh 21 Perusahaan
--   2-10310 = Utang PPh 21
--   2-10410 = Utang BPJS Karyawan
--   2-10420 = Utang BPJS Perusahaan
-- ============================================================================

INSERT INTO chart_of_accounts (tenant_id, account_code, name, account_type, normal_balance, is_active, is_header)
SELECT t.tenant_id, v.code, v.name, v.atype, v.normal, true, false
FROM (SELECT DISTINCT tenant_id FROM chart_of_accounts WHERE tenant_id IS NOT NULL) t
CROSS JOIN (VALUES
  ('5-20150', 'Beban BPJS Perusahaan', 'EXPENSE', 'DEBIT'),
  ('5-80100', 'Beban PPh 21 Perusahaan', 'EXPENSE', 'DEBIT'),
  ('2-10310', 'Utang PPh 21', 'LIABILITY', 'CREDIT'),
  ('2-10410', 'Utang BPJS Karyawan', 'LIABILITY', 'CREDIT'),
  ('2-10420', 'Utang BPJS Perusahaan', 'LIABILITY', 'CREDIT')
) AS v(code, name, atype, normal)
WHERE NOT EXISTS (
  SELECT 1 FROM chart_of_accounts ca WHERE ca.tenant_id = t.tenant_id AND ca.account_code = v.code
);
