-- ============================================================================
-- Backfill: product inventory / COGS accounts for stockable goods
-- Date: 2026-06-15
-- ----------------------------------------------------------------------------
-- Class-1 master-data NULL-account hygiene fix.
--
-- WHAT: For stockable goods (item_type='goods' AND track_inventory=true) whose
--       inventory_account_id / cogs_account_id is NULL, set it to the tenant's
--       role-mapped account (INVENTORY_MERCHANDISE / COGS_SALES) from
--       account_roles. This makes explicit the same role default the COGS/restock
--       path already substitutes silently at runtime -> ZERO economic change.
--
-- IDEMPOTENT: only writes columns that ARE NULL (re-running is a no-op).
--
-- SCOPE: EXCLUDES grapgrap (on hold; its product backfill is deferred).
--        Excludes soft-deleted products (deleted_at IS NOT NULL).
--
-- SAFETY: Run as a superuser / BYPASSRLS role (e.g. postgres) so RLS on
--         account_roles / products does not filter rows. Wrapped in a single
--         transaction. Authored NOT executed.
-- ============================================================================

BEGIN;

-- 1) Backfill inventory_account_id from INVENTORY_MERCHANDISE role mapping.
UPDATE products p
SET inventory_account_id = ar.account_id
FROM account_roles ar
WHERE ar.tenant_id = p.tenant_id
  AND ar.role_key = 'INVENTORY_MERCHANDISE'
  AND p.item_type = 'goods'
  AND p.track_inventory = TRUE
  AND p.deleted_at IS NULL
  AND p.inventory_account_id IS NULL
  AND p.tenant_id <> 'grapgrap';

-- 2) Backfill cogs_account_id from COGS_SALES role mapping.
UPDATE products p
SET cogs_account_id = ar.account_id
FROM account_roles ar
WHERE ar.tenant_id = p.tenant_id
  AND ar.role_key = 'COGS_SALES'
  AND p.item_type = 'goods'
  AND p.track_inventory = TRUE
  AND p.deleted_at IS NULL
  AND p.cogs_account_id IS NULL
  AND p.tenant_id <> 'grapgrap';

-- 3) VERIFY: remaining stockable-goods products with a NULL account, per tenant.
--    Expect 0 rows for every non-grapgrap tenant that has the role mapped.
--    (grapgrap is intentionally excluded above and will still show NULLs.)
SELECT
    p.tenant_id,
    COUNT(*) FILTER (WHERE p.inventory_account_id IS NULL) AS remaining_null_inventory,
    COUNT(*) FILTER (WHERE p.cogs_account_id IS NULL)      AS remaining_null_cogs
FROM products p
WHERE p.item_type = 'goods'
  AND p.track_inventory = TRUE
  AND p.deleted_at IS NULL
  AND p.tenant_id <> 'grapgrap'
  AND (p.inventory_account_id IS NULL OR p.cogs_account_id IS NULL)
GROUP BY p.tenant_id
ORDER BY p.tenant_id;

-- Review the verify output above, then COMMIT (or ROLLBACK to abort).
COMMIT;
