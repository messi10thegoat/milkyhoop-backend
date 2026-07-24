-- ============================================================================
-- V208__chat_events_table.sql
--
-- BUG: chatmode calc + agent-loop -> "kesalahan sistem" / respons kosong.
--   asyncpg UndefinedTableError: relation "chat_events" does not exist
--   (session_manager.py:1299 after_tool_call -> log_event:613 INSERT chat_events)
-- Berbeda dari telemetri fire-and-forget lain, log_event TIDAK dibungkus
-- try/except -> error naik lewat process_message -> CRASH request. Query pipeline
-- lolos (tak lewat after_tool_call); calc + agent-loop (pakai tool) kena.
--
-- AKAR: chat_events tak pernah dibuat resep (termasuk 32 tabel absen di audit).
-- Arbiter = kode: session_manager.py:615 INSERT (session_id, tenant_id,
-- event_type, action_type, payload, result); get_recent_events baca created_at.
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS chat_events (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id  UUID,
    tenant_id   TEXT NOT NULL,
    event_type  TEXT,
    action_type TEXT,
    payload     JSONB,
    result      JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_chat_events_session_created ON chat_events (session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_chat_events_tenant          ON chat_events (tenant_id);

DO $v208$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name='chat_events') THEN
        RAISE EXCEPTION 'V208: chat_events belum terbentuk';
    END IF;
    RAISE NOTICE 'V208 OK: chat_events';
END $v208$;

COMMIT;
