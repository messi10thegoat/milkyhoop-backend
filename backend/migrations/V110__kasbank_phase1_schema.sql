-- V110: Kas & Bank Phase 1 - Enhanced bank_transactions schema
-- Adds status workflow, origin tracking, and reconciliation status

BEGIN;

-- ============================================================
-- 1. Add new columns to bank_transactions (idempotent)
-- ============================================================

ALTER TABLE bank_transactions
  ADD COLUMN IF NOT EXISTS status VARCHAR(10) NOT NULL DEFAULT 'POSTED'
    CHECK (status IN ('DRAFT', 'POSTED', 'VOIDED')),
  ADD COLUMN IF NOT EXISTS origin_type VARCHAR(10) NOT NULL DEFAULT 'SYSTEM'
    CHECK (origin_type IN ('SYSTEM', 'MANUAL')),
  ADD COLUMN IF NOT EXISTS source_module VARCHAR(30) NULL,
  ADD COLUMN IF NOT EXISTS transaction_number VARCHAR(20) NULL,
  ADD COLUMN IF NOT EXISTS posted_by UUID NULL,
  ADD COLUMN IF NOT EXISTS posted_at TIMESTAMPTZ NULL,
  ADD COLUMN IF NOT EXISTS voided_by UUID NULL,
  ADD COLUMN IF NOT EXISTS voided_at TIMESTAMPTZ NULL,
  ADD COLUMN IF NOT EXISTS void_reason VARCHAR(500) NULL,
  ADD COLUMN IF NOT EXISTS reconciliation_status VARCHAR(15) NOT NULL DEFAULT 'UNRECONCILED'
    CHECK (reconciliation_status IN ('UNRECONCILED', 'RECONCILED'));

-- ============================================================
-- 2. Add transfer_number to bank_transfers (if not exists)
-- ============================================================
-- Already exists, skipping for safety
ALTER TABLE bank_transfers
  ADD COLUMN IF NOT EXISTS transfer_number VARCHAR(30) NULL;

-- ============================================================
-- 3. Backfill existing rows
-- ============================================================

UPDATE bank_transactions
SET posted_at = COALESCE(posted_at, created_at),
    source_module = COALESCE(source_module, reference_type, 'LEGACY')
WHERE status = 'POSTED';

-- ============================================================
-- 4. Add indexes
-- ============================================================

CREATE INDEX IF NOT EXISTS idx_bank_txn_account_status_date
ON bank_transactions(bank_account_id, status, transaction_date DESC);

CREATE UNIQUE INDEX IF NOT EXISTS idx_bank_txn_number_tenant
ON bank_transactions(tenant_id, transaction_number) WHERE transaction_number IS NOT NULL;

COMMIT;
