-- ============================================
-- V008: Recalculate Persediaan Stock with Unit Conversion
-- ============================================
-- Purpose: Fix incorrect stock values caused by missing unit conversion
-- Depends on: V007 (units_per_wholesale must be populated first)
--
-- Example fix for Triaminic Syrup Lemon 60ml:
--   BEFORE: stok = 12 (wrong - stored raw wholesale qty)
--   AFTER:  stok = 124 (correct - 13 Dus × 12 - 32 pcs sold)
-- ============================================

-- Step 1: Create temporary table with correct stock calculation
CREATE TEMP TABLE temp_stock_recalc AS
WITH stock_calculation AS (
    SELECT
        p.id as product_id,
        p.tenant_id,
        p.nama_produk,
        p.base_unit,
        p.units_per_wholesale,
        -- Total masuk (converted to base unit)
        COALESCE(SUM(
            CASE
                WHEN th.jenis_transaksi = 'pembelian' THEN
                    CASE
                        WHEN LOWER(it.satuan) IN ('dus', 'karton', 'pack', 'box', 'lusin', 'gross', 'rim')
                        THEN it.jumlah * COALESCE(p.units_per_wholesale, 1)
                        ELSE it.jumlah
                    END
                ELSE 0
            END
        ), 0) as total_masuk,
        -- Total keluar (already in base unit from POS)
        COALESCE(SUM(
            CASE
                WHEN th.jenis_transaksi = 'penjualan' THEN it.jumlah
                ELSE 0
            END
        ), 0) as total_keluar,
        -- Count transactions for audit
        COUNT(DISTINCT CASE WHEN th.jenis_transaksi = 'pembelian' THEN th.id END) as tx_masuk_count,
        COUNT(DISTINCT CASE WHEN th.jenis_transaksi = 'penjualan' THEN th.id END) as tx_keluar_count
    FROM products p
    LEFT JOIN item_transaksi it ON LOWER(it.nama_produk) = LOWER(p.nama_produk)
    LEFT JOIN transaksi_harian th ON th.id = it.transaksi_id
        AND th.tenant_id = p.tenant_id
        AND th.status = 'approved'
    WHERE p.tenant_id IS NOT NULL
    GROUP BY p.id, p.tenant_id, p.nama_produk, p.base_unit, p.units_per_wholesale
)
SELECT
    product_id,
    tenant_id,
    nama_produk,
    units_per_wholesale,
    total_masuk,
    total_keluar,
    (total_masuk - total_keluar) as calculated_stock,
    tx_masuk_count,
    tx_keluar_count
FROM stock_calculation;

-- Step 2: Log products that will be updated (for audit)
DO $$
DECLARE
    affected_count INT;
    sample_record RECORD;
BEGIN
    -- Count products with stock difference
    SELECT COUNT(*) INTO affected_count
    FROM temp_stock_recalc tsr
    JOIN persediaan pe ON pe.product_id = tsr.product_id AND pe.tenant_id = tsr.tenant_id
    WHERE ABS(pe.jumlah - tsr.calculated_stock) > 0.01;

    RAISE NOTICE 'V008: Found % products with stock discrepancy', affected_count;

    -- Log sample of changes
    FOR sample_record IN
        SELECT
            tsr.nama_produk,
            pe.jumlah as old_stock,
            tsr.calculated_stock as new_stock,
            tsr.units_per_wholesale,
            tsr.total_masuk,
            tsr.total_keluar
        FROM temp_stock_recalc tsr
        JOIN persediaan pe ON pe.product_id = tsr.product_id AND pe.tenant_id = tsr.tenant_id
        WHERE ABS(pe.jumlah - tsr.calculated_stock) > 0.01
        LIMIT 5
    LOOP
        RAISE NOTICE 'V008: % | old=% | new=% | conversion=%x | masuk=% | keluar=%',
            sample_record.nama_produk,
            sample_record.old_stock,
            sample_record.new_stock,
            sample_record.units_per_wholesale,
            sample_record.total_masuk,
            sample_record.total_keluar;
    END LOOP;
END $$;

-- Step 3: Update persediaan with correct stock values
UPDATE persediaan pe
SET
    jumlah = tsr.calculated_stock,
    updated_at = NOW()
FROM temp_stock_recalc tsr
WHERE pe.product_id = tsr.product_id
  AND pe.tenant_id = tsr.tenant_id
  AND ABS(pe.jumlah - tsr.calculated_stock) > 0.01;  -- Only update if different

-- Step 4: Clean up
DROP TABLE IF EXISTS temp_stock_recalc;

-- Step 5: Final verification
DO $$
DECLARE
    total_persediaan INT;
    with_conversion INT;
BEGIN
    SELECT COUNT(*) INTO total_persediaan FROM persediaan;
    SELECT COUNT(*) INTO with_conversion
    FROM persediaan pe
    JOIN products p ON p.id = pe.product_id
    WHERE p.units_per_wholesale IS NOT NULL;

    RAISE NOTICE 'V008: Complete. Total persediaan: %, With unit conversion: %',
        total_persediaan, with_conversion;
END $$;
