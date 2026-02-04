#!/bin/bash

PSQL="docker exec milkyhoop-dev-postgres-1 psql -U postgres -d milkydb"

echo "========================================"
echo "RESETTING TENANT EVLOGIA - FULL WIPE"
echo "========================================"

# Disable FK checks
$PSQL -c "SET session_replication_role = 'replica';"

echo "Phase 1: Deleting detail/child tables..."

# Sales Invoice related
$PSQL -c "DELETE FROM sales_invoice_items WHERE invoice_id IN (SELECT id FROM sales_invoices WHERE tenant_id = 'evlogia');" 2>/dev/null
$PSQL -c "DELETE FROM sales_invoice_payments WHERE invoice_id IN (SELECT id FROM sales_invoices WHERE tenant_id = 'evlogia');" 2>/dev/null

# Bill related
$PSQL -c "DELETE FROM bill_items WHERE bill_id IN (SELECT id FROM bills WHERE tenant_id = 'evlogia');" 2>/dev/null
$PSQL -c "DELETE FROM bill_attachments WHERE bill_id IN (SELECT id FROM bills WHERE tenant_id = 'evlogia');" 2>/dev/null
$PSQL -c "DELETE FROM bill_payment_allocations WHERE bill_id IN (SELECT id FROM bills WHERE tenant_id = 'evlogia');" 2>/dev/null

# Expense related
$PSQL -c "DELETE FROM expense_items WHERE expense_id IN (SELECT id FROM expenses WHERE tenant_id = 'evlogia');" 2>/dev/null
$PSQL -c "DELETE FROM expense_attachments WHERE expense_id IN (SELECT id FROM expenses WHERE tenant_id = 'evlogia');" 2>/dev/null

# Journal related (correct column names)
$PSQL -c "DELETE FROM journal_lines WHERE journal_id IN (SELECT id FROM journal_entries WHERE tenant_id = 'evlogia');" 2>/dev/null
$PSQL -c "DELETE FROM jurnal_detail WHERE jurnal_entry_id IN (SELECT id FROM jurnal_entry WHERE tenant_id = 'evlogia');" 2>/dev/null

# Quote related
$PSQL -c "DELETE FROM quote_items WHERE quote_id IN (SELECT id FROM quotes WHERE tenant_id = 'evlogia');" 2>/dev/null

# Sales Order related
$PSQL -c "DELETE FROM sales_order_items WHERE order_id IN (SELECT id FROM sales_orders WHERE tenant_id = 'evlogia');" 2>/dev/null
$PSQL -c "DELETE FROM sales_order_shipment_items WHERE shipment_id IN (SELECT id FROM sales_order_shipments WHERE tenant_id = 'evlogia');" 2>/dev/null

# Purchase Order related (correct column: po_id)
$PSQL -c "DELETE FROM purchase_order_items WHERE po_id IN (SELECT id FROM purchase_orders WHERE tenant_id = 'evlogia');" 2>/dev/null

# Credit Note related
$PSQL -c "DELETE FROM credit_note_items WHERE credit_note_id IN (SELECT id FROM credit_notes WHERE tenant_id = 'evlogia');" 2>/dev/null

# Vendor Credit related
$PSQL -c "DELETE FROM vendor_credit_items WHERE vendor_credit_id IN (SELECT id FROM vendor_credits WHERE tenant_id = 'evlogia');" 2>/dev/null

# Bank related
$PSQL -c "DELETE FROM bank_reconciliation_items WHERE reconciliation_id IN (SELECT id FROM bank_reconciliations WHERE tenant_id = 'evlogia');" 2>/dev/null
$PSQL -c "DELETE FROM bank_statement_lines WHERE import_id IN (SELECT id FROM bank_statement_imports WHERE tenant_id = 'evlogia');" 2>/dev/null

# Stock related
$PSQL -c "DELETE FROM stock_adjustment_items WHERE adjustment_id IN (SELECT id FROM stock_adjustments WHERE tenant_id = 'evlogia');" 2>/dev/null
$PSQL -c "DELETE FROM stock_transfer_items WHERE transfer_id IN (SELECT id FROM stock_transfers WHERE tenant_id = 'evlogia');" 2>/dev/null

# Sales Receipt
$PSQL -c "DELETE FROM sales_receipt_items WHERE receipt_id IN (SELECT id FROM sales_receipts WHERE tenant_id = 'evlogia');" 2>/dev/null

# Cheque
$PSQL -c "DELETE FROM cheque_status_history WHERE cheque_id IN (SELECT id FROM cheques WHERE tenant_id = 'evlogia');" 2>/dev/null

# Fixed assets
$PSQL -c "DELETE FROM asset_depreciations WHERE asset_id IN (SELECT id FROM fixed_assets WHERE tenant_id = 'evlogia');" 2>/dev/null
$PSQL -c "DELETE FROM asset_maintenance WHERE asset_id IN (SELECT id FROM fixed_assets WHERE tenant_id = 'evlogia');" 2>/dev/null

# Production
$PSQL -c "DELETE FROM production_order_materials WHERE production_order_id IN (SELECT id FROM production_orders WHERE tenant_id = 'evlogia');" 2>/dev/null
$PSQL -c "DELETE FROM production_order_labor WHERE production_order_id IN (SELECT id FROM production_orders WHERE tenant_id = 'evlogia');" 2>/dev/null
$PSQL -c "DELETE FROM production_completions WHERE production_order_id IN (SELECT id FROM production_orders WHERE tenant_id = 'evlogia');" 2>/dev/null

# BOM
$PSQL -c "DELETE FROM bom_components WHERE bom_id IN (SELECT id FROM bill_of_materials WHERE tenant_id = 'evlogia');" 2>/dev/null
$PSQL -c "DELETE FROM bom_operations WHERE bom_id IN (SELECT id FROM bill_of_materials WHERE tenant_id = 'evlogia');" 2>/dev/null
$PSQL -c "DELETE FROM bom_substitutes WHERE bom_id IN (SELECT id FROM bill_of_materials WHERE tenant_id = 'evlogia');" 2>/dev/null

# Recipe
$PSQL -c "DELETE FROM recipe_ingredients WHERE recipe_id IN (SELECT id FROM recipes WHERE tenant_id = 'evlogia');" 2>/dev/null
$PSQL -c "DELETE FROM recipe_instructions WHERE recipe_id IN (SELECT id FROM recipes WHERE tenant_id = 'evlogia');" 2>/dev/null
$PSQL -c "DELETE FROM recipe_modifiers WHERE recipe_id IN (SELECT id FROM recipes WHERE tenant_id = 'evlogia');" 2>/dev/null

# Budget
$PSQL -c "DELETE FROM budget_items WHERE budget_id IN (SELECT id FROM budgets WHERE tenant_id = 'evlogia');" 2>/dev/null
$PSQL -c "DELETE FROM budget_revisions WHERE budget_id IN (SELECT id FROM budgets WHERE tenant_id = 'evlogia');" 2>/dev/null

# Recurring
$PSQL -c "DELETE FROM recurring_invoice_items WHERE invoice_id IN (SELECT id FROM recurring_invoices WHERE tenant_id = 'evlogia');" 2>/dev/null
$PSQL -c "DELETE FROM recurring_bill_items WHERE bill_id IN (SELECT id FROM recurring_bills WHERE tenant_id = 'evlogia');" 2>/dev/null

# Approval
$PSQL -c "DELETE FROM approval_actions WHERE request_id IN (SELECT id FROM approval_requests WHERE tenant_id = 'evlogia');" 2>/dev/null
$PSQL -c "DELETE FROM approval_levels WHERE workflow_id IN (SELECT id FROM approval_workflows WHERE tenant_id = 'evlogia');" 2>/dev/null

# Vendor/Customer related
$PSQL -c "DELETE FROM vendor_addresses WHERE vendor_id IN (SELECT id FROM vendors WHERE tenant_id = 'evlogia');" 2>/dev/null
$PSQL -c "DELETE FROM vendor_contacts WHERE vendor_id IN (SELECT id FROM vendors WHERE tenant_id = 'evlogia');" 2>/dev/null

# KDS
$PSQL -c "DELETE FROM kds_order_items WHERE order_id IN (SELECT id FROM kds_orders WHERE tenant_id = 'evlogia');" 2>/dev/null

# Restaurant
$PSQL -c "DELETE FROM table_sessions WHERE table_id IN (SELECT id FROM restaurant_tables WHERE tenant_id = 'evlogia');" 2>/dev/null
$PSQL -c "DELETE FROM table_waitlist WHERE table_id IN (SELECT id FROM restaurant_tables WHERE tenant_id = 'evlogia');" 2>/dev/null

# Documents
$PSQL -c "DELETE FROM document_attachments WHERE document_id IN (SELECT id FROM documents WHERE tenant_id = 'evlogia');" 2>/dev/null

# Products related
$PSQL -c "DELETE FROM item_inventory WHERE product_id IN (SELECT id FROM products WHERE tenant_id = 'evlogia');" 2>/dev/null
$PSQL -c "DELETE FROM item_pricing WHERE product_id IN (SELECT id FROM products WHERE tenant_id = 'evlogia');" 2>/dev/null
$PSQL -c "DELETE FROM item_batches WHERE product_id IN (SELECT id FROM products WHERE tenant_id = 'evlogia');" 2>/dev/null
$PSQL -c "DELETE FROM item_serials WHERE product_id IN (SELECT id FROM products WHERE tenant_id = 'evlogia');" 2>/dev/null
$PSQL -c "DELETE FROM unit_conversions WHERE product_id IN (SELECT id FROM products WHERE tenant_id = 'evlogia');" 2>/dev/null

echo "Phase 2: Deleting tenant_id tables..."

# All tables with tenant_id - will use direct delete
TABLES_WITH_TENANT_ID=(
    receive_payment_allocations
    ar_payment_applications
    accounts_receivable
    ap_payment_applications
    accounts_payable
    credit_note_applications
    credit_note_refunds
    vendor_credit_applications
    vendor_credit_refunds
    customer_deposit_applications
    customer_deposit_refunds
    vendor_deposit_applications
    vendor_deposit_refunds
    bank_statement_lines_v2
    warehouse_stock
    batch_warehouse_stock
    bin_stock
    serial_movements
    kartu_stok
    inventory_ledger
    item_activities
    customer_activities
    vendor_activities
    kds_item_history
    kds_alerts
    intercompany_balances
    intercompany_settlements
    reconciliation_matches
    reconciliation_adjustments
    table_reservations
    sales_invoices
    bills
    expenses
    journal_entries
    jurnal_entry
    quotes
    sales_order_shipments
    sales_orders
    purchase_orders
    receive_payments
    bill_payments
    bill_payments_v2
    credit_notes
    vendor_credits
    customer_deposits
    vendor_deposits
    bank_reconciliations
    bank_statement_imports
    bank_transactions
    bank_transfers
    stock_adjustments
    stock_transfers
    fixed_assets
    production_orders
    sales_receipts
    cheques
    intercompany_transactions
    kds_orders
    reconciliation_sessions
    approval_requests
    approval_delegates
    recurring_invoices
    recurring_bills
    budgets
    forex_gain_loss
    opening_balance_records
    bukti_potong
    transaksi_harian
    item_transaksi
    documents
    products
    persediaan
    menu_items
    menu_categories
    recipes
    bill_of_materials
    customers
    vendors
    suppliers
    bank_accounts
    warehouse_bins
    warehouses
    asset_categories
    cost_centers
    cost_pools
    cost_variances
    overhead_allocations
    standard_costs
    work_centers
    bagan_akun
    chart_of_accounts
    kds_stations
    restaurant_tables
    table_areas
    approval_workflows
    branches
    sales_invoice_sequences
    bill_number_sequences
    expense_sequences
    journal_number_sequences
    quote_sequences
    sales_order_sequences
    purchase_order_sequences
    receive_payment_sequences
    bill_payment_sequences
    credit_note_sequences
    vendor_credit_sequences
    customer_deposit_sequences
    vendor_deposit_sequences
    bank_reconciliation_sequences
    bank_transfer_sequences
    stock_adjustment_sequences
    stock_transfer_sequences
    fixed_asset_sequences
    production_sequences
    sales_receipt_sequences
    cheque_sequences
    intercompany_sequences
    reservation_sequences
    shipment_sequences
    efaktur_sequences
    item_code_sequences
    branch_sequences
    fiscal_periods
    fiscal_years
    account_balances_daily
    trial_balance_snapshots
    aging_snapshots
    aging_brackets
    report_balance_cache
    ratio_snapshots
    ratio_alerts
    accounting_settings
    tenant_config
    tenant_rules
    tax_info
    currencies
    exchange_rates
    ratio_definitions
    industry_benchmarks
    faqs
    audit_logs
    accounting_outbox
    master_data_audit_log
    outbox
    sensitive_data_access
    chat_messages
    refresh_tokens
    user_devices
)

for table in "${TABLES_WITH_TENANT_ID[@]}"; do
    $PSQL -c "DELETE FROM $table WHERE tenant_id = 'evlogia';" 2>/dev/null
done

# Re-enable FK checks
$PSQL -c "SET session_replication_role = 'origin';"

echo "========================================"
echo "RESET COMPLETE"
echo "========================================"

# Verify
$PSQL -c "SELECT 'sales_invoices' as table_name, COUNT(*) as count FROM sales_invoices WHERE tenant_id = 'evlogia'
UNION ALL SELECT 'bills', COUNT(*) FROM bills WHERE tenant_id = 'evlogia'
UNION ALL SELECT 'expenses', COUNT(*) FROM expenses WHERE tenant_id = 'evlogia'
UNION ALL SELECT 'journal_entries', COUNT(*) FROM journal_entries WHERE tenant_id = 'evlogia'
UNION ALL SELECT 'customers', COUNT(*) FROM customers WHERE tenant_id = 'evlogia'
UNION ALL SELECT 'vendors', COUNT(*) FROM vendors WHERE tenant_id = 'evlogia'
UNION ALL SELECT 'products', COUNT(*) FROM products WHERE tenant_id = 'evlogia'
UNION ALL SELECT 'chart_of_accounts', COUNT(*) FROM chart_of_accounts WHERE tenant_id = 'evlogia';"
