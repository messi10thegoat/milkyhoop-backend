-- ============================================
-- V007: Add Unit Conversion Fields to Products
-- ============================================
-- Purpose: Support wholesale-to-retail unit conversion
-- Example: 1 Dus = 12 pcs for Triaminic Syrup
--
-- New columns:
--   base_unit          - smallest selling unit (pcs, botol, lembar)
--   wholesale_unit     - bulk purchase unit (dus, karton, pack)
--   units_per_wholesale - conversion factor (12, 24, 48)
-- ============================================

-- Step 1: Add new columns to products table
ALTER TABLE products ADD COLUMN IF NOT EXISTS base_unit VARCHAR(50) DEFAULT 'pcs';
ALTER TABLE products ADD COLUMN IF NOT EXISTS wholesale_unit VARCHAR(50);
ALTER TABLE products ADD COLUMN IF NOT EXISTS units_per_wholesale INT;

-- Step 2: Add index for products with wholesale unit (for filtering)
CREATE INDEX IF NOT EXISTS idx_products_has_wholesale
ON products (tenant_id)
WHERE wholesale_unit IS NOT NULL;

-- Step 3: Backfill existing products from transaction history
-- Formula: harga_satuan (per dus) / hpp_per_unit (per pcs) = units_per_wholesale
-- Example: 1,200,000 / 100,000 = 12 pcs per dus

-- First, update wholesale_unit from transaction history
UPDATE products p
SET wholesale_unit = (
    SELECT LOWER(it.satuan)
    FROM item_transaksi it
    JOIN transaksi_harian th ON th.id = it.transaksi_id
    WHERE th.tenant_id = p.tenant_id
      AND it.nama_produk = p.nama_produk
      AND LOWER(it.satuan) IN ('dus', 'karton', 'pack', 'box', 'lusin', 'gross', 'rim')
    ORDER BY th.created_at DESC
    LIMIT 1
)
WHERE p.tenant_id IS NOT NULL
  AND EXISTS (
    SELECT 1 FROM item_transaksi it
    JOIN transaksi_harian th ON th.id = it.transaksi_id
    WHERE th.tenant_id = p.tenant_id
      AND it.nama_produk = p.nama_produk
      AND LOWER(it.satuan) IN ('dus', 'karton', 'pack', 'box', 'lusin', 'gross', 'rim')
);

-- Then, calculate units_per_wholesale from HPP ratio
UPDATE products p
SET units_per_wholesale = subq.calculated_units
FROM (
    SELECT
        pr.id as product_id,
        ROUND(it.harga_satuan / NULLIF(it.hpp_per_unit, 0))::INT as calculated_units
    FROM products pr
    JOIN item_transaksi it ON it.nama_produk = pr.nama_produk
    JOIN transaksi_harian th ON th.id = it.transaksi_id
        AND th.tenant_id = pr.tenant_id
    WHERE th.jenis_transaksi = 'pembelian'
      AND it.hpp_per_unit > 0
      AND it.harga_satuan > 0
      AND it.hpp_per_unit < it.harga_satuan  -- HPP should be less than wholesale price
    ORDER BY th.created_at DESC
) subq
WHERE p.id = subq.product_id
  AND subq.calculated_units > 1
  AND subq.calculated_units <= 1000;  -- Sanity check: max 1000 units per wholesale

-- Log the results
DO $$
DECLARE
    updated_count INT;
BEGIN
    SELECT COUNT(*) INTO updated_count
    FROM products
    WHERE units_per_wholesale IS NOT NULL;

    RAISE NOTICE 'V007: Updated % products with unit conversion data', updated_count;
END $$;
