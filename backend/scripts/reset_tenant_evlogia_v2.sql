-- =====================================================
-- RESET TENANT EVLOGIA - FULL DATA WIPE V2
-- =====================================================

BEGIN;

-- Disable FK checks temporarily
SET session_replication_role = 'replica';

-- =====================================================
-- Delete all data for tenant evlogia from all tables
-- =====================================================

-- Transaction Details
DELETE FROM sales_invoice_items WHERE invoice_id IN (SELECT id FROM sales_invoices WHERE tenant_id = 'evlogia');
DELETE FROM sales_invoice_payments WHERE invoice_id IN (SELECT id FROM sales_invoices WHERE tenant_id = 'evlogia');
DELETE FROM bill_items WHERE bill_id IN (SELECT id FROM bills WHERE tenant_id = 'evlogia');
DELETE FROM bill_attachments WHERE bill_id IN (SELECT id FROM bills WHERE tenant_id = 'evlogia');
DELETE FROM bill_payment_allocations WHERE bill_id IN (SELECT id FROM bills WHERE tenant_id = 'evlogia');
DELETE FROM expense_items WHERE expense_id IN (SELECT id FROM expenses WHERE tenant_id = 'evlogia');
DELETE FROM expense_attachments WHERE expense_id IN (SELECT id FROM expenses WHERE tenant_id = 'evlogia');
DELETE FROM journal_lines WHERE journal_id IN (SELECT id FROM journal_entries WHERE tenant_id = 'evlogia');
DELETE FROM jurnal_detail WHERE jurnal_entry_id IN (SELECT id FROM jurnal_entry WHERE tenant_id = 'evlogia');
DELETE FROM quote_items WHERE quote_id IN (SELECT id FROM quotes WHERE tenant_id = 'evlogia');
DELETE FROM sales_order_items WHERE sales_order_id IN (SELECT id FROM sales_orders WHERE tenant_id = 'evlogia');
DELETE FROM sales_order_shipment_items WHERE shipment_id IN (SELECT id FROM sales_order_shipments WHERE tenant_id = 'evlogia');
DELETE FROM purchase_order_items WHERE purchase_order_id IN (SELECT id FROM purchase_orders WHERE tenant_id = 'evlogia');
DELETE FROM credit_note_items WHERE credit_note_id IN (SELECT id FROM credit_notes WHERE tenant_id = 'evlogia');
DELETE FROM vendor_credit_items WHERE vendor_credit_id IN (SELECT id FROM vendor_credits WHERE tenant_id = 'evlogia');
DELETE FROM bank_reconciliation_items WHERE reconciliation_id IN (SELECT id FROM bank_reconciliations WHERE tenant_id = 'evlogia');
DELETE FROM bank_statement_lines WHERE import_id IN (SELECT id FROM bank_statement_imports WHERE tenant_id = 'evlogia');
DELETE FROM stock_adjustment_items WHERE adjustment_id IN (SELECT id FROM stock_adjustments WHERE tenant_id = 'evlogia');
DELETE FROM stock_transfer_items WHERE transfer_id IN (SELECT id FROM stock_transfers WHERE tenant_id = 'evlogia');
DELETE FROM sales_receipt_items WHERE receipt_id IN (SELECT id FROM sales_receipts WHERE tenant_id = 'evlogia');
DELETE FROM cheque_status_history WHERE cheque_id IN (SELECT id FROM cheques WHERE tenant_id = 'evlogia');
DELETE FROM asset_depreciations WHERE asset_id IN (SELECT id FROM fixed_assets WHERE tenant_id = 'evlogia');
DELETE FROM asset_maintenance WHERE asset_id IN (SELECT id FROM fixed_assets WHERE tenant_id = 'evlogia');
DELETE FROM production_order_materials WHERE production_order_id IN (SELECT id FROM production_orders WHERE tenant_id = 'evlogia');
DELETE FROM production_order_labor WHERE production_order_id IN (SELECT id FROM production_orders WHERE tenant_id = 'evlogia');
DELETE FROM production_completions WHERE production_order_id IN (SELECT id FROM production_orders WHERE tenant_id = 'evlogia');
DELETE FROM bom_components WHERE bom_id IN (SELECT id FROM bill_of_materials WHERE tenant_id = 'evlogia');
DELETE FROM bom_operations WHERE bom_id IN (SELECT id FROM bill_of_materials WHERE tenant_id = 'evlogia');
DELETE FROM bom_substitutes WHERE bom_id IN (SELECT id FROM bill_of_materials WHERE tenant_id = 'evlogia');
DELETE FROM recipe_ingredients WHERE recipe_id IN (SELECT id FROM recipes WHERE tenant_id = 'evlogia');
DELETE FROM recipe_instructions WHERE recipe_id IN (SELECT id FROM recipes WHERE tenant_id = 'evlogia');
DELETE FROM recipe_modifiers WHERE recipe_id IN (SELECT id FROM recipes WHERE tenant_id = 'evlogia');
DELETE FROM budget_items WHERE budget_id IN (SELECT id FROM budgets WHERE tenant_id = 'evlogia');
DELETE FROM budget_revisions WHERE budget_id IN (SELECT id FROM budgets WHERE tenant_id = 'evlogia');
DELETE FROM recurring_invoice_items WHERE invoice_id IN (SELECT id FROM recurring_invoices WHERE tenant_id = 'evlogia');
DELETE FROM recurring_bill_items WHERE bill_id IN (SELECT id FROM recurring_bills WHERE tenant_id = 'evlogia');
DELETE FROM approval_actions WHERE request_id IN (SELECT id FROM approval_requests WHERE tenant_id = 'evlogia');
DELETE FROM approval_levels WHERE workflow_id IN (SELECT id FROM approval_workflows WHERE tenant_id = 'evlogia');
DELETE FROM vendor_addresses WHERE vendor_id IN (SELECT id FROM vendors WHERE tenant_id = 'evlogia');
DELETE FROM vendor_contacts WHERE vendor_id IN (SELECT id FROM vendors WHERE tenant_id = 'evlogia');
DELETE FROM kds_order_items WHERE order_id IN (SELECT id FROM kds_orders WHERE tenant_id = 'evlogia');
DELETE FROM table_sessions WHERE table_id IN (SELECT id FROM restaurant_tables WHERE tenant_id = 'evlogia');
DELETE FROM table_waitlist WHERE table_id IN (SELECT id FROM restaurant_tables WHERE tenant_id = 'evlogia');
DELETE FROM document_attachments WHERE document_id IN (SELECT id FROM documents WHERE tenant_id = 'evlogia');
DELETE FROM item_inventory WHERE product_id IN (SELECT id FROM products WHERE tenant_id = 'evlogia');
DELETE FROM item_pricing WHERE product_id IN (SELECT id FROM products WHERE tenant_id = 'evlogia');
DELETE FROM item_batches WHERE product_id IN (SELECT id FROM products WHERE tenant_id = 'evlogia');
DELETE FROM item_serials WHERE product_id IN (SELECT id FROM products WHERE tenant_id = 'evlogia');
DELETE FROM unit_conversions WHERE product_id IN (SELECT id FROM products WHERE tenant_id = 'evlogia');

-- Now delete all tables with tenant_id directly
DELETE FROM receive_payment_allocations WHERE tenant_id = 'evlogia';
DELETE FROM ar_payment_applications WHERE tenant_id = 'evlogia';
DELETE FROM accounts_receivable WHERE tenant_id = 'evlogia';
DELETE FROM ap_payment_applications WHERE tenant_id = 'evlogia';
DELETE FROM accounts_payable WHERE tenant_id = 'evlogia';
DELETE FROM credit_note_applications WHERE tenant_id = 'evlogia';
DELETE FROM credit_note_refunds WHERE tenant_id = 'evlogia';
DELETE FROM vendor_credit_applications WHERE tenant_id = 'evlogia';
DELETE FROM vendor_credit_refunds WHERE tenant_id = 'evlogia';
DELETE FROM customer_deposit_applications WHERE tenant_id = 'evlogia';
DELETE FROM customer_deposit_refunds WHERE tenant_id = 'evlogia';
DELETE FROM vendor_deposit_applications WHERE tenant_id = 'evlogia';
DELETE FROM vendor_deposit_refunds WHERE tenant_id = 'evlogia';
DELETE FROM bank_statement_lines_v2 WHERE tenant_id = 'evlogia';
DELETE FROM warehouse_stock WHERE tenant_id = 'evlogia';
DELETE FROM batch_warehouse_stock WHERE tenant_id = 'evlogia';
DELETE FROM bin_stock WHERE tenant_id = 'evlogia';
DELETE FROM serial_movements WHERE tenant_id = 'evlogia';
DELETE FROM kartu_stok WHERE tenant_id = 'evlogia';
DELETE FROM inventory_ledger WHERE tenant_id = 'evlogia';
DELETE FROM item_activities WHERE tenant_id = 'evlogia';
DELETE FROM customer_activities WHERE tenant_id = 'evlogia';
DELETE FROM vendor_activities WHERE tenant_id = 'evlogia';
DELETE FROM kds_item_history WHERE tenant_id = 'evlogia';
DELETE FROM kds_alerts WHERE tenant_id = 'evlogia';
DELETE FROM intercompany_balances WHERE tenant_id = 'evlogia';
DELETE FROM intercompany_settlements WHERE tenant_id = 'evlogia';
DELETE FROM reconciliation_matches WHERE tenant_id = 'evlogia';
DELETE FROM reconciliation_adjustments WHERE tenant_id = 'evlogia';
DELETE FROM table_reservations WHERE tenant_id = 'evlogia';

-- Parent transaction tables
DELETE FROM sales_invoices WHERE tenant_id = 'evlogia';
DELETE FROM bills WHERE tenant_id = 'evlogia';
DELETE FROM expenses WHERE tenant_id = 'evlogia';
DELETE FROM journal_entries WHERE tenant_id = 'evlogia';
DELETE FROM jurnal_entry WHERE tenant_id = 'evlogia';
DELETE FROM quotes WHERE tenant_id = 'evlogia';
DELETE FROM sales_order_shipments WHERE tenant_id = 'evlogia';
DELETE FROM sales_orders WHERE tenant_id = 'evlogia';
DELETE FROM purchase_orders WHERE tenant_id = 'evlogia';
DELETE FROM receive_payments WHERE tenant_id = 'evlogia';
DELETE FROM bill_payments WHERE tenant_id = 'evlogia';
DELETE FROM bill_payments_v2 WHERE tenant_id = 'evlogia';
DELETE FROM credit_notes WHERE tenant_id = 'evlogia';
DELETE FROM vendor_credits WHERE tenant_id = 'evlogia';
DELETE FROM customer_deposits WHERE tenant_id = 'evlogia';
DELETE FROM vendor_deposits WHERE tenant_id = 'evlogia';
DELETE FROM bank_reconciliations WHERE tenant_id = 'evlogia';
DELETE FROM bank_statement_imports WHERE tenant_id = 'evlogia';
DELETE FROM bank_transactions WHERE tenant_id = 'evlogia';
DELETE FROM bank_transfers WHERE tenant_id = 'evlogia';
DELETE FROM stock_adjustments WHERE tenant_id = 'evlogia';
DELETE FROM stock_transfers WHERE tenant_id = 'evlogia';
DELETE FROM fixed_assets WHERE tenant_id = 'evlogia';
DELETE FROM production_orders WHERE tenant_id = 'evlogia';
DELETE FROM sales_receipts WHERE tenant_id = 'evlogia';
DELETE FROM cheques WHERE tenant_id = 'evlogia';
DELETE FROM intercompany_transactions WHERE tenant_id = 'evlogia';
DELETE FROM kds_orders WHERE tenant_id = 'evlogia';
DELETE FROM reconciliation_sessions WHERE tenant_id = 'evlogia';
DELETE FROM approval_requests WHERE tenant_id = 'evlogia';
DELETE FROM approval_delegates WHERE tenant_id = 'evlogia';
DELETE FROM recurring_invoices WHERE tenant_id = 'evlogia';
DELETE FROM recurring_bills WHERE tenant_id = 'evlogia';
DELETE FROM budgets WHERE tenant_id = 'evlogia';
DELETE FROM forex_gain_loss WHERE tenant_id = 'evlogia';
DELETE FROM opening_balance_records WHERE tenant_id = 'evlogia';
DELETE FROM bukti_potong WHERE tenant_id = 'evlogia';
DELETE FROM transaksi_harian WHERE tenant_id = 'evlogia';
DELETE FROM item_transaksi WHERE tenant_id = 'evlogia';
DELETE FROM documents WHERE tenant_id = 'evlogia';

-- Master data
DELETE FROM products WHERE tenant_id = 'evlogia';
DELETE FROM persediaan WHERE tenant_id = 'evlogia';
DELETE FROM menu_items WHERE tenant_id = 'evlogia';
DELETE FROM menu_categories WHERE tenant_id = 'evlogia';
DELETE FROM recipes WHERE tenant_id = 'evlogia';
DELETE FROM bill_of_materials WHERE tenant_id = 'evlogia';
DELETE FROM customers WHERE tenant_id = 'evlogia';
DELETE FROM vendors WHERE tenant_id = 'evlogia';
DELETE FROM suppliers WHERE tenant_id = 'evlogia';
DELETE FROM bank_accounts WHERE tenant_id = 'evlogia';
DELETE FROM warehouse_bins WHERE tenant_id = 'evlogia';
DELETE FROM warehouses WHERE tenant_id = 'evlogia';
DELETE FROM asset_categories WHERE tenant_id = 'evlogia';
DELETE FROM cost_centers WHERE tenant_id = 'evlogia';
DELETE FROM cost_pools WHERE tenant_id = 'evlogia';
DELETE FROM cost_variances WHERE tenant_id = 'evlogia';
DELETE FROM overhead_allocations WHERE tenant_id = 'evlogia';
DELETE FROM standard_costs WHERE tenant_id = 'evlogia';
DELETE FROM work_centers WHERE tenant_id = 'evlogia';
DELETE FROM bagan_akun WHERE tenant_id = 'evlogia';
DELETE FROM chart_of_accounts WHERE tenant_id = 'evlogia';
DELETE FROM kds_stations WHERE tenant_id = 'evlogia';
DELETE FROM restaurant_tables WHERE tenant_id = 'evlogia';
DELETE FROM table_areas WHERE tenant_id = 'evlogia';
DELETE FROM approval_workflows WHERE tenant_id = 'evlogia';
DELETE FROM branches WHERE tenant_id = 'evlogia';

-- Sequences (reset for fresh numbering)
DELETE FROM sales_invoice_sequences WHERE tenant_id = 'evlogia';
DELETE FROM bill_number_sequences WHERE tenant_id = 'evlogia';
DELETE FROM expense_sequences WHERE tenant_id = 'evlogia';
DELETE FROM journal_number_sequences WHERE tenant_id = 'evlogia';
DELETE FROM quote_sequences WHERE tenant_id = 'evlogia';
DELETE FROM sales_order_sequences WHERE tenant_id = 'evlogia';
DELETE FROM purchase_order_sequences WHERE tenant_id = 'evlogia';
DELETE FROM receive_payment_sequences WHERE tenant_id = 'evlogia';
DELETE FROM bill_payment_sequences WHERE tenant_id = 'evlogia';
DELETE FROM credit_note_sequences WHERE tenant_id = 'evlogia';
DELETE FROM vendor_credit_sequences WHERE tenant_id = 'evlogia';
DELETE FROM customer_deposit_sequences WHERE tenant_id = 'evlogia';
DELETE FROM vendor_deposit_sequences WHERE tenant_id = 'evlogia';
DELETE FROM bank_reconciliation_sequences WHERE tenant_id = 'evlogia';
DELETE FROM bank_transfer_sequences WHERE tenant_id = 'evlogia';
DELETE FROM stock_adjustment_sequences WHERE tenant_id = 'evlogia';
DELETE FROM stock_transfer_sequences WHERE tenant_id = 'evlogia';
DELETE FROM fixed_asset_sequences WHERE tenant_id = 'evlogia';
DELETE FROM production_sequences WHERE tenant_id = 'evlogia';
DELETE FROM sales_receipt_sequences WHERE tenant_id = 'evlogia';
DELETE FROM cheque_sequences WHERE tenant_id = 'evlogia';
DELETE FROM intercompany_sequences WHERE tenant_id = 'evlogia';
DELETE FROM reservation_sequences WHERE tenant_id = 'evlogia';
DELETE FROM shipment_sequences WHERE tenant_id = 'evlogia';
DELETE FROM efaktur_sequences WHERE tenant_id = 'evlogia';
DELETE FROM item_code_sequences WHERE tenant_id = 'evlogia';
DELETE FROM branch_sequences WHERE tenant_id = 'evlogia';

-- Fiscal periods
DELETE FROM fiscal_periods WHERE tenant_id = 'evlogia';
DELETE FROM fiscal_years WHERE tenant_id = 'evlogia';

-- Cache and snapshots
DELETE FROM account_balances_daily WHERE tenant_id = 'evlogia';
DELETE FROM trial_balance_snapshots WHERE tenant_id = 'evlogia';
DELETE FROM aging_snapshots WHERE tenant_id = 'evlogia';
DELETE FROM aging_brackets WHERE tenant_id = 'evlogia';
DELETE FROM report_balance_cache WHERE tenant_id = 'evlogia';
DELETE FROM ratio_snapshots WHERE tenant_id = 'evlogia';
DELETE FROM ratio_alerts WHERE tenant_id = 'evlogia';

-- Config and settings
DELETE FROM accounting_settings WHERE tenant_id = 'evlogia';
DELETE FROM tenant_config WHERE tenant_id = 'evlogia';
DELETE FROM tenant_rules WHERE tenant_id = 'evlogia';
DELETE FROM tax_info WHERE tenant_id = 'evlogia';
DELETE FROM currencies WHERE tenant_id = 'evlogia';
DELETE FROM exchange_rates WHERE tenant_id = 'evlogia';
DELETE FROM ratio_definitions WHERE tenant_id = 'evlogia';
DELETE FROM industry_benchmarks WHERE tenant_id = 'evlogia';
DELETE FROM faqs WHERE tenant_id = 'evlogia';

-- Audit logs (clear for fresh start)
DELETE FROM audit_logs WHERE tenant_id = 'evlogia';
DELETE FROM accounting_outbox WHERE tenant_id = 'evlogia';
DELETE FROM master_data_audit_log WHERE tenant_id = 'evlogia';
DELETE FROM outbox WHERE tenant_id = 'evlogia';
DELETE FROM sensitive_data_access WHERE tenant_id = 'evlogia';

-- Chat and session data
DELETE FROM chat_messages WHERE tenant_id = 'evlogia';
DELETE FROM refresh_tokens WHERE tenant_id = 'evlogia';
DELETE FROM user_devices WHERE tenant_id = 'evlogia';

-- Re-enable FK checks
SET session_replication_role = 'origin';

COMMIT;

-- Verify
SELECT 'RESET COMPLETE' as status;
SELECT 'sales_invoices' as table_name, COUNT(*) as count FROM sales_invoices WHERE tenant_id = 'evlogia'
UNION ALL SELECT 'bills', COUNT(*) FROM bills WHERE tenant_id = 'evlogia'
UNION ALL SELECT 'expenses', COUNT(*) FROM expenses WHERE tenant_id = 'evlogia'
UNION ALL SELECT 'journal_entries', COUNT(*) FROM journal_entries WHERE tenant_id = 'evlogia'
UNION ALL SELECT 'customers', COUNT(*) FROM customers WHERE tenant_id = 'evlogia'
UNION ALL SELECT 'vendors', COUNT(*) FROM vendors WHERE tenant_id = 'evlogia'
UNION ALL SELECT 'products', COUNT(*) FROM products WHERE tenant_id = 'evlogia'
UNION ALL SELECT 'chart_of_accounts', COUNT(*) FROM chart_of_accounts WHERE tenant_id = 'evlogia';
