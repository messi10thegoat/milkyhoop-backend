-- ============================================================================
-- V098: Backfill Dual Status for Receive Payments
-- ============================================================================

UPDATE receive_payments SET
    operational_status = CASE status
        WHEN 'draft' THEN 'CREATED'
        WHEN 'posted' THEN 'CONFIRMED'
        WHEN 'voided' THEN 'CANCELLED'
        ELSE 'CREATED'
    END,
    accounting_status = CASE 
        WHEN status = 'draft' THEN 'UNPOSTED'
        WHEN status = 'voided' AND void_journal_id IS NOT NULL THEN 'REVERSED'
        WHEN journal_id IS NOT NULL THEN 'POSTED'
        ELSE 'UNPOSTED'
    END
WHERE operational_status = 'CREATED' AND accounting_status = 'UNPOSTED';
