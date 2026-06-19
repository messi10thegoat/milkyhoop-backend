-- V187: AI short-title per chat session (set-once, never clobbered by rolling summary)
-- Additive, idempotent, nullable. Safe to re-run.
ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS title text;
