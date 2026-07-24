-- ============================================================================
-- V209__chat_pending_action_and_telemetry_partition.sql
--
-- Dua bug chatmode dari DB murni-resep:
--
-- 1. CREATE via chat -> "Gagal menyimpan action: column is_direct of relation
--    pending_actions does not exist" (unified_chat.py:5495 INSERT ... is_direct).
--    Kolom tak pernah dibuat resep. is_direct BOOLEAN (kode menulis True).
--
-- 2. Telemetri intent -> "[TELEMETRY] no partition of relation
--    intent_decision_log found for row". Tabel RANGE-partitioned by ts; partisi
--    hanya s/d 2026_06, insert bulan berjalan (2026_07+) gagal. Non-fatal
--    (try/except) tapi tiap pesan gagal-log. Fix: partisi DEFAULT (self-healing,
--    tangkap semua bulan tanpa maintenance cron yang hilang bersama droplet).
-- ============================================================================

BEGIN;

-- 1. pending_actions.is_direct
ALTER TABLE pending_actions ADD COLUMN IF NOT EXISTS is_direct BOOLEAN DEFAULT false;

-- 2. intent_decision_log partisi DEFAULT (kalau belum ada)
DO $part$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_inherits i
         JOIN pg_class c ON c.oid = i.inhrelid
        WHERE i.inhparent = 'intent_decision_log'::regclass
          AND c.relname = 'intent_decision_log_default'
    ) THEN
        EXECUTE 'CREATE TABLE intent_decision_log_default PARTITION OF intent_decision_log DEFAULT';
        RAISE NOTICE 'partisi DEFAULT intent_decision_log dibuat';
    END IF;
END $part$;

DO $v209$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name='pending_actions' AND column_name='is_direct') THEN
        RAISE EXCEPTION 'V209: pending_actions.is_direct belum terbentuk';
    END IF;
    RAISE NOTICE 'V209 OK: is_direct + intent_decision_log DEFAULT partition';
END $v209$;

COMMIT;
