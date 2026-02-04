#!/bin/bash
# =====================================================
# RESET TENANT - Keep Only Chart of Accounts
# Robust version with proper column names and error handling
# Usage: ./reset_tenant_clean.sh <tenant_id>
# =====================================================

set -e

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
echo ""

# Confirm before proceeding
read -p "Are you sure you want to reset tenant '$TENANT_ID'? (yes/no): " CONFIRM
if [ "$CONFIRM" != "yes" ]; then
    echo "Aborted."
    exit 0
fi

echo ""
echo "Starting reset..."

docker exec milkyhoop-dev-postgres-1 psql -U postgres -d milkydb <<EOF
-- Disable FK constraints
SET session_replication_role = 'replica';

-- =====================================================
-- PHASE 1: Delete transaction line items
-- =====================================================
DELETE FROM journal_lines WHERE journal_id IN (SELECT id FROM journal_entries WHERE tenant_id = '$TENANT_ID');
DELETE FROM sales_invoice_items WHERE invoice_id IN (SELECT id FROM sales_invoices WHERE tenant_id = '$TENANT_ID');
DELETE FROM sales_invoice_payments WHERE invoice_id IN (SELECT id FROM sales_invoices WHERE tenant_id = '$TENANT_ID');
DELETE FROM bill_items WHERE bill_id IN (SELECT id FROM bills WHERE tenant_id = '$TENANT_ID');
DELETE FROM bill_attachments WHERE bill_id IN (SELECT id FROM bills WHERE tenant_id = '$TENANT_ID');
DELETE FROM bill_payment_allocations WHERE bill_id IN (SELECT id FROM bills WHERE tenant_id = '$TENANT_ID');
DELETE FROM quote_items WHERE quote_id IN (SELECT id FROM quotes WHERE tenant_id = '$TENANT_ID');
DELETE FROM sales_order_items WHERE sales_order_id IN (SELECT id FROM sales_orders WHERE tenant_id = '$TENANT_ID');
DELETE FROM purchase_order_items WHERE po_id IN (SELECT id FROM purchase_orders WHERE tenant_id = '$TENANT_ID');
DELETE FROM credit_note_items WHERE credit_note_id IN (SELECT id FROM credit_notes WHERE tenant_id = '$TENANT_ID');
DELETE FROM vendor_credit_items WHERE vendor_credit_id IN (SELECT id FROM vendor_credits WHERE tenant_id = '$TENANT_ID');
DELETE FROM sales_receipt_items WHERE receipt_id IN (SELECT id FROM sales_receipts WHERE tenant_id = '$TENANT_ID');
DELETE FROM expense_items WHERE expense_id IN (SELECT id FROM expenses WHERE tenant_id = '$TENANT_ID');
DELETE FROM expense_attachments WHERE expense_id IN (SELECT id FROM expenses WHERE tenant_id = '$TENANT_ID');

-- =====================================================
-- PHASE 2: Delete AR/AP related
-- =====================================================
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

-- =====================================================
-- PHASE 3: Delete inventory related
-- =====================================================
DELETE FROM warehouse_stock WHERE tenant_id = '$TENANT_ID';
DELETE FROM kartu_stok WHERE tenant_id = '$TENANT_ID';
DELETE FROM inventory_ledger WHERE tenant_id = '$TENANT_ID';
DELETE FROM item_activities WHERE tenant_id = '$TENANT_ID';
DELETE FROM persediaan WHERE tenant_id = '$TENANT_ID';
DELETE FROM customer_activities WHERE tenant_id = '$TENANT_ID';
DELETE FROM vendor_activities WHERE tenant_id = '$TENANT_ID';

-- =====================================================
-- PHASE 4: Delete transaction headers
-- =====================================================
DELETE FROM journal_entries WHERE tenant_id = '$TENANT_ID';
DELETE FROM jurnal_entry WHERE tenant_id = '$TENANT_ID';
DELETE FROM sales_invoices WHERE tenant_id = '$TENANT_ID';
DELETE FROM bills WHERE tenant_id = '$TENANT_ID';
DELETE FROM expenses WHERE tenant_id = '$TENANT_ID';
DELETE FROM quotes WHERE tenant_id = '$TENANT_ID';
DELETE FROM sales_orders WHERE tenant_id = '$TENANT_ID';
DELETE FROM purchase_orders WHERE tenant_id = '$TENANT_ID';
DELETE FROM receive_payments WHERE tenant_id = '$TENANT_ID';
DELETE FROM bill_payments WHERE tenant_id = '$TENANT_ID';
DELETE FROM bill_payments_v2 WHERE tenant_id = '$TENANT_ID';
DELETE FROM credit_notes WHERE tenant_id = '$TENANT_ID';
DELETE FROM vendor_credits WHERE tenant_id = '$TENANT_ID';
DELETE FROM customer_deposits WHERE tenant_id = '$TENANT_ID';
DELETE FROM vendor_deposits WHERE tenant_id = '$TENANT_ID';
DELETE FROM bank_transactions WHERE tenant_id = '$TENANT_ID';
DELETE FROM bank_transfers WHERE tenant_id = '$TENANT_ID';
DELETE FROM stock_adjustments WHERE tenant_id = '$TENANT_ID';
DELETE FROM stock_transfers WHERE tenant_id = '$TENANT_ID';
DELETE FROM sales_receipts WHERE tenant_id = '$TENANT_ID';

-- =====================================================
-- PHASE 5: Delete MASTER DATA (except CoA)
-- =====================================================
DELETE FROM products WHERE tenant_id = '$TENANT_ID';
DELETE FROM customers WHERE tenant_id = '$TENANT_ID';
DELETE FROM vendors WHERE tenant_id = '$TENANT_ID';
DELETE FROM suppliers WHERE tenant_id = '$TENANT_ID';
DELETE FROM bank_accounts WHERE tenant_id = '$TENANT_ID';
DELETE FROM warehouses WHERE tenant_id = '$TENANT_ID';

-- =====================================================
-- PHASE 6: Delete sequences and caches
-- =====================================================
DELETE FROM account_balances_daily WHERE tenant_id = '$TENANT_ID';
DELETE FROM trial_balance_snapshots WHERE tenant_id = '$TENANT_ID';
DELETE FROM report_balance_cache WHERE tenant_id = '$TENANT_ID';
DELETE FROM sales_invoice_sequences WHERE tenant_id = '$TENANT_ID';
DELETE FROM bill_number_sequences WHERE tenant_id = '$TENANT_ID';
DELETE FROM journal_number_sequences WHERE tenant_id = '$TENANT_ID';
DELETE FROM quote_sequences WHERE tenant_id = '$TENANT_ID';
DELETE FROM sales_order_sequences WHERE tenant_id = '$TENANT_ID';
DELETE FROM purchase_order_sequences WHERE tenant_id = '$TENANT_ID';
DELETE FROM receive_payment_sequences WHERE tenant_id = '$TENANT_ID';
DELETE FROM bill_payment_sequences WHERE tenant_id = '$TENANT_ID';
DELETE FROM item_code_sequences WHERE tenant_id = '$TENANT_ID';
DELETE FROM fiscal_periods WHERE tenant_id = '$TENANT_ID';
DELETE FROM fiscal_years WHERE tenant_id = '$TENANT_ID';

-- Re-enable FK constraints
SET session_replication_role = 'origin';
EOF

echo ""
echo "========== RESET COMPLETE =========="
echo ""

# Show summary
docker exec milkyhoop-dev-postgres-1 psql -U postgres -d milkydb -c "
SELECT
    CASE
        WHEN table_name = 'chart_of_accounts' THEN '✓ ' || table_name || ' (KEPT)'
        ELSE '✗ ' || table_name || ' (DELETED)'
    END as status,
    count
FROM (
    SELECT 'chart_of_accounts' as table_name, COUNT(*) as count FROM chart_of_accounts WHERE tenant_id = '$TENANT_ID'
    UNION ALL SELECT 'customers', COUNT(*) FROM customers WHERE tenant_id = '$TENANT_ID'
    UNION ALL SELECT 'vendors', COUNT(*) FROM vendors WHERE tenant_id = '$TENANT_ID'
    UNION ALL SELECT 'products', COUNT(*) FROM products WHERE tenant_id = '$TENANT_ID'
    UNION ALL SELECT 'bank_accounts', COUNT(*) FROM bank_accounts WHERE tenant_id = '$TENANT_ID'
    UNION ALL SELECT 'sales_invoices', COUNT(*) FROM sales_invoices WHERE tenant_id = '$TENANT_ID'
    UNION ALL SELECT 'bills', COUNT(*) FROM bills WHERE tenant_id = '$TENANT_ID'
    UNION ALL SELECT 'journal_entries', COUNT(*) FROM journal_entries WHERE tenant_id = '$TENANT_ID'
) t
ORDER BY table_name = 'chart_of_accounts' DESC, table_name;
"

echo ""
echo "Tenant '$TENANT_ID' has been reset. Only Chart of Accounts remains."
