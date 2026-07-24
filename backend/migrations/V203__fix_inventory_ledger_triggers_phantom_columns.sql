-- ============================================================================
-- V203__fix_inventory_ledger_triggers_phantom_columns.sql
--
-- BUG: POST /api/bills/{id}/post -> 500
--        record "new" has no field "item_id"
-- Artinya SETIAP INSERT ke inventory_ledger dengan warehouse_id terisi GAGAL:
-- pembelian berstok, manufaktur, dan fulfillment semuanya terblokir.
--
-- AKAR: V043__warehouses.sql mendefinisikan trigger terhadap bentuk skema yang
-- TIDAK PERNAH ADA. Trigger merujuk NEW.item_id dan NEW.quantity_change,
-- padahal inventory_ledger memakai product_id / quantity_in / quantity_out.
-- Nol migrasi pernah menambahkan item_id atau quantity_change ke tabel ini.
-- Kelas yang sama dengan bug hash-chain V195: trigger hidup, kolomnya fiktif.
--
-- KANONIK (dibuktikan dari 3 call-site penulis Python):
--   inventory_helpers.py:171, :381, :534
--     -> INSERT INTO inventory_ledger (... product_id ... quantity_in, quantity_out ...)
--   warehouse_stock memakai item_id (sesuai Iron Laws), jadi trigger memetakan
--   inventory_ledger.product_id -> warehouse_stock.item_id.
--
-- KEPUTUSAN: perbaiki TRIGGER-nya, BUKAN menambahkan kolom kembar
-- item_id/quantity_change ke inventory_ledger. Menambah kolom akan
-- menduplikasi product_id dan membuat quantity_in/out redundan — persis pola
-- dua-kolom-kembar yang baru dibereskan di customers (V201).
--
-- PRASYARAT YANG SUDAH DIVERIFIKASI SEBELUM APPLY:
--   (1) NOL risiko dobel-hitung. Tidak ada satupun kode Python yang
--       INSERT/UPDATE warehouse_stock. Komentar arsitektur di
--       sales_invoices.py:972 menyatakan eksplisit: "warehouse_stock is a
--       DERIVED CACHE owned by the AFTER-INSERT trigger ... We MUST NOT
--       manually UPDATE warehouse_stock here or the stock double-decrements".
--       Trigger memang pemilik tunggal yang dirancang.
--   (2) ON CONFLICT punya sasaran: uq_warehouse_stock UNIQUE
--       (tenant_id, warehouse_id, item_id) dan uq_batch_warehouse UNIQUE
--       (batch_id, warehouse_id).
--   (3) Scope trigger = AFTER INSERT FOR EACH ROW saja (tanpa UPDATE/DELETE),
--       jadi tidak ada risiko penambahan ulang saat baris ledger di-update.
--
-- CAKUPAN KELAS: sapuan seluruh DB (semua trigger, semua tabel) menemukan
-- TEPAT 3 referensi NEW.<kolom> fiktif, semuanya di inventory_ledger dan
-- semuanya diperbaiki di sini. trg_update_batch_stock kena bug yang sama
-- (quantity_change) tapi belum meledak karena hanya menyala saat item ber-batch.
--
-- UNKNOWN (sengaja TIDAK dikarang): tidak dapat dipastikan apakah trigger ini
-- pernah berfungsi di droplet lama — mungkin ada migrasi hilang yang dulu
-- menambahkan kolom tersebut. Yang PASTI: pada resep saat ini dia rusak, dan
-- product_id/quantity_in/quantity_out terbukti kanonik dari 3 call-site.
-- ============================================================================

BEGIN;

-- ---------------------------------------------------------------------------
-- 1. warehouse_stock: product_id -> item_id, quantity_in/out -> delta
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.update_warehouse_stock_from_ledger()
RETURNS trigger
LANGUAGE plpgsql
AS $fn$
BEGIN
    IF NEW.warehouse_id IS NOT NULL THEN
        INSERT INTO warehouse_stock (tenant_id, warehouse_id, item_id, quantity, last_stock_date)
        VALUES (
            NEW.tenant_id,
            NEW.warehouse_id,
            NEW.product_id,
            COALESCE(NEW.quantity_in, 0) - COALESCE(NEW.quantity_out, 0),
            NOW()
        )
        ON CONFLICT (tenant_id, warehouse_id, item_id)
        DO UPDATE SET
            quantity        = warehouse_stock.quantity + EXCLUDED.quantity,
            last_stock_date = NOW(),
            updated_at      = NOW();
    END IF;
    RETURN NEW;
END;
$fn$;

COMMENT ON FUNCTION public.update_warehouse_stock_from_ledger() IS
    'V203: pemilik tunggal warehouse_stock (derived cache). Memetakan inventory_ledger.product_id -> warehouse_stock.item_id, delta = quantity_in - quantity_out.';

-- ---------------------------------------------------------------------------
-- 2. batch_warehouse_stock: quantity_in/out -> delta (bug identik)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.update_batch_stock_from_ledger()
RETURNS trigger
LANGUAGE plpgsql
AS $fn$
BEGIN
    IF NEW.batch_id IS NOT NULL AND NEW.warehouse_id IS NOT NULL THEN
        INSERT INTO batch_warehouse_stock (tenant_id, batch_id, warehouse_id, quantity, last_movement_date)
        VALUES (
            NEW.tenant_id,
            NEW.batch_id,
            NEW.warehouse_id,
            COALESCE(NEW.quantity_in, 0) - COALESCE(NEW.quantity_out, 0),
            NOW()
        )
        ON CONFLICT (batch_id, warehouse_id)
        DO UPDATE SET
            quantity           = batch_warehouse_stock.quantity + EXCLUDED.quantity,
            last_movement_date = NOW(),
            updated_at         = NOW();
    END IF;
    RETURN NEW;
END;
$fn$;

COMMENT ON FUNCTION public.update_batch_stock_from_ledger() IS
    'V203: delta batch = quantity_in - quantity_out (dulu merujuk NEW.quantity_change yang tidak pernah ada).';

-- ---------------------------------------------------------------------------
-- 3. Assertion fail-loud: NOL trigger boleh merujuk kolom NEW yang tak ada.
--    Ini menutup KELASNYA, bukan dua instance-nya.
-- ---------------------------------------------------------------------------
DO $v203$
DECLARE v_bad TEXT := '';
BEGIN
    SELECT COALESCE(string_agg(DISTINCT c.relname || '.' || t.tgname || '->NEW.' || m[1], ', '), '')
      INTO v_bad
      FROM pg_trigger t
      JOIN pg_class c ON c.oid = t.tgrelid
      JOIN pg_proc p ON p.oid = t.tgfoid
      JOIN pg_namespace n ON n.oid = c.relnamespace,
      LATERAL regexp_matches(pg_get_functiondef(p.oid), 'NEW\.([a-z_][a-z0-9_]*)', 'g') AS m
     WHERE NOT t.tgisinternal AND n.nspname = 'public'
       AND NOT EXISTS (SELECT 1 FROM information_schema.columns ic
                        WHERE ic.table_name = c.relname AND ic.column_name = m[1]);

    IF v_bad <> '' THEN
        RAISE EXCEPTION 'V203: masih ada trigger merujuk kolom NEW fiktif: %', v_bad;
    END IF;
    RAISE NOTICE 'V203 OK: nol trigger merujuk kolom NEW yang tidak ada';
END $v203$;

COMMIT;
