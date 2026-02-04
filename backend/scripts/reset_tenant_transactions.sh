#!/bin/bash
# =====================================================
# RESET TENANT - Keep Only Chart of Accounts
# Usage: ./reset_tenant_transactions.sh <tenant_id>
# =====================================================

TENANT_ID=$1

if [ -z "$TENANT_ID" ]; then
    echo "Usage: $0 <tenant_id>"
    echo "Example: $0 evlogia"
    exit 1
fi

echo "========================================"
echo "RESETTING TENANT: $TENANT_ID"
echo "Keeping ONLY: Chart of Accounts (Daftar Akun)"
echo "Deleting: All transactions + Master data"
echo "========================================"

docker exec milkyhoop-dev-postgres-1 psql -U postgres -d milkydb -c "
SET session_replication_role = 'replica';

-- PHASE 1: Delete transaction details
DELETE FROM sales_invoice_items WHERE invoice_id IN (SELECT id FROM sales_invoices WHERE tenant_id = '$TENANT_ID');
DELETE FROM sales_invoice_payments WHERE invoice_id IN (SELECT id FROM sales_invoices WHERE tenant_id = '$TENANT_ID');
DELETE FROM bill_items WHERE bill_id IN (SELECT id FROM bills WHERE tenant_id = '$TENANT_ID');
DELETE FROM bill_attachments WHERE bill_id IN (SELECT id FROM bills WHERE tenant_id = '$TENANT_ID');
DELETE FROM bill_payment_allocations WHERE bill_id IN (SELECT id FROM bills WHERE tenant_id = '$TENANT_ID');
DELETE FROM expense_items WHERE expense_id IN (SELECT id FROM expenses WHERE tenant_id = '$TENANT_ID');
DELETE FROM expense_attachments WHERE expense_id IN (SELECT id FROM expenses WHERE tenant_id = '$TENANT_ID');
DELETE FROM journal_lines WHERE journal_id IN (SELECT id FROM journal_entries WHERE tenant_id = '$TENANT_ID');
DELETE FROM jurnal_detail WHERE jurnal_entry_id IN (SELECT id FROM jurnal_entry WHERE tenant_id = '$TENANT_ID');
DELETE FROM quote_items WHERE quote_id IN (SELECT id FROM quotes WHERE tenant_id = '$TENANT_ID');
DELETE FROM sales_order_items WHERE order_id IN (SELECT id FROM sales_orders WHERE tenant_id = '$TENANT_ID');
DELETE FROM sales_order_shipment_items WHERE shipment_id IN (SELECT id FROM sales_order_shipments WHERE tenant_id = '$TENANT_ID');
DELETE FROM purchase_order_items WHERE po_id IN (SELECT id FROM purchase_orders WHERE tenant_id = '$TENANT_ID');
DELETE FROM credit_note_items WHERE credit_note_id IN (SELECT id FROM credit_notes WHERE tenant_id = '$TENANT_ID');
DELETE FROM vendor_credit_items WHERE vendor_credit_id IN (SELECT id FROM vendor_credits WHERE tenant_id = '$TENANT_ID');
DELETE FROM bank_reconciliation_items WHERE reconciliation_id IN (SELECT id FROM bank_reconciliations WHERE tenant_id = '$TENANT_ID');
DELETE FROM bank_statement_lines WHERE import_id IN (SELECT id FROM bank_statement_imports WHERE tenant_id = '$TENANT_ID');
DELETE FROM stock_adjustment_items WHERE adjustment_id IN (SELECT id FROM stock_adjustments WHERE tenant_id = '$TENANT_ID');
DELETE FROM stock_transfer_items WHERE transfer_id IN (SELECT id FROM stock_transfers WHERE tenant_id = '$TENANT_ID');
DELETE FROM sales_receipt_items WHERE receipt_id IN (SELECT id FROM sales_receipts WHERE tenant_id = '$TENANT_ID');
DELETE FROM cheque_status_history WHERE cheque_id IN (SELECT id FROM cheques WHERE tenant_id = '$TENANT_ID');
DELETE FROM asset_depreciations WHERE asset_id IN (SELECT id FROM fixed_assets WHERE tenant_id = '$TENANT_ID');
DELETE FROM asset_maintenance WHERE asset_id IN (SELECT id FROM fixed_assets WHERE tenant_id = '$TENANT_ID');
DELETE FROM production_order_materials WHERE production_order_id IN (SELECT id FROM production_orders WHERE tenant_id = '$TENANT_ID');
DELETE FROM production_order_labor WHERE production_order_id IN (SELECT id FROM production_orders WHERE tenant_id = '$TENANT_ID');
DELETE FROM production_completions WHERE production_order_id IN (SELECT id FROM production_orders WHERE tenant_id = '$TENANT_ID');
DELETE FROM bom_components WHERE bom_id IN (SELECT id FROM bill_of_materials WHERE tenant_id = '$TENANT_ID');
DELETE FROM bom_operations WHERE bom_id IN (SELECT id FROM bill_of_materials WHERE tenant_id = '$TENANT_ID');
DELETE FROM bom_substitutes WHERE bom_id IN (SELECT id FROM bill_of_materials WHERE tenant_id = '$TENANT_ID');
DELETE FROM recipe_ingredients WHERE recipe_id IN (SELECT id FROM recipes WHERE tenant_id = '$TENANT_ID');
DELETE FROM recipe_instructions WHERE recipe_id IN (SELECT id FROM recipes WHERE tenant_id = '$TENANT_ID');
DELETE FROM recipe_modifiers WHERE recipe_id IN (SELECT id FROM recipes WHERE tenant_id = '$TENANT_ID');
DELETE FROM recurring_invoice_items WHERE invoice_id IN (SELECT id FROM recurring_invoices WHERE tenant_id = '$TENANT_ID');
DELETE FROM recurring_bill_items WHERE bill_id IN (SELECT id FROM recurring_bills WHERE tenant_id = '$TENANT_ID');
DELETE FROM approval_actions WHERE request_id IN (SELECT id FROM approval_requests WHERE tenant_id = '$TENANT_ID');
DELETE FROM approval_levels WHERE workflow_id IN (SELECT id FROM approval_workflows WHERE tenant_id = '$TENANT_ID');
DELETE FROM document_attachments WHERE document_id IN (SELECT id FROM documents WHERE tenant_id = '$TENANT_ID');
DELETE FROM vendor_addresses WHERE vendor_id IN (SELECT id FROM vendors WHERE tenant_id = '$TENANT_ID');
DELETE FROM vendor_contacts WHERE vendor_id IN (SELECT id FROM vendors WHERE tenant_id = '$TENANT_ID');
DELETE FROM unit_conversions WHERE product_id IN (SELECT id FROM products WHERE tenant_id = '$TENANT_ID');
DELETE FROM item_pricing WHERE produk_id::text IN (SELECT id::text FROM products WHERE tenant_id = '$TENANT_ID');
DELETE FROM item_batches WHERE produk_id::text IN (SELECT id::text FROM products WHERE tenant_id = '$TENANT_ID');
DELETE FROM item_serials WHERE produk_id::text IN (SELECT id::text FROM products WHERE tenant_id = '$TENANT_ID');

-- PHASE 2: Delete AR/AP/Deposits
DELETE FROM receive_payment_allocations WHERE tenant_id = '$TENANT_ID';
DELETE FROM ar_payment_applications WHERE tenant_id = '$TENANT_ID';
DELETE FROM accounts_receivable WHERE tenant_id = '$TENANT_ID';
DELETE FROM ap_payment_applications WHERE tenant_id = '$TENANT_ID';
DELETE FROM accounts_payable WHERE tenant_id = '$TENANT_ID';
DELETE FROM credit_note_applications WHERE tenant_id = '$TENANT_ID';
DELETE FROM credit_note_refunds WHERE tenant_id = '$TENANT_ID';
DELETE FROM vendor_credit_applications WHERE tenant_id = '$TENANT_ID';
DELETE FROM vendor_credit_refunds WHERE tenant_id = '$TENANT_ID';
DELETE FROM customer_deposit_applications WHERE tenant_id = '$TENANT_ID';
DELETE FROM customer_deposit_refunds WHERE tenant_id = '$TENANT_ID';
DELETE FROM vendor_deposit_applications WHERE tenant_id = '$TENANT_ID';
DELETE FROM vendor_deposit_refunds WHERE tenant_id = '$TENANT_ID';

-- PHASE 3: Delete inventory
DELETE FROM warehouse_stock WHERE tenant_id = '$TENANT_ID';
DELETE FROM batch_warehouse_stock WHERE tenant_id = '$TENANT_ID';
DELETE FROM bin_stock WHERE tenant_id = '$TENANT_ID';
DELETE FROM serial_movements WHERE tenant_id = '$TENANT_ID';
DELETE FROM kartu_stok WHERE tenant_id = '$TENANT_ID';
DELETE FROM inventory_ledger WHERE tenant_id = '$TENANT_ID';
DELETE FROM item_activities WHERE tenant_id = '$TENANT_ID';
DELETE FROM persediaan WHERE tenant_id = '$TENANT_ID';
DELETE FROM item_inventory WHERE tenant_id = '$TENANT_ID';
DELETE FROM customer_activities WHERE tenant_id = '$TENANT_ID';
DELETE FROM vendor_activities WHERE tenant_id = '$TENANT_ID';

-- PHASE 4: Delete transactions
DELETE FROM sales_invoices WHERE tenant_id = '$TENANT_ID';
DELETE FROM bills WHERE tenant_id = '$TENANT_ID';
DELETE FROM expenses WHERE tenant_id = '$TENANT_ID';
DELETE FROM journal_entries WHERE tenant_id = '$TENANT_ID';
DELETE FROM jurnal_entry WHERE tenant_id = '$TENANT_ID';
DELETE FROM quotes WHERE tenant_id = '$TENANT_ID';
DELETE FROM sales_order_shipments WHERE tenant_id = '$TENANT_ID';
DELETE FROM sales_orders WHERE tenant_id = '$TENANT_ID';
DELETE FROM purchase_orders WHERE tenant_id = '$TENANT_ID';
DELETE FROM receive_payments WHERE tenant_id = '$TENANT_ID';
DELETE FROM bill_payments WHERE tenant_id = '$TENANT_ID';
DELETE FROM bill_payments_v2 WHERE tenant_id = '$TENANT_ID';
DELETE FROM credit_notes WHERE tenant_id = '$TENANT_ID';
DELETE FROM vendor_credits WHERE tenant_id = '$TENANT_ID';
DELETE FROM customer_deposits WHERE tenant_id = '$TENANT_ID';
DELETE FROM vendor_deposits WHERE tenant_id = '$TENANT_ID';
DELETE FROM bank_reconciliations WHERE tenant_id = '$TENANT_ID';
DELETE FROM bank_statement_imports WHERE tenant_id = '$TENANT_ID';
DELETE FROM bank_transactions WHERE tenant_id = '$TENANT_ID';
DELETE FROM bank_transfers WHERE tenant_id = '$TENANT_ID';
DELETE FROM stock_adjustments WHERE tenant_id = '$TENANT_ID';
DELETE FROM stock_transfers WHERE tenant_id = '$TENANT_ID';
DELETE FROM fixed_assets WHERE tenant_id = '$TENANT_ID';
DELETE FROM production_orders WHERE tenant_id = '$TENANT_ID';
DELETE FROM sales_receipts WHERE tenant_id = '$TENANT_ID';
DELETE FROM cheques WHERE tenant_id = '$TENANT_ID';
DELETE FROM intercompany_transactions WHERE tenant_id = '$TENANT_ID';
DELETE FROM reconciliation_sessions WHERE tenant_id = '$TENANT_ID';
DELETE FROM approval_requests WHERE tenant_id = '$TENANT_ID';
DELETE FROM recurring_invoices WHERE tenant_id = '$TENANT_ID';
DELETE FROM recurring_bills WHERE tenant_id = '$TENANT_ID';
DELETE FROM forex_gain_loss WHERE tenant_id = '$TENANT_ID';
DELETE FROM opening_balance_records WHERE tenant_id = '$TENANT_ID';
DELETE FROM bukti_potong WHERE tenant_id = '$TENANT_ID';
DELETE FROM transaksi_harian WHERE tenant_id = '$TENANT_ID';
DELETE FROM documents WHERE tenant_id = '$TENANT_ID';

-- PHASE 5: Delete MASTER DATA (except COA)
DELETE FROM products WHERE tenant_id = '$TENANT_ID';
DELETE FROM customers WHERE tenant_id = '$TENANT_ID';
DELETE FROM vendors WHERE tenant_id = '$TENANT_ID';
DELETE FROM suppliers WHERE tenant_id = '$TENANT_ID';
DELETE FROM bank_accounts WHERE tenant_id = '$TENANT_ID';
DELETE FROM warehouse_bins WHERE tenant_id = '$TENANT_ID';
DELETE FROM warehouses WHERE tenant_id = '$TENANT_ID';
DELETE FROM bill_of_materials WHERE tenant_id = '$TENANT_ID';
DELETE FROM recipes WHERE tenant_id = '$TENANT_ID';
DELETE FROM menu_items WHERE tenant_id = '$TENANT_ID';
DELETE FROM menu_categories WHERE tenant_id = '$TENANT_ID';
DELETE FROM asset_categories WHERE tenant_id = '$TENANT_ID';
DELETE FROM cost_centers WHERE tenant_id = '$TENANT_ID';
DELETE FROM work_centers WHERE tenant_id = '$TENANT_ID';
DELETE FROM approval_workflows WHERE tenant_id = '$TENANT_ID';
DELETE FROM branches WHERE tenant_id = '$TENANT_ID';

-- PHASE 6: Delete caches/snapshots/sequences
DELETE FROM account_balances_daily WHERE tenant_id = '$TENANT_ID';
DELETE FROM trial_balance_snapshots WHERE tenant_id = '$TENANT_ID';
DELETE FROM aging_snapshots WHERE tenant_id = '$TENANT_ID';
DELETE FROM report_balance_cache WHERE tenant_id = '$TENANT_ID';
DELETE FROM ratio_snapshots WHERE tenant_id = '$TENANT_ID';
DELETE FROM sales_invoice_sequences WHERE tenant_id = '$TENANT_ID';
DELETE FROM bill_number_sequences WHERE tenant_id = '$TENANT_ID';
DELETE FROM expense_sequences WHERE tenant_id = '$TENANT_ID';
DELETE FROM journal_number_sequences WHERE tenant_id = '$TENANT_ID';
DELETE FROM quote_sequences WHERE tenant_id = '$TENANT_ID';
DELETE FROM sales_order_sequences WHERE tenant_id = '$TENANT_ID';
DELETE FROM purchase_order_sequences WHERE tenant_id = '$TENANT_ID';
DELETE FROM receive_payment_sequences WHERE tenant_id = '$TENANT_ID';
DELETE FROM bill_payment_sequences WHERE tenant_id = '$TENANT_ID';
DELETE FROM credit_note_sequences WHERE tenant_id = '$TENANT_ID';
DELETE FROM vendor_credit_sequences WHERE tenant_id = '$TENANT_ID';
DELETE FROM customer_deposit_sequences WHERE tenant_id = '$TENANT_ID';
DELETE FROM vendor_deposit_sequences WHERE tenant_id = '$TENANT_ID';
DELETE FROM bank_reconciliation_sequences WHERE tenant_id = '$TENANT_ID';
DELETE FROM bank_transfer_sequences WHERE tenant_id = '$TENANT_ID';
DELETE FROM stock_adjustment_sequences WHERE tenant_id = '$TENANT_ID';
DELETE FROM stock_transfer_sequences WHERE tenant_id = '$TENANT_ID';
DELETE FROM fixed_asset_sequences WHERE tenant_id = '$TENANT_ID';
DELETE FROM production_sequences WHERE tenant_id = '$TENANT_ID';
DELETE FROM sales_receipt_sequences WHERE tenant_id = '$TENANT_ID';
DELETE FROM shipment_sequences WHERE tenant_id = '$TENANT_ID';
DELETE FROM item_code_sequences WHERE tenant_id = '$TENANT_ID';
DELETE FROM fiscal_periods WHERE tenant_id = '$TENANT_ID';
DELETE FROM fiscal_years WHERE tenant_id = '$TENANT_ID';
DELETE FROM audit_logs WHERE tenant_id = '$TENANT_ID';
DELETE FROM accounting_outbox WHERE tenant_id = '$TENANT_ID';
DELETE FROM master_data_audit_log WHERE tenant_id = '$TENANT_ID';

SET session_replication_role = 'origin';
"

echo ""
echo "========== RESET COMPLETE =========="
docker exec milkyhoop-dev-postgres-1 psql -U postgres -d milkydb -c "
SELECT 'chart_of_accounts (KEPT)' as data, COUNT(*) as count FROM chart_of_accounts WHERE tenant_id = '$TENANT_ID'
UNION ALL SELECT 'customers (DELETED)', COUNT(*) FROM customers WHERE tenant_id = '$TENANT_ID'
UNION ALL SELECT 'vendors (DELETED)', COUNT(*) FROM vendors WHERE tenant_id = '$TENANT_ID'
UNION ALL SELECT 'products (DELETED)', COUNT(*) FROM products WHERE tenant_id = '$TENANT_ID'
UNION ALL SELECT 'sales_invoices (DELETED)', COUNT(*) FROM sales_invoices WHERE tenant_id = '$TENANT_ID'
UNION ALL SELECT 'journal_entries (DELETED)', COUNT(*) FROM journal_entries WHERE tenant_id = '$TENANT_ID';
"
