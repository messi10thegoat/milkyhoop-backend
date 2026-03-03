-- V119__unit_conversions_intelligence_upgrade.sql
-- Financial Intelligence Layer — Upgrade unit_conversions for OCR pipeline
-- Purpose: Enable global (product-agnostic) conversions and improve numeric precision
-- Background: V078 created unit_conversions with product_id NOT NULL and INTEGER factor.
-- Intelligence Layer needs: nullable product_id (global conversions) + NUMERIC(15,6) precision (Law 25).

-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- 1. Make product_id nullable (for global/tenant-wide conversions)
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

ALTER TABLE unit_conversions ALTER COLUMN product_id DROP NOT NULL;

-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- 2. Upgrade conversion_factor to NUMERIC(15,6) precision (Law 25)
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

ALTER TABLE unit_conversions ALTER COLUMN conversion_factor TYPE NUMERIC(15,6);

-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- 3. Add index for global conversions lookup (product_id IS NULL)
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

CREATE INDEX IF NOT EXISTS idx_unit_conv_global
    ON unit_conversions(tenant_id, base_unit)
    WHERE product_id IS NULL;

-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- 4. Drop old unique constraint and recreate to handle NULL product_id
-- The existing constraint uq_unit_conversion is (tenant_id, product_id, base_unit, conversion_unit)
-- With NULL product_id, PostgreSQL treats NULLs as distinct, so we need a partial unique index
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

-- Keep existing constraint for product-specific rows (they still need uniqueness)
-- Add separate unique index for global rows (product_id IS NULL)
CREATE UNIQUE INDEX IF NOT EXISTS idx_unit_conv_global_unique
    ON unit_conversions(tenant_id, base_unit, conversion_unit)
    WHERE product_id IS NULL;
