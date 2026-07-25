-- Step C standalone fixture on milkydb_c_test (clone of live milkydb).
-- 5 regression cases for compute_ap_outstanding vendor-deposit fix (V218).
-- All journals follow the production pattern: INSERT DRAFT -> INSERT lines -> UPDATE POSTED.
\set T '''c0000000-0000-0000-0000-000000000001'''
\set U '''00000000-0000-0000-0000-000000000009'''
BEGIN;

-- Tenant (chart_of_accounts + fiscal_periods FK -> "Tenant"(id))
INSERT INTO "Tenant"(id) VALUES ('c0000000-0000-0000-0000-000000000001');

-- Open fiscal period so prevent_closed_period_journal passes
INSERT INTO fiscal_periods(id, tenant_id, period_name, start_date, end_date, status)
VALUES ('f0000000-0000-0000-0000-000000000001', :T, '2026-07', '2026-07-01', '2026-07-31', 'OPEN');

-- Chart of accounts
INSERT INTO chart_of_accounts(id, tenant_id, account_code, name, account_type, normal_balance) VALUES
  ('acc00000-0000-0000-0000-000000000101', :T, '2-10100', 'Hutang Usaha',      'PAYABLE', 'CREDIT'),
  ('acc00000-0000-0000-0000-000000000108', :T, '1-10800', 'Uang Muka Vendor',  'ASSET',   'DEBIT'),
  ('acc00000-0000-0000-0000-000000000501', :T, '5-10100', 'Beban Pembelian',   'EXPENSE', 'DEBIT'),
  ('acc00000-0000-0000-0000-000000000001', :T, '1-10001', 'Kas/Bank',          'ASSET',   'DEBIT');

-- Vendor
INSERT INTO vendors(id, tenant_id, name) VALUES ('fe000000-0000-0000-0000-000000000001', :T, 'PT Pemasok Uji');

-- Bills B1..B5 (all obligation 1.000.000, posted)
INSERT INTO bills(id, tenant_id, invoice_number, vendor_id, vendor_name, amount, issue_date, due_date, status_v2, created_by) VALUES
  ('b1000000-0000-0000-0000-000000000001', :T, 'BILL-1', 'fe000000-0000-0000-0000-000000000001', 'PT Pemasok Uji', 1000000, '2026-07-01', '2026-07-31', 'posted', :U),
  ('b2000000-0000-0000-0000-000000000002', :T, 'BILL-2', 'fe000000-0000-0000-0000-000000000001', 'PT Pemasok Uji', 1000000, '2026-07-01', '2026-07-31', 'posted', :U),
  ('b3000000-0000-0000-0000-000000000003', :T, 'BILL-3', 'fe000000-0000-0000-0000-000000000001', 'PT Pemasok Uji', 1000000, '2026-07-01', '2026-07-31', 'posted', :U),
  ('b4000000-0000-0000-0000-000000000004', :T, 'BILL-4', 'fe000000-0000-0000-0000-000000000001', 'PT Pemasok Uji', 1000000, '2026-07-01', '2026-07-31', 'posted', :U),
  ('b5000000-0000-0000-0000-000000000005', :T, 'BILL-5', 'fe000000-0000-0000-0000-000000000001', 'PT Pemasok Uji', 1000000, '2026-07-01', '2026-07-31', 'posted', :U);

-- Vendor deposits used by cases 2,3,4,5
INSERT INTO vendor_deposits(id, tenant_id, deposit_number, deposit_date, vendor_id, amount, status) VALUES
  ('d2000000-0000-0000-0000-000000000002', :T, 'VD-2', '2026-07-05', 'fe000000-0000-0000-0000-000000000001', 400000,  'posted'),
  ('d3000000-0000-0000-0000-000000000003', :T, 'VD-3', '2026-07-05', 'fe000000-0000-0000-0000-000000000001', 1000000, 'posted'),
  ('d4000000-0000-0000-0000-000000000004', :T, 'VD-4', '2026-07-05', 'fe000000-0000-0000-0000-000000000001', 300000,  'posted'),
  ('d5000000-0000-0000-0000-000000000005', :T, 'VD-5', '2026-07-05', 'fe000000-0000-0000-0000-000000000001', 500000,  'posted');

-- Vendor credit for case 4
INSERT INTO vendor_credits(id, tenant_id, credit_number, vendor_name, total_amount, credit_date, reason, created_by)
VALUES ('fc400000-0000-0000-0000-000000000004', :T, 'VC-4', 'PT Pemasok Uji', 100000, '2026-07-06', 'other', :U);

-- Bill payment for case 4
INSERT INTO bill_payments_v2(id, tenant_id, payment_number, vendor_id, vendor_name, payment_date, total_amount, journal_id, status)
VALUES ('fb400000-0000-0000-0000-000000000004', :T, 'BPAY-4', 'fe000000-0000-0000-0000-000000000001', 'PT Pemasok Uji', '2026-07-10', 200000, 'e4a00000-0000-0000-0000-000000000004', 'posted');

-- ============================================================
-- JOURNALS  (helper pattern per journal: DRAFT -> lines -> POSTED)
-- ============================================================

-- B1 obligation: Dr Beban 1.000.000 / Cr AP 1.000.000
INSERT INTO journal_entries(id, tenant_id, journal_number, journal_date, description, source_type, source_id, status, total_debit, total_credit) VALUES ('e1000000-0000-0000-0000-000000000001', :T, 'JV-B1', '2026-07-15', 'Bill 1', 'BILL', 'b1000000-0000-0000-0000-000000000001', 'DRAFT', 1000000, 1000000);
INSERT INTO journal_lines(journal_id, line_number, account_id, debit, credit) VALUES ('e1000000-0000-0000-0000-000000000001', 1, 'acc00000-0000-0000-0000-000000000501', 1000000, 0), ('e1000000-0000-0000-0000-000000000001', 2, 'acc00000-0000-0000-0000-000000000101', 0, 1000000);
UPDATE journal_entries SET status='POSTED' WHERE id='e1000000-0000-0000-0000-000000000001';

-- B2 obligation
INSERT INTO journal_entries(id, tenant_id, journal_number, journal_date, description, source_type, source_id, status, total_debit, total_credit) VALUES ('e2000000-0000-0000-0000-000000000002', :T, 'JV-B2', '2026-07-15', 'Bill 2', 'BILL', 'b2000000-0000-0000-0000-000000000002', 'DRAFT', 1000000, 1000000);
INSERT INTO journal_lines(journal_id, line_number, account_id, debit, credit) VALUES ('e2000000-0000-0000-0000-000000000002', 1, 'acc00000-0000-0000-0000-000000000501', 1000000, 0), ('e2000000-0000-0000-0000-000000000002', 2, 'acc00000-0000-0000-0000-000000000101', 0, 1000000);
UPDATE journal_entries SET status='POSTED' WHERE id='e2000000-0000-0000-0000-000000000002';
-- B2 deposit apply 400.000: Dr AP 400.000 / Cr Uang Muka Vendor 400.000
INSERT INTO journal_entries(id, tenant_id, journal_number, journal_date, description, source_type, source_id, status, total_debit, total_credit) VALUES ('e2a00000-0000-0000-0000-000000000002', :T, 'JV-B2-DA', '2026-07-16', 'Apply VD-2 to Bill 2', 'DEPOSIT_APPLICATION', 'd2000000-0000-0000-0000-000000000002', 'DRAFT', 400000, 400000);
INSERT INTO journal_lines(journal_id, line_number, account_id, debit, credit) VALUES ('e2a00000-0000-0000-0000-000000000002', 1, 'acc00000-0000-0000-0000-000000000101', 400000, 0), ('e2a00000-0000-0000-0000-000000000002', 2, 'acc00000-0000-0000-0000-000000000108', 0, 400000);
UPDATE journal_entries SET status='POSTED' WHERE id='e2a00000-0000-0000-0000-000000000002';
INSERT INTO vendor_deposit_applications(vendor_deposit_id, bill_id, amount, applied_date, journal_id) VALUES ('d2000000-0000-0000-0000-000000000002', 'b2000000-0000-0000-0000-000000000002', 400000, '2026-07-16', 'e2a00000-0000-0000-0000-000000000002');

-- B3 obligation
INSERT INTO journal_entries(id, tenant_id, journal_number, journal_date, description, source_type, source_id, status, total_debit, total_credit) VALUES ('e3000000-0000-0000-0000-000000000003', :T, 'JV-B3', '2026-07-15', 'Bill 3', 'BILL', 'b3000000-0000-0000-0000-000000000003', 'DRAFT', 1000000, 1000000);
INSERT INTO journal_lines(journal_id, line_number, account_id, debit, credit) VALUES ('e3000000-0000-0000-0000-000000000003', 1, 'acc00000-0000-0000-0000-000000000501', 1000000, 0), ('e3000000-0000-0000-0000-000000000003', 2, 'acc00000-0000-0000-0000-000000000101', 0, 1000000);
UPDATE journal_entries SET status='POSTED' WHERE id='e3000000-0000-0000-0000-000000000003';
-- B3 deposit apply 1.000.000 (full)
INSERT INTO journal_entries(id, tenant_id, journal_number, journal_date, description, source_type, source_id, status, total_debit, total_credit) VALUES ('e3a00000-0000-0000-0000-000000000003', :T, 'JV-B3-DA', '2026-07-16', 'Apply VD-3 to Bill 3', 'DEPOSIT_APPLICATION', 'd3000000-0000-0000-0000-000000000003', 'DRAFT', 1000000, 1000000);
INSERT INTO journal_lines(journal_id, line_number, account_id, debit, credit) VALUES ('e3a00000-0000-0000-0000-000000000003', 1, 'acc00000-0000-0000-0000-000000000101', 1000000, 0), ('e3a00000-0000-0000-0000-000000000003', 2, 'acc00000-0000-0000-0000-000000000108', 0, 1000000);
UPDATE journal_entries SET status='POSTED' WHERE id='e3a00000-0000-0000-0000-000000000003';
INSERT INTO vendor_deposit_applications(vendor_deposit_id, bill_id, amount, applied_date, journal_id) VALUES ('d3000000-0000-0000-0000-000000000003', 'b3000000-0000-0000-0000-000000000003', 1000000, '2026-07-16', 'e3a00000-0000-0000-0000-000000000003');

-- B4 obligation
INSERT INTO journal_entries(id, tenant_id, journal_number, journal_date, description, source_type, source_id, status, total_debit, total_credit) VALUES ('e4000000-0000-0000-0000-000000000004', :T, 'JV-B4', '2026-07-15', 'Bill 4', 'BILL', 'b4000000-0000-0000-0000-000000000004', 'DRAFT', 1000000, 1000000);
INSERT INTO journal_lines(journal_id, line_number, account_id, debit, credit) VALUES ('e4000000-0000-0000-0000-000000000004', 1, 'acc00000-0000-0000-0000-000000000501', 1000000, 0), ('e4000000-0000-0000-0000-000000000004', 2, 'acc00000-0000-0000-0000-000000000101', 0, 1000000);
UPDATE journal_entries SET status='POSTED' WHERE id='e4000000-0000-0000-0000-000000000004';
-- B4 bill payment 200.000: Dr AP 200.000 / Cr Bank 200.000
INSERT INTO journal_entries(id, tenant_id, journal_number, journal_date, description, source_type, source_id, status, total_debit, total_credit) VALUES ('e4a00000-0000-0000-0000-000000000004', :T, 'JV-B4-PAY', '2026-07-17', 'Pay Bill 4', 'BILL_PAYMENT', 'fb400000-0000-0000-0000-000000000004', 'DRAFT', 200000, 200000);
INSERT INTO journal_lines(journal_id, line_number, account_id, debit, credit) VALUES ('e4a00000-0000-0000-0000-000000000004', 1, 'acc00000-0000-0000-0000-000000000101', 200000, 0), ('e4a00000-0000-0000-0000-000000000004', 2, 'acc00000-0000-0000-0000-000000000001', 0, 200000);
UPDATE journal_entries SET status='POSTED' WHERE id='e4a00000-0000-0000-0000-000000000004';
INSERT INTO bill_payment_allocations(payment_id, bill_id, remaining_before, amount_applied, remaining_after) VALUES ('fb400000-0000-0000-0000-000000000004', 'b4000000-0000-0000-0000-000000000004', 1000000, 200000, 800000);
-- B4 vendor credit application 100.000: Dr AP 100.000 / Cr Bank 100.000 (source VENDOR_CREDIT, source_id = VC id)
INSERT INTO journal_entries(id, tenant_id, journal_number, journal_date, description, source_type, source_id, status, total_debit, total_credit) VALUES ('e4b00000-0000-0000-0000-000000000004', :T, 'JV-B4-VC', '2026-07-18', 'Apply VC-4 to Bill 4', 'VENDOR_CREDIT', 'fc400000-0000-0000-0000-000000000004', 'DRAFT', 100000, 100000);
INSERT INTO journal_lines(journal_id, line_number, account_id, debit, credit) VALUES ('e4b00000-0000-0000-0000-000000000004', 1, 'acc00000-0000-0000-0000-000000000101', 100000, 0), ('e4b00000-0000-0000-0000-000000000004', 2, 'acc00000-0000-0000-0000-000000000001', 0, 100000);
UPDATE journal_entries SET status='POSTED' WHERE id='e4b00000-0000-0000-0000-000000000004';
INSERT INTO vendor_credit_applications(tenant_id, vendor_credit_id, bill_id, amount_applied, application_date, journal_id, created_by) VALUES (:T, 'fc400000-0000-0000-0000-000000000004', 'b4000000-0000-0000-0000-000000000004', 100000, '2026-07-18', 'e4b00000-0000-0000-0000-000000000004', :U);
-- B4 deposit apply 300.000: Dr AP 300.000 / Cr Uang Muka Vendor 300.000
INSERT INTO journal_entries(id, tenant_id, journal_number, journal_date, description, source_type, source_id, status, total_debit, total_credit) VALUES ('e4c00000-0000-0000-0000-000000000004', :T, 'JV-B4-DA', '2026-07-19', 'Apply VD-4 to Bill 4', 'DEPOSIT_APPLICATION', 'd4000000-0000-0000-0000-000000000004', 'DRAFT', 300000, 300000);
INSERT INTO journal_lines(journal_id, line_number, account_id, debit, credit) VALUES ('e4c00000-0000-0000-0000-000000000004', 1, 'acc00000-0000-0000-0000-000000000101', 300000, 0), ('e4c00000-0000-0000-0000-000000000004', 2, 'acc00000-0000-0000-0000-000000000108', 0, 300000);
UPDATE journal_entries SET status='POSTED' WHERE id='e4c00000-0000-0000-0000-000000000004';
INSERT INTO vendor_deposit_applications(vendor_deposit_id, bill_id, amount, applied_date, journal_id) VALUES ('d4000000-0000-0000-0000-000000000004', 'b4000000-0000-0000-0000-000000000004', 300000, '2026-07-19', 'e4c00000-0000-0000-0000-000000000004');

-- B5 obligation
INSERT INTO journal_entries(id, tenant_id, journal_number, journal_date, description, source_type, source_id, status, total_debit, total_credit) VALUES ('e5000000-0000-0000-0000-000000000005', :T, 'JV-B5', '2026-07-15', 'Bill 5', 'BILL', 'b5000000-0000-0000-0000-000000000005', 'DRAFT', 1000000, 1000000);
INSERT INTO journal_lines(journal_id, line_number, account_id, debit, credit) VALUES ('e5000000-0000-0000-0000-000000000005', 1, 'acc00000-0000-0000-0000-000000000501', 1000000, 0), ('e5000000-0000-0000-0000-000000000005', 2, 'acc00000-0000-0000-0000-000000000101', 0, 1000000);
UPDATE journal_entries SET status='POSTED' WHERE id='e5000000-0000-0000-0000-000000000005';
-- B5 deposit apply 500.000 (POSTED)
INSERT INTO journal_entries(id, tenant_id, journal_number, journal_date, description, source_type, source_id, status, total_debit, total_credit) VALUES ('e5a00000-0000-0000-0000-000000000005', :T, 'JV-B5-DA', '2026-07-16', 'Apply VD-5 to Bill 5', 'DEPOSIT_APPLICATION', 'd5000000-0000-0000-0000-000000000005', 'DRAFT', 500000, 500000);
INSERT INTO journal_lines(journal_id, line_number, account_id, debit, credit) VALUES ('e5a00000-0000-0000-0000-000000000005', 1, 'acc00000-0000-0000-0000-000000000101', 500000, 0), ('e5a00000-0000-0000-0000-000000000005', 2, 'acc00000-0000-0000-0000-000000000108', 0, 500000);
UPDATE journal_entries SET status='POSTED' WHERE id='e5a00000-0000-0000-0000-000000000005';
INSERT INTO vendor_deposit_applications(vendor_deposit_id, bill_id, amount, applied_date, journal_id) VALUES ('d5000000-0000-0000-0000-000000000005', 'b5000000-0000-0000-0000-000000000005', 500000, '2026-07-16', 'e5a00000-0000-0000-0000-000000000005');
-- B5 REVERSING journal (Law 2): Cr AP 500.000 / Dr Uang Muka Vendor 500.000, source REVERSAL, reversal_of = apply
INSERT INTO journal_entries(id, tenant_id, journal_number, journal_date, description, source_type, source_id, reversal_of_id, status, total_debit, total_credit) VALUES ('e5b00000-0000-0000-0000-000000000005', :T, 'JV-B5-REV', '2026-07-20', 'Reverse apply VD-5', 'REVERSAL', 'e5a00000-0000-0000-0000-000000000005', 'e5a00000-0000-0000-0000-000000000005', 'DRAFT', 500000, 500000);
INSERT INTO journal_lines(journal_id, line_number, account_id, debit, credit) VALUES ('e5b00000-0000-0000-0000-000000000005', 1, 'acc00000-0000-0000-0000-000000000108', 500000, 0), ('e5b00000-0000-0000-0000-000000000005', 2, 'acc00000-0000-0000-0000-000000000101', 0, 500000);
UPDATE journal_entries SET status='POSTED' WHERE id='e5b00000-0000-0000-0000-000000000005';
-- link reversal (Law 2): apply journal stays POSTED, gets reversed_by_id
UPDATE journal_entries SET reversed_by_id='e5b00000-0000-0000-0000-000000000005', reversed_at=NOW() WHERE id='e5a00000-0000-0000-0000-000000000005';

COMMIT;
