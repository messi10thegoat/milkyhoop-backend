-- Migration: Add missing customer fields
-- Date: 2026-01-28
-- Purpose: Align customers table with Vendor module and accounting software standards (QB/Xero/Zoho)

-- Add missing columns to customers table
ALTER TABLE customers 
  ADD COLUMN IF NOT EXISTS contact_person VARCHAR(255),
  ADD COLUMN IF NOT EXISTS city VARCHAR(100),
  ADD COLUMN IF NOT EXISTS province VARCHAR(100),
  ADD COLUMN IF NOT EXISTS postal_code VARCHAR(20),
  ADD COLUMN IF NOT EXISTS tax_id VARCHAR(50),
  ADD COLUMN IF NOT EXISTS payment_terms_days INTEGER DEFAULT 0,
  ADD COLUMN IF NOT EXISTS credit_limit BIGINT,
  ADD COLUMN IF NOT EXISTS notes TEXT,
  ADD COLUMN IF NOT EXISTS mobile_phone VARCHAR(50),
  ADD COLUMN IF NOT EXISTS website VARCHAR(255),
  ADD COLUMN IF NOT EXISTS company_name VARCHAR(255),
  ADD COLUMN IF NOT EXISTS display_name VARCHAR(255),
  ADD COLUMN IF NOT EXISTS customer_type VARCHAR(20) DEFAULT 'BADAN',
  ADD COLUMN IF NOT EXISTS is_pkp BOOLEAN DEFAULT false,
  ADD COLUMN IF NOT EXISTS nik VARCHAR(20),
  ADD COLUMN IF NOT EXISTS currency VARCHAR(3) DEFAULT 'IDR',
  ADD COLUMN IF NOT EXISTS created_by UUID;

-- Add check constraint for customer_type
ALTER TABLE customers ADD CONSTRAINT chk_customers_customer_type 
  CHECK (customer_type IS NULL OR customer_type IN ('BADAN', 'ORANG_PRIBADI', 'LUAR_NEGERI'));

-- Create indexes for new searchable fields
CREATE INDEX IF NOT EXISTS idx_customers_city ON customers(tenant_id, city);
CREATE INDEX IF NOT EXISTS idx_customers_tax_id ON customers(tenant_id, tax_id) WHERE tax_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_customers_customer_type ON customers(tenant_id, customer_type);
CREATE INDEX IF NOT EXISTS idx_customers_pkp ON customers(tenant_id, is_pkp) WHERE is_pkp = true;
