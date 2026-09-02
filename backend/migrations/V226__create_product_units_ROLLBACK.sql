-- ============================================================================
-- V226 ROLLBACK — drop product_units.
-- ----------------------------------------------------------------------------
-- Safe ONLY while the table is empty or its contents are disposable. Satuan is
-- master data, not ledger: no journal, no FK anywhere points at it (nothing in
-- the schema references product_units.id — unit_conversions and products store
-- unit NAMES as varchar, see V226 note D5). Dropping it returns the system to
-- the fail-loud state it is in today.
--
-- Run this ONLY on explicit owner instruction.
-- ============================================================================

-- Guard: refuse to drop silently if a tenant already created real units.
DO $$
DECLARE v_rows BIGINT;
BEGIN
    IF to_regclass('public.product_units') IS NULL THEN
        RAISE NOTICE 'V226 rollback: product_units sudah tidak ada — tidak ada yang dikerjakan';
        RETURN;
    END IF;
    EXECUTE 'SELECT count(*) FROM product_units' INTO v_rows;
    RAISE NOTICE 'V226 rollback: menghapus product_units berisi % baris', v_rows;
END $$;

DROP INDEX IF EXISTS uq_product_units_tenant_abbr;
DROP INDEX IF EXISTS idx_product_units_tenant;
DROP TABLE IF EXISTS product_units;

DELETE FROM schema_migrations WHERE version = 'V226__create_product_units.sql';

DO $$
BEGIN
    IF to_regclass('public.product_units') IS NOT NULL THEN
        RAISE EXCEPTION 'V226 rollback GAGAL: product_units masih ada';
    END IF;
    RAISE NOTICE 'V226 rollback OK: product_units terhapus';
END $$;
