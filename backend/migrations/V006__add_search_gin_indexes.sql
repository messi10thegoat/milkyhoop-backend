-- =====================================================
-- V006: Add GIN indexes for pg_trgm fuzzy search
-- =====================================================
-- Purpose: Optimize product/member search queries that use
--          similarity() and ILIKE patterns
-- Impact: Search latency ~500ms → ~20ms for 10K+ rows
-- =====================================================

-- Enable pg_trgm extension (required for GIN trigram indexes)
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- =====================================================
-- 1. Products table - main product search
-- =====================================================
-- Used by: /api/products/search/pos, /api/products/search/kulakan
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_products_nama_trgm
ON public.products USING GIN (nama_produk gin_trgm_ops);

-- =====================================================
-- 2. Item Transaksi table - transaction history search
-- =====================================================
-- Used by: /api/products/search, /api/products/all
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_item_transaksi_nama_trgm
ON public.item_transaksi USING GIN (nama_produk gin_trgm_ops);

-- =====================================================
-- 3. Customers table - member/customer search
-- =====================================================
-- Used by: /api/members/search
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_customers_nama_trgm
ON public.customers USING GIN (nama gin_trgm_ops);

-- Optional: Index for phone number search (if frequently used)
-- CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_customers_telepon_trgm
-- ON public.customers USING GIN (telepon gin_trgm_ops);
