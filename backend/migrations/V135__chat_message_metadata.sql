-- V135: Phase D — Chat History Persistence
-- Add metadata JSONB column to chat_messages for self-contained message rendering.
-- Stores action preview snapshots, status, and other message-level context
-- so messages are self-contained (no fragile JOINs to pending_actions).

ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS metadata JSONB;

-- Index for finding messages by pending_action_id (for status updates on confirm/cancel)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_chat_messages_pending_action
ON chat_messages ((metadata->>'pending_action_id'))
WHERE metadata IS NOT NULL AND metadata->>'pending_action_id' IS NOT NULL;
