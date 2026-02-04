-- =====================================================
-- RESET TENANT EVLOGIA - FULL DATA WIPE
-- =====================================================
-- WARNING: This will DELETE ALL data for tenant evlogia
-- Making it like a brand new tenant
-- =====================================================

BEGIN;

-- Tenant ID
DO $$
DECLARE
    tid TEXT := 'evlogia';
BEGIN
    RAISE NOTICE 'Starting full reset for tenant: %', tid;
    
    -- =====================================================
    -- PHASE 1: Delete Transaction Detail Tables (Child)
    -- =====================================================
    RAISE NOTICE 'Phase 1: Deleting transaction details...';
    
    -- Sales related
    DELETE FROM sales_invoice_items WHERE invoice_id IN (SELECT id FROM sales_invoices WHERE tenant_id = tid);
    DELETE FROM sales_invoice_payments WHERE invoice_id IN (SELECT id FROM sales_invoices WHERE tenant_id = tid);
    DELETE FROM receive_payment_allocations WHERE tenant_id = tid;
    DELETE FROM ar_payment_applications WHERE tenant_id = tid;
    DELETE FROM accounts_receivable WHERE tenant_id = tid;
    
    -- Bill related  
    DELETE FROM bill_items WHERE bill_id IN (SELECT id FROM bills WHERE tenant_id = tid);
    DELETE FROM bill_attachments WHERE bill_id IN (SELECT id FROM bills WHERE tenant_id = tid);
    DELETE FROM bill_payment_allocations WHERE bill_id IN (SELECT id FROM bills WHERE tenant_id = tid);
    DELETE FROM ap_payment_applications WHERE tenant_id = tid;
    DELETE FROM accounts_payable WHERE tenant_id = tid;
    
    -- Expense related
    DELETE FROM expense_items WHERE expense_id IN (SELECT id FROM expenses WHERE tenant_id = tid);
    DELETE FROM expense_attachments WHERE expense_id IN (SELECT id FROM expenses WHERE tenant_id = tid);
    
    -- Journal related
    DELETE FROM journal_lines WHERE journal_entry_id IN (SELECT id FROM journal_entries WHERE tenant_id = tid);
    DELETE FROM jurnal_detail WHERE jurnal_entry_id IN (SELECT id FROM jurnal_entry WHERE tenant_id = tid);
    
    -- Quote related
    DELETE FROM quote_items WHERE quote_id IN (SELECT id FROM quotes WHERE tenant_id = tid);
    
    -- Sales Order related
    DELETE FROM sales_order_items WHERE sales_order_id IN (SELECT id FROM sales_orders WHERE tenant_id = tid);
    DELETE FROM sales_order_shipment_items WHERE shipment_id IN (SELECT id FROM sales_order_shipments WHERE tenant_id = tid);
    DELETE FROM sales_order_shipments WHERE tenant_id = tid;
    
    -- Purchase Order related
    DELETE FROM purchase_order_items WHERE purchase_order_id IN (SELECT id FROM purchase_orders WHERE tenant_id = tid);
    
    -- Credit Note related
    DELETE FROM credit_note_items WHERE credit_note_id IN (SELECT id FROM credit_notes WHERE tenant_id = tid);
    DELETE FROM credit_note_applications WHERE tenant_id = tid;
    DELETE FROM credit_note_refunds WHERE tenant_id = tid;
    
    -- Vendor Credit related
    DELETE FROM vendor_credit_items WHERE vendor_credit_id IN (SELECT id FROM vendor_credits WHERE tenant_id = tid);
    DELETE FROM vendor_credit_applications WHERE tenant_id = tid;
    DELETE FROM vendor_credit_refunds WHERE tenant_id = tid;
    
    -- Customer Deposits
    DELETE FROM customer_deposit_applications WHERE tenant_id = tid;
    DELETE FROM customer_deposit_refunds WHERE tenant_id = tid;
    
    -- Vendor Deposits
    DELETE FROM vendor_deposit_applications WHERE tenant_id = tid;
    DELETE FROM vendor_deposit_refunds WHERE tenant_id = tid;
    
    -- Bank related
    DELETE FROM bank_reconciliation_items WHERE reconciliation_id IN (SELECT id FROM bank_reconciliations WHERE tenant_id = tid);
    DELETE FROM bank_statement_lines WHERE import_id IN (SELECT id FROM bank_statement_imports WHERE tenant_id = tid);
    DELETE FROM bank_statement_lines_v2 WHERE tenant_id = tid;
    
    -- Inventory related
    DELETE FROM stock_adjustment_items WHERE adjustment_id IN (SELECT id FROM stock_adjustments WHERE tenant_id = tid);
    DELETE FROM stock_transfer_items WHERE transfer_id IN (SELECT id FROM stock_transfers WHERE tenant_id = tid);
    DELETE FROM item_inventory WHERE product_id IN (SELECT id FROM products WHERE tenant_id = tid);
    DELETE FROM item_pricing WHERE product_id IN (SELECT id FROM products WHERE tenant_id = tid);
    DELETE FROM item_batches WHERE product_id IN (SELECT id FROM products WHERE tenant_id = tid);
    DELETE FROM item_serials WHERE product_id IN (SELECT id FROM products WHERE tenant_id = tid);
    DELETE FROM item_activities WHERE tenant_id = tid;
    DELETE FROM warehouse_stock WHERE tenant_id = tid;
    DELETE FROM batch_warehouse_stock WHERE tenant_id = tid;
    DELETE FROM bin_stock WHERE tenant_id = tid;
    DELETE FROM serial_movements WHERE tenant_id = tid;
    DELETE FROM kartu_stok WHERE tenant_id = tid;
    DELETE FROM inventory_ledger WHERE tenant_id = tid;
    
    -- Fixed Asset related
    DELETE FROM asset_depreciations WHERE asset_id IN (SELECT id FROM fixed_assets WHERE tenant_id = tid);
    DELETE FROM asset_maintenance WHERE asset_id IN (SELECT id FROM fixed_assets WHERE tenant_id = tid);
    
    -- Production related
    DELETE FROM production_order_materials WHERE production_order_id IN (SELECT id FROM production_orders WHERE tenant_id = tid);
    DELETE FROM production_order_labor WHERE production_order_id IN (SELECT id FROM production_orders WHERE tenant_id = tid);
    DELETE FROM production_completions WHERE production_order_id IN (SELECT id FROM production_orders WHERE tenant_id = tid);
    DELETE FROM bom_components WHERE bom_id IN (SELECT id FROM bill_of_materials WHERE tenant_id = tid);
    DELETE FROM bom_operations WHERE bom_id IN (SELECT id FROM bill_of_materials WHERE tenant_id = tid);
    DELETE FROM bom_substitutes WHERE bom_id IN (SELECT id FROM bill_of_materials WHERE tenant_id = tid);
    
    -- Recipe related
    DELETE FROM recipe_ingredients WHERE recipe_id IN (SELECT id FROM recipes WHERE tenant_id = tid);
    DELETE FROM recipe_instructions WHERE recipe_id IN (SELECT id FROM recipes WHERE tenant_id = tid);
    DELETE FROM recipe_modifiers WHERE recipe_id IN (SELECT id FROM recipes WHERE tenant_id = tid);
    
    -- Budget related
    DELETE FROM budget_items WHERE budget_id IN (SELECT id FROM budgets WHERE tenant_id = tid);
    DELETE FROM budget_revisions WHERE budget_id IN (SELECT id FROM budgets WHERE tenant_id = tid);
    
    -- Recurring related
    DELETE FROM recurring_invoice_items WHERE invoice_id IN (SELECT id FROM recurring_invoices WHERE tenant_id = tid);
    DELETE FROM recurring_bill_items WHERE bill_id IN (SELECT id FROM recurring_bills WHERE tenant_id = tid);
    
    -- Approval related
    DELETE FROM approval_actions WHERE request_id IN (SELECT id FROM approval_requests WHERE tenant_id = tid);
    DELETE FROM approval_levels WHERE workflow_id IN (SELECT id FROM approval_workflows WHERE tenant_id = tid);
    
    -- Sales Receipt related
    DELETE FROM sales_receipt_items WHERE receipt_id IN (SELECT id FROM sales_receipts WHERE tenant_id = tid);
    
    -- Vendor related
    DELETE FROM vendor_addresses WHERE vendor_id IN (SELECT id FROM vendors WHERE tenant_id = tid);
    DELETE FROM vendor_contacts WHERE vendor_id IN (SELECT id FROM vendors WHERE tenant_id = tid);
    DELETE FROM vendor_activities WHERE tenant_id = tid;
    
    -- Customer related
    DELETE FROM customer_activities WHERE tenant_id = tid;
    
    -- KDS related
    DELETE FROM kds_order_items WHERE order_id IN (SELECT id FROM kds_orders WHERE tenant_id = tid);
    DELETE FROM kds_item_history WHERE tenant_id = tid;
    DELETE FROM kds_alerts WHERE tenant_id = tid;
    
    -- Table/Restaurant related
    DELETE FROM table_sessions WHERE table_id IN (SELECT id FROM restaurant_tables WHERE tenant_id = tid);
    DELETE FROM table_waitlist WHERE table_id IN (SELECT id FROM restaurant_tables WHERE tenant_id = tid);
    DELETE FROM table_reservations WHERE tenant_id = tid;
    
    -- Cheque related
    DELETE FROM cheque_status_history WHERE cheque_id IN (SELECT id FROM cheques WHERE tenant_id = tid);
    
    -- Intercompany related
    DELETE FROM intercompany_balances WHERE tenant_id = tid;
    DELETE FROM intercompany_settlements WHERE tenant_id = tid;
    
    RAISE NOTICE 'Phase 1 complete.';
    
    -- =====================================================
    -- PHASE 2: Delete Transaction Parent Tables
    -- =====================================================
    RAISE NOTICE 'Phase 2: Deleting transaction parents...';
    
    DELETE FROM sales_invoices WHERE tenant_id = tid;
    DELETE FROM bills WHERE tenant_id = tid;
    DELETE FROM expenses WHERE tenant_id = tid;
    DELETE FROM journal_entries WHERE tenant_id = tid;
    DELETE FROM jurnal_entry WHERE tenant_id = tid;
    DELETE FROM quotes WHERE tenant_id = tid;
    DELETE FROM sales_orders WHERE tenant_id = tid;
    DELETE FROM purchase_orders WHERE tenant_id = tid;
    DELETE FROM receive_payments WHERE tenant_id = tid;
    DELETE FROM bill_payments WHERE tenant_id = tid;
    DELETE FROM bill_payments_v2 WHERE tenant_id = tid;
    DELETE FROM credit_notes WHERE tenant_id = tid;
    DELETE FROM vendor_credits WHERE tenant_id = tid;
    DELETE FROM customer_deposits WHERE tenant_id = tid;
    DELETE FROM vendor_deposits WHERE tenant_id = tid;
    DELETE FROM bank_reconciliations WHERE tenant_id = tid;
    DELETE FROM bank_statement_imports WHERE tenant_id = tid;
    DELETE FROM bank_transactions WHERE tenant_id = tid;
    DELETE FROM bank_transfers WHERE tenant_id = tid;
    DELETE FROM stock_adjustments WHERE tenant_id = tid;
    DELETE FROM stock_transfers WHERE tenant_id = tid;
    DELETE FROM fixed_assets WHERE tenant_id = tid;
    DELETE FROM production_orders WHERE tenant_id = tid;
    DELETE FROM sales_receipts WHERE tenant_id = tid;
    DELETE FROM cheques WHERE tenant_id = tid;
    DELETE FROM intercompany_transactions WHERE tenant_id = tid;
    DELETE FROM kds_orders WHERE tenant_id = tid;
    DELETE FROM reconciliation_matches WHERE tenant_id = tid;
    DELETE FROM reconciliation_adjustments WHERE tenant_id = tid;
    DELETE FROM reconciliation_sessions WHERE tenant_id = tid;
    DELETE FROM approval_requests WHERE tenant_id = tid;
    DELETE FROM approval_delegates WHERE tenant_id = tid;
    DELETE FROM recurring_invoices WHERE tenant_id = tid;
    DELETE FROM recurring_bills WHERE tenant_id = tid;
    DELETE FROM budgets WHERE tenant_id = tid;
    DELETE FROM forex_gain_loss WHERE tenant_id = tid;
    DELETE FROM opening_balance_records WHERE tenant_id = tid;
    DELETE FROM bukti_potong WHERE tenant_id = tid;
    DELETE FROM transaksi_harian WHERE tenant_id = tid;
    DELETE FROM item_transaksi WHERE tenant_id = tid;
    
    RAISE NOTICE 'Phase 2 complete.';
    
    -- =====================================================
    -- PHASE 3: Delete Master Data
    -- =====================================================
    RAISE NOTICE 'Phase 3: Deleting master data...';
    
    -- Products and related
    DELETE FROM unit_conversions WHERE product_id IN (SELECT id FROM products WHERE tenant_id = tid);
    DELETE FROM products WHERE tenant_id = tid;
    DELETE FROM persediaan WHERE tenant_id = tid;
    DELETE FROM menu_items WHERE tenant_id = tid;
    DELETE FROM menu_categories WHERE tenant_id = tid;
    DELETE FROM recipes WHERE tenant_id = tid;
    DELETE FROM bill_of_materials WHERE tenant_id = tid;
    
    -- Customers and Vendors
    DELETE FROM customers WHERE tenant_id = tid;
    DELETE FROM vendors WHERE tenant_id = tid;
    DELETE FROM suppliers WHERE tenant_id = tid;
    
    -- Bank Accounts
    DELETE FROM bank_accounts WHERE tenant_id = tid;
    
    -- Warehouses
    DELETE FROM warehouse_bins WHERE tenant_id = tid;
    DELETE FROM warehouses WHERE tenant_id = tid;
    
    -- Fixed Asset Categories
    DELETE FROM asset_categories WHERE tenant_id = tid;
    
    -- Cost Centers
    DELETE FROM cost_centers WHERE tenant_id = tid;
    DELETE FROM cost_pools WHERE tenant_id = tid;
    DELETE FROM cost_variances WHERE tenant_id = tid;
    DELETE FROM overhead_allocations WHERE tenant_id = tid;
    DELETE FROM standard_costs WHERE tenant_id = tid;
    
    -- Work Centers
    DELETE FROM work_centers WHERE tenant_id = tid;
    
    -- Chart of Accounts (bagan_akun)
    DELETE FROM bagan_akun WHERE tenant_id = tid;
    DELETE FROM chart_of_accounts WHERE tenant_id = tid;
    
    -- KDS Stations
    DELETE FROM kds_stations WHERE tenant_id = tid;
    
    -- Restaurant Tables
    DELETE FROM restaurant_tables WHERE tenant_id = tid;
    DELETE FROM table_areas WHERE tenant_id = tid;
    
    -- Approval Workflows
    DELETE FROM approval_workflows WHERE tenant_id = tid;
    
    -- Branches
    DELETE FROM branches WHERE tenant_id = tid;
    
    -- Consolidation
    DELETE FROM consolidation_account_mappings WHERE entity_id IN (SELECT id FROM consolidation_entities WHERE tenant_id IN (SELECT id FROM consolidation_groups WHERE tenant_id = tid));
    DELETE FROM consolidation_entities WHERE group_id IN (SELECT id FROM consolidation_groups WHERE tenant_id = tid);
    DELETE FROM consolidation_runs WHERE group_id IN (SELECT id FROM consolidation_groups WHERE tenant_id = tid);
    DELETE FROM consolidation_groups WHERE tenant_id = tid;
    
    -- Intercompany
    DELETE FROM intercompany_relationships WHERE parent_tenant_id = tid OR child_tenant_id = tid;
    
    RAISE NOTICE 'Phase 3 complete.';
    
    -- =====================================================
    -- PHASE 4: Delete Supporting Data
    -- =====================================================
    RAISE NOTICE 'Phase 4: Deleting supporting data...';
    
    -- Documents
    DELETE FROM document_attachments WHERE document_id IN (SELECT id FROM documents WHERE tenant_id = tid);
    DELETE FROM documents WHERE tenant_id = tid;
    
    -- Sequences (reset to start fresh)
    DELETE FROM sales_invoice_sequences WHERE tenant_id = tid;
    DELETE FROM bill_number_sequences WHERE tenant_id = tid;
    DELETE FROM expense_sequences WHERE tenant_id = tid;
    DELETE FROM journal_number_sequences WHERE tenant_id = tid;
    DELETE FROM quote_sequences WHERE tenant_id = tid;
    DELETE FROM sales_order_sequences WHERE tenant_id = tid;
    DELETE FROM purchase_order_sequences WHERE tenant_id = tid;
    DELETE FROM receive_payment_sequences WHERE tenant_id = tid;
    DELETE FROM bill_payment_sequences WHERE tenant_id = tid;
    DELETE FROM credit_note_sequences WHERE tenant_id = tid;
    DELETE FROM vendor_credit_sequences WHERE tenant_id = tid;
    DELETE FROM customer_deposit_sequences WHERE tenant_id = tid;
    DELETE FROM vendor_deposit_sequences WHERE tenant_id = tid;
    DELETE FROM bank_reconciliation_sequences WHERE tenant_id = tid;
    DELETE FROM bank_transfer_sequences WHERE tenant_id = tid;
    DELETE FROM stock_adjustment_sequences WHERE tenant_id = tid;
    DELETE FROM stock_transfer_sequences WHERE tenant_id = tid;
    DELETE FROM fixed_asset_sequences WHERE tenant_id = tid;
    DELETE FROM production_sequences WHERE tenant_id = tid;
    DELETE FROM sales_receipt_sequences WHERE tenant_id = tid;
    DELETE FROM cheque_sequences WHERE tenant_id = tid;
    DELETE FROM intercompany_sequences WHERE tenant_id = tid;
    DELETE FROM reservation_sequences WHERE tenant_id = tid;
    DELETE FROM shipment_sequences WHERE tenant_id = tid;
    DELETE FROM efaktur_sequences WHERE tenant_id = tid;
    DELETE FROM item_code_sequences WHERE tenant_id = tid;
    DELETE FROM branch_sequences WHERE tenant_id = tid;
    
    -- Fiscal periods and years
    DELETE FROM fiscal_periods WHERE tenant_id = tid;
    DELETE FROM fiscal_years WHERE tenant_id = tid;
    
    -- Cache and Snapshots
    DELETE FROM account_balances_daily WHERE tenant_id = tid;
    DELETE FROM trial_balance_snapshots WHERE tenant_id = tid;
    DELETE FROM aging_snapshots WHERE tenant_id = tid;
    DELETE FROM aging_brackets WHERE tenant_id = tid;
    DELETE FROM report_balance_cache WHERE tenant_id = tid;
    DELETE FROM ratio_snapshots WHERE tenant_id = tid;
    DELETE FROM ratio_alerts WHERE tenant_id = tid;
    
    -- Config and Settings
    DELETE FROM accounting_settings WHERE tenant_id = tid;
    DELETE FROM tenant_config WHERE tenant_id = tid;
    DELETE FROM tenant_rules WHERE tenant_id = tid;
    DELETE FROM tax_info WHERE tenant_id = tid;
    DELETE FROM currencies WHERE tenant_id = tid;
    DELETE FROM exchange_rates WHERE tenant_id = tid;
    DELETE FROM ratio_definitions WHERE tenant_id = tid;
    DELETE FROM industry_benchmarks WHERE tenant_id = tid;
    DELETE FROM faqs WHERE tenant_id = tid;
    
    -- Audit and Outbox (keep for debugging but can clear)
    DELETE FROM audit_logs WHERE tenant_id = tid;
    DELETE FROM accounting_outbox WHERE tenant_id = tid;
    DELETE FROM master_data_audit_log WHERE tenant_id = tid;
    DELETE FROM outbox WHERE tenant_id = tid;
    DELETE FROM sensitive_data_access WHERE tenant_id = tid;
    
    -- Chat messages
    DELETE FROM chat_messages WHERE tenant_id = tid;
    
    -- Refresh tokens (keep sessions but clear data-related tokens)
    DELETE FROM refresh_tokens WHERE tenant_id = tid;
    
    -- User devices
    DELETE FROM user_devices WHERE tenant_id = tid;
    
    RAISE NOTICE 'Phase 4 complete.';
    
    RAISE NOTICE '========================================';
    RAISE NOTICE 'RESET COMPLETE for tenant: %', tid;
    RAISE NOTICE 'Tenant is now like a fresh new user.';
    RAISE NOTICE '========================================';
    
END $$;

COMMIT;
