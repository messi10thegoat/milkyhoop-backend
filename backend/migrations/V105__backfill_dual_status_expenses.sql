-- ============================================================================
-- V105: Backfill Dual Status for Expenses
-- ============================================================================
-- Backfills operational_status and accounting_status from legacy status column
-- for existing expense records.
-- ============================================================================

UPDATE expenses SET
    operational_status = CASE status
        WHEN 'draft' THEN 'DRAFT'
        WHEN 'posted' THEN 'PAID'
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
