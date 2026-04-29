-- V144: Period persistence (sticky resolved period across turns)
-- Extends ADR P4 (P4.1 addendum). Resolves B_arap follow-up regression.
--
-- current_period was reserved as varchar(10) but never written. Widen it to
-- TEXT so we can store the full resolved period dict as JSON
-- ({kind,start_date,end_date,label}). Add expires_at sibling for 30-min TTL.

ALTER TABLE chat_session_state ALTER COLUMN current_period TYPE TEXT;
ALTER TABLE chat_session_state ADD COLUMN IF NOT EXISTS current_period_expires_at TIMESTAMPTZ NULL;
CREATE INDEX IF NOT EXISTS idx_chat_session_state_current_period_expires
    ON chat_session_state(current_period_expires_at)
    WHERE current_period IS NOT NULL;
