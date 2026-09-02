-- ============================================================================
-- V226 — create product_units (satuan master data)
-- ----------------------------------------------------------------------------
-- CLASS A recovery (code-referenced, DDL in no migration) — see
-- backend/migrations/RECOVERY_MISSING_TABLES_BACKLOG.md.
--
-- PRECONDITION SATISFIED: the backlog policy is "create only on an OBSERVED
-- real failure". Observed 2026-09-02: owner pressed `+ Buat satuan "Kilogram"`
-- in Tambah Item repeatedly, nothing happened. Measured cause:
--     to_regclass('public.product_units')  -> NULL
--     to_regclass('public.unit_conversions') -> unit_conversions (exists)
-- Every endpoint in routers/units.py that touches product_units returns 500.
--
-- ARBITER = CODE: backend/api_gateway/app/routers/units.py (653 lines, read in
-- full). Per-column justification below cites the exact line.
--
-- NOTE ON THE ROLLED-BACK V215 (commit fba68fa9, reverted by f1f275d0): it
-- created product_units WITHOUT `is_active` and WITHOUT `updated_at`. Both are
-- read and written by units.py. Had V215 stayed, list/dropdown/update/delete
-- would STILL have returned 500 and the DO-block would have reported OK — a
-- silently-wrong schema, exactly the failure mode the rollback was meant to
-- avoid. This file does NOT reuse V215's number or its column list.
--
-- Columns (every one traced to units.py):
--   id           UUID  — line 214/285 path param typed `unit_id: UUID`, passed
--                        to asyncpg as a uuid object against `WHERE id = $1`
--                        (l.222/242/292). A text column would raise
--                        DataError. INSERT (l.186/640) omits id and does
--                        `RETURNING id` -> needs a default.
--   tenant_id    TEXT  — filtered in every query (l.47,114,126,175,222,...).
--                        TEXT matches products.tenant_id and
--                        unit_conversions.tenant_id (both text in live schema).
--   name         TEXT  — SELECT l.66,112,292; INSERT l.186,640; UPDATE l.234;
--                        ORDER BY name ASC l.69,116,127.
--   abbreviation TEXT  — SELECT l.66,112,222,292; INSERT l.186,640; UPDATE
--                        l.251; LOWER(abbreviation) lookups l.175,242,634.
--   is_system    BOOL  — SELECT l.66,222,292; INSERT supplies false (l.187) /
--                        true (l.640); ORDER BY is_system DESC l.69,116,127;
--                        delete guard l.298.
--   is_active    BOOL  — SELECT l.66; filter `is_active = $n` l.57;
--                        `is_active = true` l.114,126; soft-delete UPDATE
--                        l.316. DEFAULT true is REQUIRED, not cosmetic: the
--                        INSERT never supplies is_active, and the dropdown
--                        filters is_active = true — without the default,
--                        every newly created unit would be invisible.
--   created_at   TSTZ  — SELECT l.66.
--   updated_at   TSTZ  — UPDATE ... `updated_at = NOW()` l.258 and l.316.
--
-- DECISIONS (NOT recovered from code — stated explicitly per backlog policy):
--   D1. `idx_product_units_tenant` — a performance index. No code requires it;
--       every query filters tenant_id.
--   D2. `uq_product_units_tenant_abbr` UNIQUE (tenant_id, lower(abbreviation))
--       — enforces in the DB the duplicate rule units.py already enforces in
--       application code (l.174-182, l.241-250, l.633-638). Safe against the
--       soft-delete path: the dup check does NOT filter is_active, so a
--       deactivated abbreviation is rejected with 400 before any INSERT.
--   D3. Column types TEXT rather than VARCHAR(50)/VARCHAR(20). POST validates
--       those lengths (l.165-170) but PATCH does NOT (l.233-236), so a
--       length-capped type would turn a PATCH into a 500. TEXT fails open,
--       matching every recovered sibling (V212/V213/V214).
--   D4. NO row-level security. Verified convention, not assumption:
--       pg_class.relrowsecurity = false for unit_conversions, products,
--       chat_attachments, sales_invoice_attachments,
--       user_explicit_preferences and withholding_tax_records; and
--       pg_policies has ZERO rows for unit_conversions/products. Tenant
--       isolation on this table is enforced by the WHERE tenant_id = $1 in
--       every units.py query, as it is for unit_conversions.
--   D5. No FK to any table, and none is possible: unit_conversions stores unit
--       names as varchar(50) strings (base_unit/conversion_unit), and
--       products.satuan is varchar(50) compared via LOWER(satuan) = abbreviation
--       (l.305). Nothing in the schema references product_units.id.
--   D6. No seed rows. Seeding is an application concern — units.py exposes
--       POST /api/units/seed (l.624) with its own 20-unit default list.
--
-- Idempotent (IF NOT EXISTS throughout). Rollback: V226__rollback.sql.
-- ============================================================================

CREATE TABLE IF NOT EXISTS product_units (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    TEXT        NOT NULL,
    name         TEXT        NOT NULL,
    abbreviation TEXT        NOT NULL,
    is_system    BOOLEAN     NOT NULL DEFAULT false,
    is_active    BOOLEAN     NOT NULL DEFAULT true,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_product_units_tenant
    ON product_units (tenant_id);

CREATE UNIQUE INDEX IF NOT EXISTS uq_product_units_tenant_abbr
    ON product_units (tenant_id, lower(abbreviation));

-- --------------------------------------------------------------------------
-- Verification. Asserts the FULL contract units.py needs, including the two
-- columns V215 omitted. Proven able to FAIL: running this block against the
-- pre-migration database raises 'V226: product_units.id belum terbentuk'.
-- --------------------------------------------------------------------------
DO $$
DECLARE v_missing TEXT;
BEGIN
    FOR v_missing IN
        SELECT unnest(ARRAY['id','tenant_id','name','abbreviation',
                            'is_system','is_active','created_at','updated_at'])
        EXCEPT
        SELECT column_name FROM information_schema.columns
         WHERE table_schema='public' AND table_name='product_units'
    LOOP
        RAISE EXCEPTION 'V226: product_units.% belum terbentuk', v_missing;
    END LOOP;

    IF (SELECT data_type FROM information_schema.columns
         WHERE table_schema='public' AND table_name='product_units'
           AND column_name='id') <> 'uuid' THEN
        RAISE EXCEPTION 'V226: product_units.id harus uuid (units.py mengirim objek UUID)';
    END IF;

    IF (SELECT column_default FROM information_schema.columns
         WHERE table_schema='public' AND table_name='product_units'
           AND column_name='is_active') IS DISTINCT FROM 'true' THEN
        RAISE EXCEPTION 'V226: product_units.is_active wajib DEFAULT true (INSERT tidak mengisinya, dropdown menyaringnya)';
    END IF;

    IF (SELECT column_default FROM information_schema.columns
         WHERE table_schema='public' AND table_name='product_units'
           AND column_name='id') IS NULL THEN
        RAISE EXCEPTION 'V226: product_units.id wajib punya DEFAULT (INSERT menghilangkan id)';
    END IF;

    RAISE NOTICE 'V226 OK: product_units lengkap (8 kolom, id uuid, is_active default true)';
END $$;
