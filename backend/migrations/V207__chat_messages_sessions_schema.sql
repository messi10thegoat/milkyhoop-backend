-- ============================================================================
-- V207__chat_messages_sessions_schema.sql
--
-- BUG: chatmode error "column session_id of relation chat_messages does not exist"
--      (action_chat.py:84, unified_chat.py:7096, session_manager.py:277).
-- Chat SAMA SEKALI tak bisa menyimpan pesan -> chatmode mati total.
--
-- AKAR: chat_messages & chat_sessions dibuat sebagai STUB MINIMAL di Step 0
-- run_migrations_v9.sh (skema lama gaya Prisma: message/response/createdAt),
-- tapi kode chat memakai skema baru per-baris (session_id/role/content/
-- message_type/tool_calls/token_count/metadata + created_at snake_case).
-- Tak ada migrasi yang meng-upgrade stub -> arbiter = KODE.
--
-- METODE: ALTER-ADD (idempoten, tanpa DROP). Aman: 0 data, 0 FK menunjuk ke
-- chat_messages (diverifikasi). Kolom stub lama (message/response/createdAt/
-- updatedAt) dibiarkan vestigial (nullable, harmless). Tidak menyentuh id
-- (text, dipakai RETURNING id + chat_attachments.message_id link).
--
-- Kolom diturunkan dari INSERT/SELECT kode:
--   session_manager.py:277 INSERT (session_id, tenant_id, role, content,
--     tool_calls, tool_call_id, message_type, token_count, metadata)
--   chat_history.py: SELECT ... ORDER BY created_at; cs.status
-- ============================================================================

BEGIN;

-- 1. chat_messages: kolom skema baru
ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS session_id   UUID;
ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS role         TEXT;
ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS content      TEXT;
ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS message_type TEXT DEFAULT 'TEXT';
ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS tool_calls   JSONB;
ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS tool_call_id TEXT;
ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS token_count  INTEGER;
ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS created_at   TIMESTAMPTZ NOT NULL DEFAULT now();
ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS updated_at   TIMESTAMPTZ NOT NULL DEFAULT now();

COMMENT ON COLUMN chat_messages.message  IS 'DEPRECATED (stub Prisma lama). Kanonik = content.';
COMMENT ON COLUMN chat_messages.response IS 'DEPRECATED (stub Prisma lama). Pesan asisten = baris role=assistant.';

CREATE INDEX IF NOT EXISTS idx_chat_messages_session_created ON chat_messages (session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_chat_messages_tenant          ON chat_messages (tenant_id);

-- 2. chat_sessions: kolom status (chat_history.py membaca cs.status)
ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'active';

-- 3. Assertion fail-loud: kolom yang benar-benar ditulis kode harus ada
DO $v207$
DECLARE v_missing TEXT := '';
BEGIN
    FOR v_missing IN
        SELECT c FROM unnest(ARRAY['session_id','role','content','message_type',
                                   'tool_calls','tool_call_id','token_count','created_at']) AS c
        WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns
                          WHERE table_name='chat_messages' AND column_name=c)
    LOOP
        RAISE EXCEPTION 'V207: chat_messages.% belum terbentuk', v_missing;
    END LOOP;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name='chat_sessions' AND column_name='status') THEN
        RAISE EXCEPTION 'V207: chat_sessions.status belum terbentuk';
    END IF;
    RAISE NOTICE 'V207 OK: chat_messages skema-baru + chat_sessions.status';
END $v207$;

COMMIT;
