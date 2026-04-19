-- V137__three_event_revenue_recognition.sql
-- 3-Event Revenue Recognition rollout — Phase 1 / Agent A1
-- Architecture v1.3 Final — implementation Step 1
-- NOTE: renamed from planned V135 because V135 (chat_message_metadata) and V136
-- (llm_router_telemetry) already exist on dev. Next free slot is V137.
-- All statements idempotent. Wrap entire file in BEGIN/COMMIT for atomic apply.

BEGIN;

-- =========================================================================
-- 1a. CoA account 2-10700 "Pendapatan Diterima Dimuka" for every tenant
-- =========================================================================
-- chart_of_accounts uses parent_code (text) — confirmed via information_schema.
INSERT INTO chart_of_accounts (
    id, tenant_id, account_code, name, account_type, normal_balance,
    parent_code, level, is_header, is_active, is_system, created_at, updated_at
)
SELECT
    gen_random_uuid(),
    t.tenant_id,
    '2-10700',
    'Pendapatan Diterima Dimuka',
    'LIABILITY',
    'CREDIT',
    '2-10000',
    3,
    false,
    true,
    true,
    NOW(),
    NOW()
FROM (SELECT DISTINCT tenant_id FROM chart_of_accounts WHERE account_code = '2-10000') t
WHERE NOT EXISTS (
    SELECT 1 FROM chart_of_accounts c2
    WHERE c2.tenant_id = t.tenant_id AND c2.account_code = '2-10700'
);

-- =========================================================================
-- 1b. sales_invoices columns + status CHECKs
-- =========================================================================
ALTER TABLE sales_invoices
    ADD COLUMN IF NOT EXISTS fulfillment_status VARCHAR DEFAULT 'pending',
    ADD COLUMN IF NOT EXISTS revenue_status VARCHAR DEFAULT 'deferred',
    ADD COLUMN IF NOT EXISTS fulfillment_journal_id UUID REFERENCES journal_entries(id),
    ADD COLUMN IF NOT EXISTS revenue_journal_id UUID REFERENCES journal_entries(id),
    ADD COLUMN IF NOT EXISTS total_fulfilled_qty NUMERIC(18,2) DEFAULT 0,
    ADD COLUMN IF NOT EXISTS total_recognized_amount NUMERIC(18,2) DEFAULT 0;

DO $$
BEGIN
    ALTER TABLE sales_invoices
        ADD CONSTRAINT chk_si_fulfillment_status
        CHECK (fulfillment_status IN ('pending','partial','fulfilled','not_applicable'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$
BEGIN
    ALTER TABLE sales_invoices
        ADD CONSTRAINT chk_si_revenue_status
        CHECK (revenue_status IN ('deferred','partial','recognized'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- =========================================================================
-- 1c. sales_invoice_items columns
-- =========================================================================
ALTER TABLE sales_invoice_items
    ADD COLUMN IF NOT EXISTS allocated_amount NUMERIC(18,2),
    ADD COLUMN IF NOT EXISTS fulfilled_qty NUMERIC(18,2) DEFAULT 0,
    ADD COLUMN IF NOT EXISTS recognized_amount NUMERIC(18,2) DEFAULT 0;

-- =========================================================================
-- 1d. invoice_fulfillments + invoice_fulfillment_items
-- =========================================================================
CREATE TABLE IF NOT EXISTS invoice_fulfillments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    invoice_id UUID NOT NULL REFERENCES sales_invoices(id),
    fulfillment_number VARCHAR NOT NULL,
    fulfillment_date DATE NOT NULL,
    warehouse_id UUID REFERENCES warehouses(id),
    status VARCHAR NOT NULL DEFAULT 'posted',
    journal_id UUID REFERENCES journal_entries(id),
    notes TEXT,
    idempotency_key TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by UUID,
    posted_at TIMESTAMPTZ,
    posted_by UUID,
    voided_at TIMESTAMPTZ,
    voided_reason TEXT
);

CREATE INDEX IF NOT EXISTS idx_if_tenant ON invoice_fulfillments(tenant_id);
CREATE INDEX IF NOT EXISTS idx_if_invoice ON invoice_fulfillments(invoice_id);
CREATE INDEX IF NOT EXISTS idx_if_date ON invoice_fulfillments(fulfillment_date);
CREATE UNIQUE INDEX IF NOT EXISTS idx_if_idempotency
    ON invoice_fulfillments(tenant_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS invoice_fulfillment_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    fulfillment_id UUID NOT NULL REFERENCES invoice_fulfillments(id) ON DELETE CASCADE,
    invoice_item_id UUID NOT NULL REFERENCES sales_invoice_items(id),
    product_id UUID REFERENCES products(id),
    quantity NUMERIC(18,2) NOT NULL,
    unit_cost NUMERIC(18,2),
    total_cost NUMERIC(18,2),
    batch_id UUID,
    serial_ids TEXT[],
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ifi_fulfillment ON invoice_fulfillment_items(fulfillment_id);
CREATE INDEX IF NOT EXISTS idx_ifi_invoice_item ON invoice_fulfillment_items(invoice_item_id);
CREATE INDEX IF NOT EXISTS idx_ifi_product ON invoice_fulfillment_items(product_id);

-- =========================================================================
-- 1e. Register source_type values (INVOICE_FULFILLMENT, INVOICE_REVENUE)
-- =========================================================================
-- Inspection on dev: journal_entries and inventory_ledger have NO CHECK
-- constraint on source_type (it is free-form text), and no journal_source_types
-- reference table exists. So no DDL required to register new values.
-- This block is a no-op placeholder so rollout stays consistent with arch §5.
DO $$ BEGIN RAISE NOTICE 'source_type is free-form text — no CHECK to extend'; END $$;

-- =========================================================================
-- 1g. Data backfill
-- =========================================================================
-- NOTE: sales_invoices.status on dev uses lowercase ('draft','posted','partial','paid','void').
-- Treat posted/partial/paid as POSTED invoices.

-- POSTED invoices WITH cogs_journal_id → fulfilled + recognized
UPDATE sales_invoices
SET fulfillment_status = 'fulfilled',
    revenue_status     = 'recognized',
    total_recognized_amount = COALESCE(total_amount, 0)
WHERE status IN ('posted','partial','paid')
  AND cogs_journal_id IS NOT NULL;

-- POSTED invoices WITHOUT cogs_journal_id → not_applicable + recognized
UPDATE sales_invoices
SET fulfillment_status = 'not_applicable',
    revenue_status     = 'recognized',
    total_recognized_amount = COALESCE(total_amount, 0)
WHERE status IN ('posted','partial','paid')
  AND cogs_journal_id IS NULL;

-- Draft invoices → pending + deferred (defaults already set, but be explicit for existing rows)
UPDATE sales_invoices
SET fulfillment_status = 'pending',
    revenue_status     = 'deferred'
WHERE status = 'draft';

-- sales_invoice_items.allocated_amount defaults to subtotal
UPDATE sales_invoice_items
SET allocated_amount = subtotal
WHERE allocated_amount IS NULL;

-- Items on fulfilled invoices: fulfilled_qty=quantity, recognized_amount=allocated_amount
UPDATE sales_invoice_items sii
SET fulfilled_qty = sii.quantity,
    recognized_amount = sii.allocated_amount
FROM sales_invoices si
WHERE sii.invoice_id = si.id
  AND si.fulfillment_status = 'fulfilled';

COMMIT;

-- LEGACY NOTE (2026-04-16): Invoices posted before V137 use source_type='SALES_INVOICE'
-- in inventory_ledger (not 'INVOICE_FULFILLMENT'). INV-9 invariant check should filter
-- by created_at >= '2026-04-16' to exclude pre-migration fulfillments.
-- These legacy invoices have fulfillment_status='fulfilled' but their inventory entries
-- predate the 3-event model. No data correction needed — they are functionally correct.

-- INV-9 (v2): inventory outbound = fulfilled_qty (post-V137 only)
-- NOTE: Uses invoice_fulfillment_items (not sales_invoice_items JOIN invoice_fulfillments)
-- to avoid JOIN fan-out that inflates fulfilled_qty when multiple fulfillments exist.
--
-- WITH ledger_out AS (
--     SELECT il.product_id, SUM(il.quantity_out) AS ledger_qty
--     FROM inventory_ledger il
--     WHERE il.tenant_id=$TENANT
--       AND il.source_type='INVOICE_FULFILLMENT'
--       AND il.created_at >= '2026-04-16'
--     GROUP BY il.product_id
-- ),
-- item_fulfilled AS (
--     SELECT fi.product_id, SUM(fi.quantity) AS item_qty
--     FROM invoice_fulfillment_items fi
--     JOIN invoice_fulfillments f ON f.id = fi.fulfillment_id
--     WHERE f.tenant_id=$TENANT
--       AND f.status = 'posted'
--       AND f.created_at >= '2026-04-16'
--     GROUP BY fi.product_id
-- )
-- SELECT COALESCE(l.product_id, f.product_id) AS product_id,
--        COALESCE(l.ledger_qty, 0) AS ledger_qty,
--        COALESCE(f.item_qty, 0) AS item_qty,
--        ABS(COALESCE(l.ledger_qty,0) - COALESCE(f.item_qty,0)) AS drift
-- FROM ledger_out l FULL OUTER JOIN item_fulfilled f ON l.product_id = f.product_id
-- WHERE ABS(COALESCE(l.ledger_qty,0) - COALESCE(f.item_qty,0)) > 0.01;
