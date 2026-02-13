-- V106: Action Mode Foundation Tables
-- Sprint 1: Core tables for the MilkyHoop Agentic Accounting system
-- Date: 2026-02-13
-- NOTE: Sections 3 and 5 (audit_logs ALTER + trigger) require running as
--       the 'postgres' user since audit_logs is owned by postgres.
--       Sections 1, 2, 4 run fine as milkyadmin.

-- ============================================================
-- 1. pending_actions table
-- ============================================================
CREATE TABLE IF NOT EXISTS pending_actions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id VARCHAR(100) NOT NULL,
  user_id TEXT NOT NULL,
  conversation_id TEXT,

  -- Action details
  action_id VARCHAR(50) NOT NULL,
  action_type VARCHAR(50) NOT NULL,
  action_category VARCHAR(20) NOT NULL DEFAULT 'DOCUMENT',
  action_plan JSONB NOT NULL,
  validation_result JSONB,
  dry_run_preview JSONB,

  -- State (optimistic locking)
  status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
  version INTEGER NOT NULL DEFAULT 1,

  -- Idempotency
  idempotency_key VARCHAR(64),

  -- Timing
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  expires_at TIMESTAMPTZ NOT NULL,
  confirmed_at TIMESTAMPTZ,
  executed_at TIMESTAMPTZ,

  -- Result
  result JSONB,
  error_message TEXT,

  -- Audit correlation
  trace_id UUID DEFAULT gen_random_uuid(),
  audit_log_id UUID,

  CONSTRAINT valid_pending_status CHECK (
    status IN ('PENDING', 'EXECUTING', 'COMPLETED', 'FAILED', 'CANCELLED', 'EXPIRED')
  ),
  CONSTRAINT valid_action_category CHECK (
    action_category IN ('MASTER_DATA', 'DOCUMENT', 'PAYMENT', 'ACCOUNTING', 'READ')
  )
);

-- Indexes for pending_actions
CREATE INDEX idx_pending_actions_tenant_status ON pending_actions(tenant_id, status);
CREATE INDEX idx_pending_actions_expires ON pending_actions(expires_at) WHERE status = 'PENDING';
CREATE INDEX idx_pending_actions_trace ON pending_actions(trace_id);
CREATE UNIQUE INDEX idx_pending_actions_idempotency ON pending_actions(tenant_id, idempotency_key) WHERE idempotency_key IS NOT NULL;

-- ============================================================
-- 2. idempotency_keys table
-- ============================================================
CREATE TABLE IF NOT EXISTS idempotency_keys (
  key VARCHAR(64) PRIMARY KEY,
  tenant_id VARCHAR(100) NOT NULL,
  pending_action_id UUID REFERENCES pending_actions(id),
  result JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  expires_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX idx_idempotency_expires ON idempotency_keys(expires_at);
CREATE INDEX idx_idempotency_tenant ON idempotency_keys(tenant_id);

-- ============================================================
-- 3. Enhance audit_logs table (ALTER, not recreate - has data!)
-- ============================================================
ALTER TABLE audit_logs
  ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(100),
  ADD COLUMN IF NOT EXISTS trace_id UUID,
  ADD COLUMN IF NOT EXISTS conversation_id TEXT,
  ADD COLUMN IF NOT EXISTS pending_action_id UUID,
  ADD COLUMN IF NOT EXISTS action_id VARCHAR(50),
  ADD COLUMN IF NOT EXISTS entity_type VARCHAR(50),
  ADD COLUMN IF NOT EXISTS entity_id UUID,
  ADD COLUMN IF NOT EXISTS entity_number VARCHAR(50),
  ADD COLUMN IF NOT EXISTS input_data JSONB,
  ADD COLUMN IF NOT EXISTS action_plan JSONB,
  ADD COLUMN IF NOT EXISTS validation_result JSONB,
  ADD COLUMN IF NOT EXISTS execution_result JSONB,
  ADD COLUMN IF NOT EXISTS error_code VARCHAR(50),
  ADD COLUMN IF NOT EXISTS duration_ms INTEGER,
  ADD COLUMN IF NOT EXISTS checksum VARCHAR(64),
  ADD COLUMN IF NOT EXISTS user_role VARCHAR(50);

-- New indexes on audit_logs
CREATE INDEX IF NOT EXISTS idx_audit_tenant_time ON audit_logs(tenant_id, "createdAt" DESC);
CREATE INDEX IF NOT EXISTS idx_audit_trace ON audit_logs(trace_id);
CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit_logs(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_logs(action_id, "createdAt" DESC);

-- ============================================================
-- 4. conversation_actions table
-- ============================================================
CREATE TABLE IF NOT EXISTS conversation_actions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id VARCHAR(100) NOT NULL,
  conversation_id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  message_text TEXT,
  intent_type VARCHAR(20),
  action_type VARCHAR(50),
  confidence DOUBLE PRECISION,
  pending_action_id UUID REFERENCES pending_actions(id),
  response_type VARCHAR(30),
  response_text TEXT,
  trace_id UUID,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_conv_actions_tenant ON conversation_actions(tenant_id, conversation_id, created_at DESC);
CREATE INDEX idx_conv_actions_trace ON conversation_actions(trace_id);

-- ============================================================
-- 5. Immutability trigger for audit_logs (Iron Law 12)
-- ============================================================
CREATE OR REPLACE FUNCTION prevent_audit_mutation()
RETURNS TRIGGER AS $$
BEGIN
  RAISE EXCEPTION 'Audit logs are immutable. Cannot UPDATE or DELETE.';
  RETURN NULL;
END;
$$ LANGUAGE plpgsql;

-- Only create trigger if not exists
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_audit_immutable') THEN
    CREATE TRIGGER trg_audit_immutable
      BEFORE UPDATE OR DELETE ON audit_logs
      FOR EACH ROW
      EXECUTE FUNCTION prevent_audit_mutation();
  END IF;
END
$$;
