-- V022: Bills Pharmacy Extension
-- Adds pharmacy-specific fields to bills and bill_items tables
-- Implements draft -> posted -> paid status flow

-- ============================================================================
-- STEP 1: Add new columns to bills table
-- ============================================================================

-- Status V2 for new flow: draft -> posted -> paid -> void
ALTER TABLE bills
    ADD COLUMN IF NOT EXISTS status_v2 VARCHAR(20) DEFAULT 'posted';

-- Reference number from vendor (optional)
ALTER TABLE bills
    ADD COLUMN IF NOT EXISTS ref_no VARCHAR(100);

-- Tax fields
ALTER TABLE bills
    ADD COLUMN IF NOT EXISTS tax_rate INTEGER DEFAULT 11,
    ADD COLUMN IF NOT EXISTS tax_inclusive BOOLEAN DEFAULT false;

-- Multi-level discount fields
ALTER TABLE bills
    ADD COLUMN IF NOT EXISTS invoice_discount_percent DECIMAL(5,2) DEFAULT 0,
    ADD COLUMN IF NOT EXISTS invoice_discount_amount BIGINT DEFAULT 0,
    ADD COLUMN IF NOT EXISTS cash_discount_percent DECIMAL(5,2) DEFAULT 0,
    ADD COLUMN IF NOT EXISTS cash_discount_amount BIGINT DEFAULT 0;

-- Manual DPP override (null = auto-calculate)
ALTER TABLE bills
    ADD COLUMN IF NOT EXISTS dpp_manual BIGINT;

-- Calculated totals
ALTER TABLE bills
    ADD COLUMN IF NOT EXISTS subtotal BIGINT DEFAULT 0,
    ADD COLUMN IF NOT EXISTS item_discount_total BIGINT DEFAULT 0,
    ADD COLUMN IF NOT EXISTS invoice_discount_total BIGINT DEFAULT 0,
    ADD COLUMN IF NOT EXISTS cash_discount_total BIGINT DEFAULT 0,
    ADD COLUMN IF NOT EXISTS dpp BIGINT DEFAULT 0,
    ADD COLUMN IF NOT EXISTS tax_amount BIGINT DEFAULT 0,
    ADD COLUMN IF NOT EXISTS grand_total BIGINT DEFAULT 0;

-- Posting metadata
ALTER TABLE bills
    ADD COLUMN IF NOT EXISTS posted_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS posted_by UUID;

-- Constraints for status_v2
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'chk_bills_status_v2'
    ) THEN
        ALTER TABLE bills
            ADD CONSTRAINT chk_bills_status_v2
            CHECK (status_v2 IN ('draft', 'posted', 'paid', 'void'));
    END IF;
END $$;

-- Constraints for tax_rate
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'chk_bills_tax_rate'
    ) THEN
        ALTER TABLE bills
            ADD CONSTRAINT chk_bills_tax_rate
            CHECK (tax_rate IN (0, 11, 12));
    END IF;
END $$;

-- ============================================================================
-- STEP 2: Add new columns to bill_items table
-- ============================================================================

-- Product identification
ALTER TABLE bill_items
    ADD COLUMN IF NOT EXISTS product_code VARCHAR(100),
    ADD COLUMN IF NOT EXISTS product_name VARCHAR(255);

-- Item-level discount
ALTER TABLE bill_items
    ADD COLUMN IF NOT EXISTS discount_percent DECIMAL(5,2) DEFAULT 0,
    ADD COLUMN IF NOT EXISTS discount_amount BIGINT DEFAULT 0,
    ADD COLUMN IF NOT EXISTS total BIGINT DEFAULT 0;

-- Pharmacy-specific fields
ALTER TABLE bill_items
    ADD COLUMN IF NOT EXISTS batch_no VARCHAR(100),
    ADD COLUMN IF NOT EXISTS exp_date DATE,              -- Store as DATE with day=01
    ADD COLUMN IF NOT EXISTS bonus_qty INTEGER DEFAULT 0;

-- ============================================================================
-- STEP 3: Create indexes for new columns
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_bills_tenant_status_v2 ON bills(tenant_id, status_v2);
CREATE INDEX IF NOT EXISTS idx_bills_ref_no ON bills(tenant_id, ref_no) WHERE ref_no IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_bill_items_batch ON bill_items(batch_no) WHERE batch_no IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_bill_items_exp_date ON bill_items(exp_date) WHERE exp_date IS NOT NULL;

-- ============================================================================
-- STEP 4: Migrate existing data to new status
-- ============================================================================

-- Map existing status to status_v2
UPDATE bills
SET status_v2 = CASE status
    WHEN 'void' THEN 'void'
    WHEN 'paid' THEN 'paid'
    ELSE 'posted'  -- unpaid, partial, overdue -> posted (already in accounting)
END,
    -- Copy existing amount to grand_total and subtotal
    grand_total = COALESCE(amount, 0),
    subtotal = COALESCE(amount, 0),
    dpp = COALESCE(amount, 0)
WHERE status_v2 IS NULL OR status_v2 = 'draft';

-- Update bill_items to set total = subtotal where not set
UPDATE bill_items
SET total = COALESCE(subtotal, 0)
WHERE total IS NULL OR total = 0;

-- ============================================================================
-- STEP 5: Helper function for bill calculations
-- ============================================================================

CREATE OR REPLACE FUNCTION calculate_bill_totals_v2(
    p_items JSONB,
    p_invoice_discount_percent DECIMAL(5,2),
    p_invoice_discount_amount BIGINT,
    p_cash_discount_percent DECIMAL(5,2),
    p_cash_discount_amount BIGINT,
    p_tax_rate INTEGER,
    p_dpp_manual BIGINT
)
RETURNS TABLE (
    subtotal BIGINT,
    item_discount_total BIGINT,
    invoice_discount_total BIGINT,
    cash_discount_total BIGINT,
    dpp BIGINT,
    tax_amount BIGINT,
    grand_total BIGINT
) AS $$
DECLARE
    v_subtotal BIGINT := 0;
    v_item_discount_total BIGINT := 0;
    v_after_item_discount BIGINT;
    v_invoice_discount_total BIGINT := 0;
    v_after_invoice_discount BIGINT;
    v_cash_discount_total BIGINT := 0;
    v_auto_dpp BIGINT;
    v_dpp BIGINT;
    v_tax_amount BIGINT;
    v_grand_total BIGINT;
    item JSONB;
BEGIN
    -- Calculate subtotal and item discounts from items array
    FOR item IN SELECT * FROM jsonb_array_elements(p_items)
    LOOP
        v_subtotal := v_subtotal +
            ((item->>'qty')::INTEGER * (item->>'price')::BIGINT);
        v_item_discount_total := v_item_discount_total +
            ((item->>'qty')::INTEGER * (item->>'price')::BIGINT *
             COALESCE((item->>'discount_percent')::DECIMAL, 0) / 100)::BIGINT;
    END LOOP;

    v_after_item_discount := v_subtotal - v_item_discount_total;

    -- Invoice discount (% OR amount, percent takes precedence)
    IF p_invoice_discount_percent > 0 THEN
        v_invoice_discount_total := (v_after_item_discount * p_invoice_discount_percent / 100)::BIGINT;
    ELSE
        v_invoice_discount_total := COALESCE(p_invoice_discount_amount, 0);
    END IF;

    v_after_invoice_discount := v_after_item_discount - v_invoice_discount_total;

    -- Cash discount (% OR amount, percent takes precedence)
    IF p_cash_discount_percent > 0 THEN
        v_cash_discount_total := (v_after_invoice_discount * p_cash_discount_percent / 100)::BIGINT;
    ELSE
        v_cash_discount_total := COALESCE(p_cash_discount_amount, 0);
    END IF;

    -- DPP (Dasar Pengenaan Pajak)
    v_auto_dpp := v_after_invoice_discount - v_cash_discount_total;
    v_dpp := COALESCE(p_dpp_manual, v_auto_dpp);

    -- Tax
    v_tax_amount := (v_dpp * p_tax_rate / 100)::BIGINT;

    -- Grand total
    v_grand_total := v_dpp + v_tax_amount;

    RETURN QUERY SELECT
        v_subtotal,
        v_item_discount_total,
        v_invoice_discount_total,
        v_cash_discount_total,
        v_dpp,
        v_tax_amount,
        v_grand_total;
END;
$$ LANGUAGE plpgsql IMMUTABLE;

-- ============================================================================
-- STEP 6: Status transition validation function
-- ============================================================================

CREATE OR REPLACE FUNCTION validate_bill_status_transition(
    p_current_status VARCHAR,
    p_new_status VARCHAR
)
RETURNS BOOLEAN AS $$
BEGIN
    -- Valid transitions:
    -- draft -> posted (when posting to accounting)
    -- draft -> void (cancel draft)
    -- posted -> paid (full payment)
    -- posted -> void (void with reversal journal)
    -- void -> (no transitions allowed)
    -- paid -> (no transitions allowed)

    IF p_current_status = p_new_status THEN
        RETURN true;  -- No change
    END IF;

    RETURN CASE p_current_status
        WHEN 'draft' THEN p_new_status IN ('posted', 'void')
        WHEN 'posted' THEN p_new_status IN ('paid', 'void')
        WHEN 'paid' THEN false
        WHEN 'void' THEN false
        ELSE false
    END;
END;
$$ LANGUAGE plpgsql IMMUTABLE;

-- ============================================================================
-- STEP 7: Trigger for status_v2 validation
-- ============================================================================

CREATE OR REPLACE FUNCTION trg_validate_bill_status_v2()
RETURNS TRIGGER AS $$
BEGIN
    -- Skip validation if status_v2 not changed
    IF OLD.status_v2 = NEW.status_v2 THEN
        RETURN NEW;
    END IF;

    -- Validate transition
    IF NOT validate_bill_status_transition(OLD.status_v2, NEW.status_v2) THEN
        RAISE EXCEPTION 'Invalid status transition from % to %',
            OLD.status_v2, NEW.status_v2;
    END IF;

    -- Set posted_at when transitioning to posted
    IF NEW.status_v2 = 'posted' AND OLD.status_v2 = 'draft' THEN
        NEW.posted_at := NOW();
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Drop existing trigger if exists
DROP TRIGGER IF EXISTS trg_bills_status_v2_validation ON bills;

CREATE TRIGGER trg_bills_status_v2_validation
    BEFORE UPDATE ON bills
    FOR EACH ROW
    EXECUTE FUNCTION trg_validate_bill_status_v2();

-- ============================================================================
-- STEP 8: Generate purchase bill number (format: PB-YYMM-0001)
-- ============================================================================

CREATE OR REPLACE FUNCTION generate_purchase_bill_number(p_tenant_id TEXT)
RETURNS VARCHAR AS $$
DECLARE
    v_year_month VARCHAR(7);
    v_next_number INT;
    v_bill_number VARCHAR(50);
BEGIN
    -- Get current year-month
    v_year_month := TO_CHAR(CURRENT_DATE, 'YYYY-MM');

    -- Insert or update sequence (atomic)
    INSERT INTO bill_number_sequences (tenant_id, year_month, last_number, prefix)
    VALUES (p_tenant_id, v_year_month, 1, 'PB')
    ON CONFLICT (tenant_id, year_month)
    DO UPDATE SET
        last_number = bill_number_sequences.last_number + 1,
        updated_at = NOW()
    RETURNING last_number INTO v_next_number;

    -- Format: PB-2601-0001
    v_bill_number := 'PB-' ||
                     SUBSTRING(v_year_month, 3, 2) ||
                     SUBSTRING(v_year_month, 6, 2) || '-' ||
                     LPAD(v_next_number::TEXT, 4, '0');

    RETURN v_bill_number;
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- DOCUMENTATION
-- ============================================================================

COMMENT ON COLUMN bills.status_v2 IS 'New status flow: draft (editable) -> posted (in accounting) -> paid (fully paid) | void (cancelled)';
COMMENT ON COLUMN bills.ref_no IS 'Reference number from vendor/supplier (e.g., PO number, delivery order)';
COMMENT ON COLUMN bills.tax_rate IS 'Tax rate: 0% (non-taxable), 11% (standard), or 12% (luxury goods)';
COMMENT ON COLUMN bills.tax_inclusive IS 'True if prices already include tax (DPP = price / 1.11)';
COMMENT ON COLUMN bills.invoice_discount_percent IS 'Invoice-level discount percentage (0-100)';
COMMENT ON COLUMN bills.invoice_discount_amount IS 'Invoice-level discount amount (used if percent is 0)';
COMMENT ON COLUMN bills.cash_discount_percent IS 'Cash/early payment discount percentage';
COMMENT ON COLUMN bills.cash_discount_amount IS 'Cash discount amount (used if percent is 0)';
COMMENT ON COLUMN bills.dpp_manual IS 'Manual DPP override (null = auto-calculate from discounts)';
COMMENT ON COLUMN bills.dpp IS 'Dasar Pengenaan Pajak (tax base)';

COMMENT ON COLUMN bill_items.product_code IS 'Product code from supplier catalog';
COMMENT ON COLUMN bill_items.product_name IS 'Product name for display';
COMMENT ON COLUMN bill_items.discount_percent IS 'Item-level discount percentage';
COMMENT ON COLUMN bill_items.discount_amount IS 'Calculated: qty * price * discount_percent / 100';
COMMENT ON COLUMN bill_items.total IS 'Line total after discount: (qty * price) - discount_amount';
COMMENT ON COLUMN bill_items.batch_no IS 'Batch/lot number for traceability';
COMMENT ON COLUMN bill_items.exp_date IS 'Expiry date (stored as DATE with day=01, e.g., 2027-06-01)';
COMMENT ON COLUMN bill_items.bonus_qty IS 'Free/bonus items from vendor (not included in calculation)';

COMMENT ON FUNCTION calculate_bill_totals_v2 IS 'Calculates all bill totals from items and discount parameters';
COMMENT ON FUNCTION validate_bill_status_transition IS 'Validates allowed status transitions for bills';
COMMENT ON FUNCTION generate_purchase_bill_number IS 'Generates purchase bill number in format PB-YYMM-0001';
