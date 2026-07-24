-- ============================================================================
-- V078: Add COGS tracking columns to sales_invoice_items and sales_invoices
-- ============================================================================
-- These columns were in V033 original but missing from V033_fixed (which had
-- correct DDL for inventory_ledger). Added here before V115 ALTER TYPE.
-- Types: BIGINT (pre-V115), V115 will ALTER to numeric(18,2).
-- ============================================================================

ALTER TABLE sales_invoice_items
    ADD COLUMN IF NOT EXISTS unit_cost BIGINT DEFAULT 0,
    ADD COLUMN IF NOT EXISTS total_cost BIGINT DEFAULT 0,
    ADD COLUMN IF NOT EXISTS is_inventory_item BOOLEAN DEFAULT false,
    ADD COLUMN IF NOT EXISTS cost_source VARCHAR(30) DEFAULT NULL;

ALTER TABLE sales_invoices
    ADD COLUMN IF NOT EXISTS cogs_journal_id UUID,
    ADD COLUMN IF NOT EXISTS total_cogs BIGINT DEFAULT 0,
    ADD COLUMN IF NOT EXISTS cogs_posted_at TIMESTAMPTZ;

COMMENT ON COLUMN sales_invoice_items.unit_cost IS 'Unit cost at time of sale (weighted average)';
COMMENT ON COLUMN sales_invoice_items.total_cost IS 'Total COGS for this line (quantity * unit_cost)';
COMMENT ON COLUMN sales_invoice_items.is_inventory_item IS 'True if item tracked in inventory';
COMMENT ON COLUMN sales_invoice_items.cost_source IS 'WEIGHTED_AVG, PURCHASE_PRICE (fallback), MANUAL';
COMMENT ON COLUMN sales_invoices.cogs_journal_id IS 'Journal entry ID for COGS posting';
COMMENT ON COLUMN sales_invoices.total_cogs IS 'Total Cost of Goods Sold for this invoice';
COMMENT ON COLUMN sales_invoices.cogs_posted_at IS 'When COGS journal was posted';
