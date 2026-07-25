\set T '''c0000000-0000-0000-0000-000000000001'''
\echo '=== compute_ap_outstanding (per bill) ==='
SELECT bill_number, outstanding FROM compute_ap_outstanding(:T) ORDER BY bill_number;
\echo '=== compute_ap TOTAL vs raw ledger PAYABLE (all POSTED; reversal pairs net) ==='
SELECT
  (SELECT COALESCE(SUM(outstanding),0) FROM compute_ap_outstanding(:T)) AS compute_ap_total,
  (SELECT COALESCE(SUM(jl.credit)-SUM(jl.debit),0)
     FROM journal_lines jl
     JOIN journal_entries je ON je.id=jl.journal_id
     JOIN chart_of_accounts coa ON coa.id=jl.account_id
    WHERE je.tenant_id=:T AND je.status='POSTED' AND coa.account_type='PAYABLE') AS raw_ledger_payable,
  (SELECT COALESCE(SUM(outstanding),0) FROM compute_ap_outstanding(:T))
   - (SELECT COALESCE(SUM(jl.credit)-SUM(jl.debit),0)
        FROM journal_lines jl JOIN journal_entries je ON je.id=jl.journal_id
        JOIN chart_of_accounts coa ON coa.id=jl.account_id
       WHERE je.tenant_id=:T AND je.status='POSTED' AND coa.account_type='PAYABLE') AS drift;
\echo '=== compute_ap (per bill) vs inline get_bill_remaining_from_journal (per bill) ==='
SELECT b.invoice_number AS bill_number,
       COALESCE(ca.outstanding,0) AS compute_ap,
       COALESCE(x.inline_remaining,0) AS inline_calc,
       COALESCE(ca.outstanding,0) - COALESCE(x.inline_remaining,0) AS diff
FROM bills b
LEFT JOIN compute_ap_outstanding(:T) ca ON ca.bill_id = b.id
LEFT JOIN LATERAL (
  SELECT COALESCE(SUM(jl.credit)-SUM(jl.debit),0) AS inline_remaining
  FROM journal_lines jl
  JOIN journal_entries je ON je.id=jl.journal_id
  JOIN chart_of_accounts coa ON coa.id=jl.account_id
  WHERE je.tenant_id=b.tenant_id AND je.status='POSTED' AND coa.account_code='2-10100'
    AND (
      (je.source_type='BILL' AND je.source_id=b.id)
      OR (je.source_type IN ('BILL_PAYMENT','PAYMENT_BILL') AND je.source_id IN (SELECT bp.id FROM bill_payments_v2 bp JOIN bill_payment_allocations bpa ON bpa.payment_id=bp.id WHERE bpa.bill_id=b.id AND bp.tenant_id=b.tenant_id))
      OR (je.source_type='BILL_PAYMENT_VOID' AND je.source_id IN (SELECT bp.id FROM bill_payments_v2 bp JOIN bill_payment_allocations bpa ON bpa.payment_id=bp.id WHERE bpa.bill_id=b.id AND bp.tenant_id=b.tenant_id))
      OR (je.source_type='VENDOR_CREDIT' AND je.source_id IN (SELECT vca.vendor_credit_id FROM vendor_credit_applications vca WHERE vca.bill_id=b.id))
      OR (je.source_type='DEPOSIT_APPLICATION' AND je.id IN (SELECT vda.journal_id FROM vendor_deposit_applications vda WHERE vda.bill_id=b.id))
    )
) x ON true
WHERE b.tenant_id=:T
ORDER BY b.invoice_number;
