-- Migration V005: Add Discount/PPN fields to TransaksiHarian and Barcode to Products
-- Sprint 2.2: Enhanced Transaction Pricing
-- Created: 2025-11-22

-- ============================================
-- ISSUE 2: DISCOUNT & PPN CALCULATION (URGENT)
-- ============================================

-- Add discount and PPN columns to transaksi_harian
ALTER TABLE transaksi_harian
ADD COLUMN IF NOT EXISTS discount_type VARCHAR(20) DEFAULT NULL,           -- 'percentage' or 'nominal'
ADD COLUMN IF NOT EXISTS discount_value DECIMAL(15, 2) DEFAULT 0,           -- e.g., 10 (%) or 5000 (Rp)
ADD COLUMN IF NOT EXISTS discount_amount BIGINT DEFAULT 0,                  -- Calculated discount in cents
ADD COLUMN IF NOT EXISTS subtotal_before_discount BIGINT DEFAULT 0,         -- Sum of items before discount
ADD COLUMN IF NOT EXISTS subtotal_after_discount BIGINT DEFAULT 0,          -- After discount applied
ADD COLUMN IF NOT EXISTS include_vat BOOLEAN DEFAULT FALSE,                 -- Whether to include PPN 11%
ADD COLUMN IF NOT EXISTS vat_amount BIGINT DEFAULT 0,                       -- Calculated PPN amount
ADD COLUMN IF NOT EXISTS grand_total BIGINT DEFAULT 0;                      -- Final total after discount & PPN

-- Add indexes for reporting queries
CREATE INDEX IF NOT EXISTS idx_transaksi_discount_type ON transaksi_harian(discount_type) WHERE discount_type IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_transaksi_include_vat ON transaksi_harian(include_vat) WHERE include_vat = TRUE;

-- ============================================
-- ISSUE 1: BARCODE PRODUCT REGISTRATION
-- ============================================

-- Add barcode column to products table
ALTER TABLE products
ADD COLUMN IF NOT EXISTS barcode VARCHAR(50) DEFAULT NULL;

-- Add unique constraint for barcode per tenant
-- (same barcode can exist in different tenants)
CREATE UNIQUE INDEX IF NOT EXISTS idx_products_tenant_barcode
ON products(tenant_id, barcode)
WHERE barcode IS NOT NULL;

-- Add index for barcode lookup
CREATE INDEX IF NOT EXISTS idx_products_barcode ON products(barcode) WHERE barcode IS NOT NULL;

-- ============================================
-- COMMENTS
-- ============================================
COMMENT ON COLUMN transaksi_harian.discount_type IS 'Type of discount: percentage or nominal';
COMMENT ON COLUMN transaksi_harian.discount_value IS 'Raw discount value (e.g., 10 for 10%, or 5000 for Rp 5000)';
COMMENT ON COLUMN transaksi_harian.discount_amount IS 'Calculated discount amount in smallest currency unit';
COMMENT ON COLUMN transaksi_harian.subtotal_before_discount IS 'Sum of all items before any discount';
COMMENT ON COLUMN transaksi_harian.subtotal_after_discount IS 'Total after discount applied';
COMMENT ON COLUMN transaksi_harian.include_vat IS 'Whether PPN 11% is applied';
COMMENT ON COLUMN transaksi_harian.vat_amount IS 'Calculated VAT amount (11% of subtotal_after_discount)';
COMMENT ON COLUMN transaksi_harian.grand_total IS 'Final total: subtotal_after_discount + vat_amount';
COMMENT ON COLUMN products.barcode IS 'Product barcode (EAN-13, UPC, etc.)';

-- ============================================
-- MIGRATION NOTES
-- ============================================
-- After this migration:
--
-- 1. Backend needs to calculate:
--    - subtotal_before_discount = sum(item.subtotal)
--    - discount_amount = subtotal * (discount_value/100) OR discount_value
--    - subtotal_after_discount = subtotal_before_discount - discount_amount
--    - vat_amount = subtotal_after_discount * 0.11 (if include_vat)
--    - grand_total = subtotal_after_discount + vat_amount
--
-- 2. Receipt should show:
--    - Subtotal: Rp X
--    - Diskon (10%): -Rp Y
--    - Setelah Diskon: Rp Z
--    - PPN 11%: Rp A
--    - Grand Total: Rp B
--
-- 3. Barcode lookup API:
--    GET /api/products/barcode/{barcode}
--    Returns product details for quick POS scanning
-- ============================================
