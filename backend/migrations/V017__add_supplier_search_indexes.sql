-- =====================================================
-- V017: Add indexes for supplier autocomplete optimization
-- =====================================================
-- Purpose: Optimize supplier search in /suppliers/all endpoint
--          which queries transaksi_harian.nama_pihak
-- Impact: Supplier prefetch latency ~200ms → ~50ms
-- =====================================================

-- =====================================================
-- 1. Composite B-tree index for tenant + supplier lookup
-- =====================================================
-- Used by: /api/suppliers/all, /api/suppliers/search
-- Pattern: WHERE tenant_id = $1 AND nama_pihak IS NOT NULL
-- This is the PRIMARY optimization - speeds up GROUP BY significantly
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_transaksi_tenant_supplier
ON public.transaksi_harian (tenant_id, nama_pihak)
WHERE nama_pihak IS NOT NULL AND nama_pihak != '';

-- =====================================================
-- 2. GIN trigram index for fuzzy supplier search (OPTIONAL)
-- =====================================================
-- Used by: /api/suppliers/search (rarely called, frontend uses Fuse.js)
-- Note: This index is OPTIONAL since suppliers are prefetched to frontend
--       and fuzzy search happens client-side with Fuse.js
-- Uncomment if server-side fuzzy search is needed in the future
--
-- CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_transaksi_supplier_trgm
-- ON public.transaksi_harian USING GIN (lower(nama_pihak) gin_trgm_ops)
-- WHERE nama_pihak IS NOT NULL AND nama_pihak != '';

-- =====================================================
-- 3. Partial index for contact lookup (OPTIONAL)
-- =====================================================
-- Used by: MAX(kontak_pihak) in /api/suppliers/all
-- Only useful if contact info is frequently needed
--
-- CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_transaksi_tenant_contact
-- ON public.transaksi_harian (tenant_id, nama_pihak, kontak_pihak)
-- WHERE nama_pihak IS NOT NULL AND nama_pihak != '' AND kontak_pihak IS NOT NULL;
