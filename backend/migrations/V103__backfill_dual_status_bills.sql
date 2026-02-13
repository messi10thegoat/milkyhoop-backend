-- ============================================================================
-- V103: Backfill Dual Status for Bills
-- ============================================================================

UPDATE bills SET
    operational_status = CASE status
        WHEN 'draft' THEN 'DRAFT'
        WHEN 'unpaid' THEN 'RECEIVED'
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
