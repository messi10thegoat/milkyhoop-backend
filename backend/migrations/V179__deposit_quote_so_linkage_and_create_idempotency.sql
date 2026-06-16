-- FIX_P3_BRIDGE 2026-06-16
-- P3 (bridge) of Quote -> Uang Muka (DP) -> Invoice initiative.
--
-- ZERO NEW ACCOUNTING. This migration is pure FK-linkage + create-idempotency
-- plumbing on customer_deposits. No journal/CoA changes.
--
-- 1. Link a customer_deposit to the obligation spine it was taken against:
--    - quote_id        : deposit taken at quote-accepted stage
--    - sales_order_id  : deposit taken at (or propagated to) the SO stage
--    Both nullable, FK ON DELETE SET NULL (a deleted quote/SO must not orphan
--    a money-in deposit row).
--
-- 2. Create idempotency: idempotency_key (text, nullable) + partial UNIQUE
--    index. Mirrors the receive_payments convention (column on the table +
--    pre-check inside the advisory-locked create txn). A money-in double-click
--    must NOT double-record cash.
--
-- Additive + idempotent (IF NOT EXISTS everywhere). Safe to re-run.

-- --------------------------------------------------------------------------
-- 1. Spine linkage columns
-- --------------------------------------------------------------------------
ALTER TABLE customer_deposits
    ADD COLUMN IF NOT EXISTS quote_id uuid NULL;

ALTER TABLE customer_deposits
    ADD COLUMN IF NOT EXISTS sales_order_id uuid NULL;

-- FKs (guarded: add only if not already present). ON DELETE SET NULL so the
-- deposit (money received) survives deletion of the originating quote/SO.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_customer_deposits_quote_id'
    ) THEN
        ALTER TABLE customer_deposits
            ADD CONSTRAINT fk_customer_deposits_quote_id
            FOREIGN KEY (quote_id) REFERENCES quotes(id) ON DELETE SET NULL;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_customer_deposits_sales_order_id'
    ) THEN
        ALTER TABLE customer_deposits
            ADD CONSTRAINT fk_customer_deposits_sales_order_id
            FOREIGN KEY (sales_order_id) REFERENCES sales_orders(id) ON DELETE SET NULL;
    END IF;
END$$;

-- Helper indexes for spine lookups (propagation UPDATE + applicable-deposits).
CREATE INDEX IF NOT EXISTS idx_customer_deposits_quote_id
    ON customer_deposits (quote_id) WHERE quote_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_customer_deposits_sales_order_id
    ON customer_deposits (sales_order_id) WHERE sales_order_id IS NOT NULL;

-- --------------------------------------------------------------------------
-- 2. Create idempotency
-- --------------------------------------------------------------------------
ALTER TABLE customer_deposits
    ADD COLUMN IF NOT EXISTS idempotency_key text NULL;

-- Partial UNIQUE: one (tenant, key) at most, ignoring NULL keys (legacy + any
-- create that does not pass a key). This index is what backs the race guard:
-- a concurrent second create with the same key hits a unique violation, which
-- the create handler catches and converts into "return existing".
CREATE UNIQUE INDEX IF NOT EXISTS uq_customer_deposits_tenant_idempotency_key
    ON customer_deposits (tenant_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;
