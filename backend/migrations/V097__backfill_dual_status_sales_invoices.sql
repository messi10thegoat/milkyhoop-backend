-- ============================================================================
-- V097: Backfill Dual Status for Sales Invoices
-- Maps old single status -> (operational_status, accounting_status)
-- 
-- Status mapping:
--   draft  -> DRAFT + UNPOSTED
--   posted -> SENT + POSTED (if journal_id exists) or UNPOSTED
--   partial -> PARTIALLY_PAID + POSTED
--   paid   -> PAID + POSTED
--   overdue -> OVERDUE + POSTED
--   void   -> VOID + REVERSED (if journal_id exists) or UNPOSTED
-- ============================================================================

-- Backfill based on existing status and journal presence
UPDATE sales_invoices SET
    operational_status = CASE status
        WHEN 'draft' THEN 'DRAFT'
        WHEN 'posted' THEN 'SENT'
        WHEN 'partial' THEN 'PARTIALLY_PAID'
        WHEN 'paid' THEN 'PAID'
        WHEN 'overdue' THEN 'OVERDUE'
        WHEN 'void' THEN 'VOID'
        ELSE 'DRAFT'
    END,
    accounting_status = CASE 
        WHEN status = 'draft' THEN 'UNPOSTED'
        WHEN status = 'void' AND journal_id IS NOT NULL THEN 'REVERSED'
        WHEN journal_id IS NOT NULL THEN 'POSTED'
        ELSE 'UNPOSTED'
    END
WHERE operational_status = 'DRAFT' AND accounting_status = 'UNPOSTED';

-- ============================================================================
-- Verification query (run manually after migration to verify)
-- ============================================================================
-- SELECT 
--     status AS old_status, 
--     operational_status, 
--     accounting_status, 
--     journal_id IS NOT NULL AS has_journal,
--     COUNT(*) 
-- FROM sales_invoices 
-- GROUP BY 1, 2, 3, 4
-- ORDER BY 1, 2, 3;
