-- Pembalik V517 (kolom baru saja; data lain tak tersentuh).
ALTER TABLE products DROP CONSTRAINT IF EXISTS chk_products_dimensi_v517;
ALTER TABLE products DROP COLUMN IF EXISTS weight, DROP COLUMN IF EXISTS weight_unit, DROP COLUMN IF EXISTS length, DROP COLUMN IF EXISTS width,
    DROP COLUMN IF EXISTS height, DROP COLUMN IF EXISTS dimension_unit, DROP COLUMN IF EXISTS min_qty_jual, DROP COLUMN IF EXISTS min_qty_beli;
