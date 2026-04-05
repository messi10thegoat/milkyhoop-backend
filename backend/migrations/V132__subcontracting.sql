-- V132__subcontracting.sql
-- Subcontracting (Maklun) support for manufacturing module

-- 1a. ALTER bom_operations — add subcontract columns
ALTER TABLE bom_operations ADD COLUMN IF NOT EXISTS is_subcontract boolean NOT NULL DEFAULT false;
ALTER TABLE bom_operations ADD COLUMN IF NOT EXISTS vendor_id uuid REFERENCES vendors(id);
ALTER TABLE bom_operations ADD COLUMN IF NOT EXISTS subcontract_cost_per_unit numeric(18,2) DEFAULT 0;
ALTER TABLE bom_operations ADD COLUMN IF NOT EXISTS subcontract_description text;

-- Constraint: if is_subcontract=true, vendor_id MUST be set
DO $$ BEGIN
  ALTER TABLE bom_operations ADD CONSTRAINT chk_subcontract_vendor
    CHECK (NOT is_subcontract OR vendor_id IS NOT NULL);
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- 1b. ALTER production_orders — track subcontract cost + total cost
ALTER TABLE production_orders ADD COLUMN IF NOT EXISTS subcontract_cost numeric(18,2) DEFAULT 0;
ALTER TABLE production_orders ADD COLUMN IF NOT EXISTS total_cost numeric(18,2) DEFAULT 0;

-- 1c. CREATE production_subcontracts table
CREATE TABLE IF NOT EXISTS production_subcontracts (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id varchar NOT NULL,
  production_order_id uuid NOT NULL REFERENCES production_orders(id),
  bom_operation_id uuid NOT NULL REFERENCES bom_operations(id),
  vendor_id uuid NOT NULL REFERENCES vendors(id),
  quantity numeric NOT NULL,
  unit_cost numeric(18,2) NOT NULL,
  total_cost numeric(18,2) NOT NULL,
  bill_id uuid REFERENCES bills(id),
  bill_status varchar,
  status varchar NOT NULL DEFAULT 'pending',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

-- RLS
ALTER TABLE production_subcontracts ENABLE ROW LEVEL SECURITY;
ALTER TABLE production_subcontracts FORCE ROW LEVEL SECURITY;

DO $$ BEGIN
  CREATE POLICY tenant_isolation ON production_subcontracts
    USING (tenant_id = current_setting('app.tenant_id'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- Indexes
CREATE INDEX IF NOT EXISTS idx_prodsub_wo ON production_subcontracts(production_order_id);
CREATE INDEX IF NOT EXISTS idx_prodsub_bill ON production_subcontracts(bill_id);
CREATE INDEX IF NOT EXISTS idx_prodsub_tenant ON production_subcontracts(tenant_id);
