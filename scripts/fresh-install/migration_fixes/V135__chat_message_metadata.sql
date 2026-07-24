-- FIXDIR override of V135 (2026-07-25): drop CONCURRENTLY (see V017 fix).
-- Step 0 already provides chat_messages.metadata, so the ALTER is a no-op.
ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS metadata JSONB;
CREATE INDEX IF NOT EXISTS idx_chat_messages_pending_action
ON chat_messages ((metadata->>'pending_action_id'))
WHERE metadata IS NOT NULL AND metadata->>'pending_action_id' IS NOT NULL;
