-- V143: ADR P4 v1.3 — pending_clarification slot persistence
-- Ref: docs/plans/2026-04-22-adr-p4-clarification-slot.md

ALTER TABLE chat_session_state
  ADD COLUMN IF NOT EXISTS pending_clarification JSONB NULL,
  ADD COLUMN IF NOT EXISTS pending_clarification_expires_at TIMESTAMPTZ NULL;

CREATE INDEX IF NOT EXISTS idx_chat_session_state_pending_clar_expires
  ON chat_session_state (pending_clarification_expires_at)
  WHERE pending_clarification IS NOT NULL;

ALTER TABLE intent_decision_log
  ADD COLUMN IF NOT EXISTS clarification_event TEXT NULL;
