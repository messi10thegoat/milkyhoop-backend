-- ============================================================================
-- V213 — create chat_attachments (recovery read-path drift; MISSING TABLE class)
-- ----------------------------------------------------------------------------
-- Same defect class as V212 (withholding_tax_records): a table the CODE depends
-- on but that NO migration ever creates. V207 only ALTERs chat_messages /
-- chat_sessions and MENTIONS chat_attachments in a COMMENT (line 17) — it never
-- CREATEs it. The old droplet DB had the table created ad-hoc (contamination);
-- a clean rebuild from migrations loses it, so on the recovered milkydb it does
-- not exist.
--
-- Symptom: GET /api/v3/chat/sessions/{id}/messages → 500
--   asyncpg.exceptions.UndefinedTableError: relation "chat_attachments" does not exist
-- because chat_history._enrich_messages (chat_history.py:113) SELECTs from it on
-- EVERY per-session history fetch → chat history never renders for any session.
-- Also: chat file uploads silently fail to persist their attachment rows.
--
-- ARBITER = CODE (never the contaminated old DB). Column contract derived from:
--   • WRITE  unified_chat._save_chat_attachments (unified_chat.py:337):
--       INSERT (id, tenant_id, message_id, file_name, content_type, file_size, storage_key)
--       — casts $1::uuid (id), $3::uuid (message_id); tenant_id/file_name/
--         content_type TEXT; file_size = int; storage_key TEXT.
--   • READ   chat_history._enrich_messages (chat_history.py:113):
--       SELECT message_id, file_name, content_type, file_size, storage_key,
--              thumbnail_url  ... ORDER BY created_at ASC
--       WHERE message_id = ANY($1::uuid[])
--       — so message_id is UUID; thumbnail_url + created_at must also exist.
--
-- Notes / decisions (consistent with the recovery playbook + V211/V212):
--   • message_id is UUID because BOTH code paths cast to ::uuid. No FK to
--     chat_messages: chat_messages.id is TEXT (type mismatch would block the FK),
--     and the recovery playbook keeps these sidecars FK/CHECK-free to avoid the
--     over-constraint 500s (cf. V197/V205). Tenant isolation is upheld upstream
--     (get_session_messages verifies session ownership before the id list is
--     built; the write path scopes by session_id + tenant_id).
--   • thumbnail_url is read but never written → nullable.
--   • No RLS: sibling chat_messages / chat_sessions have relrowsecurity=false;
--     gateway connects BYPASSRLS (Law 24). Matches V211/V212.
--   • Idempotent (IF NOT EXISTS) so a re-run / fresh-install is a no-op.
-- ============================================================================

CREATE TABLE IF NOT EXISTS chat_attachments (
    id            UUID PRIMARY KEY,
    tenant_id     TEXT        NOT NULL,
    message_id    UUID        NOT NULL,
    file_name     TEXT,
    content_type  TEXT,
    file_size     BIGINT,
    storage_key   TEXT,
    thumbnail_url TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Read path filters WHERE message_id = ANY(...) then ORDER BY created_at ASC.
CREATE INDEX IF NOT EXISTS idx_chat_attachments_message_id
    ON chat_attachments (message_id, created_at);
-- Tenant-scoped scans / housekeeping.
CREATE INDEX IF NOT EXISTS idx_chat_attachments_tenant
    ON chat_attachments (tenant_id);

-- Verify the column contract the code needs actually exists.
DO $$
DECLARE
    v_missing TEXT;
BEGIN
    IF to_regclass('public.chat_attachments') IS NULL THEN
        RAISE EXCEPTION 'V213: chat_attachments belum terbentuk';
    END IF;
    FOR v_missing IN
        SELECT unnest(ARRAY['id','tenant_id','message_id','file_name',
                            'content_type','file_size','storage_key',
                            'thumbnail_url','created_at'])
        EXCEPT
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'chat_attachments'
    LOOP
        RAISE EXCEPTION 'V213: chat_attachments.% belum terbentuk', v_missing;
    END LOOP;
    RAISE NOTICE 'V213 OK: chat_attachments dibuat (9 kolom, 2 index)';
END $$;
