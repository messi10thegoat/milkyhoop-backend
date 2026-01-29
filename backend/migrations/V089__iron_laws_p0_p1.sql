-- Iron Laws P0+P1 Compliance
-- Applied: 2026-01-29
-- Compliance: 75% -> 88%

-- ============================================================
-- P0: AUDIT LOG IMMUTABILITY (Law 12)
-- ============================================================

CREATE OR REPLACE FUNCTION prevent_audit_log_modification()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'Audit logs are immutable and cannot be modified or deleted';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_prevent_audit_log_update ON audit_logs;
DROP TRIGGER IF EXISTS trg_prevent_audit_log_delete ON audit_logs;

CREATE TRIGGER trg_prevent_audit_log_update
BEFORE UPDATE ON audit_logs
FOR EACH ROW EXECUTE FUNCTION prevent_audit_log_modification();

CREATE TRIGGER trg_prevent_audit_log_delete
BEFORE DELETE ON audit_logs
FOR EACH ROW EXECUTE FUNCTION prevent_audit_log_modification();

-- ============================================================
-- P0: PERIOD LOCK ENFORCEMENT (Law 5)
-- ============================================================

CREATE OR REPLACE FUNCTION prevent_closed_period_journal()
RETURNS TRIGGER AS $$
BEGIN
    IF is_period_locked(NEW.tenant_id, NEW.journal_date) THEN
        RAISE EXCEPTION 'Cannot create journal entry in locked period for date %', NEW.journal_date;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_prevent_closed_period_journal ON journal_entries;

CREATE TRIGGER trg_prevent_closed_period_journal
BEFORE INSERT ON journal_entries
FOR EACH ROW EXECUTE FUNCTION prevent_closed_period_journal();

-- ============================================================
-- P1: IDEMPOTENCY KEYS (Law 14)
-- ============================================================

ALTER TABLE receive_payments ADD COLUMN IF NOT EXISTS idempotency_key TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS uq_receive_payments_idempotency 
  ON receive_payments(tenant_id, idempotency_key) 
  WHERE idempotency_key IS NOT NULL;

ALTER TABLE bill_payments ADD COLUMN IF NOT EXISTS idempotency_key TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS uq_bill_payments_idempotency 
  ON bill_payments(tenant_id, idempotency_key) 
  WHERE idempotency_key IS NOT NULL;

ALTER TABLE expenses ADD COLUMN IF NOT EXISTS idempotency_key TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS uq_expenses_idempotency 
  ON expenses(tenant_id, idempotency_key) 
  WHERE idempotency_key IS NOT NULL;
