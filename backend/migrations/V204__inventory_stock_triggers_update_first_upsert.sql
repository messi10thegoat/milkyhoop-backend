-- ============================================================================
-- V204__inventory_stock_triggers_update_first_upsert.sql
--
-- BUG: POST /api/production/{id}/issue-materials -> 500
--        new row for relation "warehouse_stock" violates check constraint
--        "chk_ws_quantity"  (production.py:1822)
-- Artinya SETIAP pengeluaran stok gagal: material issue, fulfillment, dan
-- stock-out apa pun. Yang selama ini "berhasil" hanya pergerakan MASUK.
--
-- AKAR (dibuktikan empiris, bukan inspeksi):
--   Trigger update_warehouse_stock_from_ledger (dan sibling _batch_) memakai
--     INSERT ... VALUES (delta) ON CONFLICT DO UPDATE SET quantity = quantity + delta
--   Untuk pergerakan KELUAR, delta negatif. PostgreSQL mengevaluasi CHECK
--   (quantity >= 0) pada TUPLE YANG DIUSULKAN (delta = -75) SEBELUM ON CONFLICT
--   mengalihkan ke UPDATE. Tuple usulan -75 langsung ditolak chk_ws_quantity,
--   meski hasil akhir (100 - 75 = 25) positif dan sah.
--
--   Uji empiris di milkydb (2026-07-24):
--     INSERT..ON CONFLICT delta -75 pada baris qty 100  -> check_violation
--     UPDATE-dulu delta -75 pada baris qty 100          -> OK, jadi 25
--
-- Ini instance KETIGA dari kelas "trigger V043-era rusak sejak fresh install":
--   V195 hash-chain (kolom hilang), V203 (kolom NEW fiktif), V204 (pola upsert).
--   V204 LOLOS grep kolom fiktif karena kolomnya benar — logikanya yang salah.
--   Hanya uji fungsional 2-arah yang menangkapnya.
--
-- FIX: UPDATE-dulu; kalau baris belum ada baru INSERT (dengan ON CONFLICT tetap
-- dipertahankan sebagai pengaman race di cabang INSERT). Bila baris BELUM ADA
-- dan delta negatif, INSERT tuple negatif tetap DITOLAK chk_ws_quantity — itu
-- perilaku BENAR (Law 13 anti-oversell: tak bisa mengeluarkan stok yang belum
-- pernah masuk). V204 hanya memperbaiki URUTAN EVALUASI, TIDAK melemahkan CHECK.
-- ============================================================================

BEGIN;

-- ---------------------------------------------------------------------------
-- 1. warehouse_stock
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.update_warehouse_stock_from_ledger()
RETURNS trigger
LANGUAGE plpgsql
AS $fn$
DECLARE
    v_delta NUMERIC;
BEGIN
    IF NEW.warehouse_id IS NOT NULL THEN
        v_delta := COALESCE(NEW.quantity_in, 0) - COALESCE(NEW.quantity_out, 0);

        -- UPDATE dulu: kalau baris ada, CHECK dievaluasi pada hasil (quantity + delta),
        -- bukan pada tuple usulan berisi delta mentah.
        UPDATE warehouse_stock
           SET quantity        = quantity + v_delta,
               last_stock_date = NOW(),
               updated_at       = NOW()
         WHERE tenant_id = NEW.tenant_id
           AND warehouse_id = NEW.warehouse_id
           AND item_id = NEW.product_id;

        -- Baris belum ada: INSERT. ON CONFLICT dipertahankan sebagai pengaman
        -- race (dua ledger row untuk item baru yang sama, konkuren). Delta
        -- negatif di sini DITOLAK chk_ws_quantity — benar (anti-oversell).
        IF NOT FOUND THEN
            INSERT INTO warehouse_stock (tenant_id, warehouse_id, item_id, quantity, last_stock_date)
            VALUES (NEW.tenant_id, NEW.warehouse_id, NEW.product_id, v_delta, NOW())
            ON CONFLICT (tenant_id, warehouse_id, item_id)
            DO UPDATE SET
                quantity        = warehouse_stock.quantity + EXCLUDED.quantity,
                last_stock_date = NOW(),
                updated_at      = NOW();
        END IF;
    END IF;
    RETURN NEW;
END;
$fn$;

COMMENT ON FUNCTION public.update_warehouse_stock_from_ledger() IS
    'V204: pemilik tunggal warehouse_stock. UPDATE-dulu-baru-INSERT agar outflow (delta<0) tidak ditolak chk_ws_quantity pada tuple usulan; over-issue tetap ditolak (Law 13).';

-- ---------------------------------------------------------------------------
-- 2. batch_warehouse_stock (pola identik)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.update_batch_stock_from_ledger()
RETURNS trigger
LANGUAGE plpgsql
AS $fn$
DECLARE
    v_delta NUMERIC;
BEGIN
    IF NEW.batch_id IS NOT NULL AND NEW.warehouse_id IS NOT NULL THEN
        v_delta := COALESCE(NEW.quantity_in, 0) - COALESCE(NEW.quantity_out, 0);

        UPDATE batch_warehouse_stock
           SET quantity           = quantity + v_delta,
               last_movement_date = NOW(),
               updated_at         = NOW()
         WHERE tenant_id = NEW.tenant_id
           AND batch_id = NEW.batch_id
           AND warehouse_id = NEW.warehouse_id;

        IF NOT FOUND THEN
            INSERT INTO batch_warehouse_stock (tenant_id, batch_id, warehouse_id, quantity, last_movement_date)
            VALUES (NEW.tenant_id, NEW.batch_id, NEW.warehouse_id, v_delta, NOW())
            ON CONFLICT (batch_id, warehouse_id)
            DO UPDATE SET
                quantity           = batch_warehouse_stock.quantity + EXCLUDED.quantity,
                last_movement_date = NOW(),
                updated_at         = NOW();
        END IF;
    END IF;
    RETURN NEW;
END;
$fn$;

COMMENT ON FUNCTION public.update_batch_stock_from_ledger() IS
    'V204: delta batch = quantity_in - quantity_out, UPDATE-dulu-baru-INSERT (sama seperti warehouse_stock).';

COMMIT;
