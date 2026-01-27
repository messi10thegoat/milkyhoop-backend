-- V079: Add image_url to products table for primary product image
-- The full gallery uses the existing documents/attachments system

ALTER TABLE products ADD COLUMN IF NOT EXISTS image_url TEXT;
COMMENT ON COLUMN products.image_url IS 'Primary product image URL (stored in MinIO)';
