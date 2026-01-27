-- =============================================
-- EVLOGIA SEED: 00_cleanup.sql
-- Purpose: Clean existing seed data for Evlogia tenant
-- WARNING: This will DELETE transaction data, preserving tenant & users
-- =============================================

DO $$
DECLARE
    v_tenant_id TEXT := 'evlogia';
    v_count INTEGER;
BEGIN
    RAISE NOTICE '========================================';
    RAISE NOTICE 'CLEANUP: Starting for tenant %', v_tenant_id;
    RAISE NOTICE '========================================';

    -- Verify tenant exists
    IF NOT EXISTS (SELECT 1 FROM "Tenant" WHERE alias = v_tenant_id) THEN
        RAISE EXCEPTION 'Tenant % tidak ditemukan!', v_tenant_id;
    END IF;

    -- ==========================================
    -- DELETE in reverse dependency order
    -- ==========================================

    -- 1. Journal Lines & Entries (depends on everything)
    DELETE FROM journal_lines WHERE tenant_id = v_tenant_id;
    GET DIAGNOSTICS v_count = ROW_COUNT;
    RAISE NOTICE 'Deleted % journal_lines', v_count;

    DELETE FROM journal_entries WHERE tenant_id = v_tenant_id;
    GET DIAGNOSTICS v_count = ROW_COUNT;
    RAISE NOTICE 'Deleted % journal_entries', v_count;

    -- 2. Credit Notes & Applications
    DELETE FROM credit_note_refunds WHERE tenant_id = v_tenant_id;
    DELETE FROM credit_note_applications WHERE tenant_id = v_tenant_id;
    DELETE FROM credit_note_items WHERE credit_note_id IN (SELECT id FROM credit_notes WHERE tenant_id = v_tenant_id);
    DELETE FROM credit_notes WHERE tenant_id = v_tenant_id;
    GET DIAGNOSTICS v_count = ROW_COUNT;
    RAISE NOTICE 'Deleted % credit_notes', v_count;

    -- 3. Vendor Credits & Applications
    DELETE FROM vendor_credit_refunds WHERE tenant_id = v_tenant_id;
    DELETE FROM vendor_credit_applications WHERE tenant_id = v_tenant_id;
    DELETE FROM vendor_credit_items WHERE vendor_credit_id IN (SELECT id FROM vendor_credits WHERE tenant_id = v_tenant_id);
    DELETE FROM vendor_credits WHERE tenant_id = v_tenant_id;
    GET DIAGNOSTICS v_count = ROW_COUNT;
    RAISE NOTICE 'Deleted % vendor_credits', v_count;

    -- 4. Customer Deposits
    DELETE FROM customer_deposit_refunds WHERE tenant_id = v_tenant_id;
    DELETE FROM customer_deposit_applications WHERE tenant_id = v_tenant_id;
    DELETE FROM customer_deposits WHERE tenant_id = v_tenant_id;
    GET DIAGNOSTICS v_count = ROW_COUNT;
    RAISE NOTICE 'Deleted % customer_deposits', v_count;

    -- 5. Sales Receipts (POS)
    DELETE FROM sales_receipt_items WHERE sales_receipt_id IN (SELECT id FROM sales_receipts WHERE tenant_id = v_tenant_id);
    DELETE FROM sales_receipts WHERE tenant_id = v_tenant_id;
    GET DIAGNOSTICS v_count = ROW_COUNT;
    RAISE NOTICE 'Deleted % sales_receipts', v_count;

    -- 6. Payment Receipts
    DELETE FROM payment_receipt_allocations WHERE tenant_id = v_tenant_id;
    DELETE FROM payment_receipts WHERE tenant_id = v_tenant_id;
    GET DIAGNOSTICS v_count = ROW_COUNT;
    RAISE NOTICE 'Deleted % payment_receipts', v_count;

    -- 7. Sales Invoices
    DELETE FROM sales_invoice_payments WHERE invoice_id IN (SELECT id FROM sales_invoices WHERE tenant_id = v_tenant_id);
    DELETE FROM sales_invoice_items WHERE invoice_id IN (SELECT id FROM sales_invoices WHERE tenant_id = v_tenant_id);
    DELETE FROM sales_invoices WHERE tenant_id = v_tenant_id;
    GET DIAGNOSTICS v_count = ROW_COUNT;
    RAISE NOTICE 'Deleted % sales_invoices', v_count;

    -- 8. Sales Orders & Shipments
    DELETE FROM sales_order_shipment_items WHERE shipment_id IN (
        SELECT id FROM sales_order_shipments WHERE tenant_id = v_tenant_id
    );
    DELETE FROM sales_order_shipments WHERE tenant_id = v_tenant_id;
    DELETE FROM sales_order_items WHERE sales_order_id IN (SELECT id FROM sales_orders WHERE tenant_id = v_tenant_id);
    DELETE FROM sales_orders WHERE tenant_id = v_tenant_id;
    GET DIAGNOSTICS v_count = ROW_COUNT;
    RAISE NOTICE 'Deleted % sales_orders', v_count;

    -- 9. Quotes
    DELETE FROM quote_items WHERE quote_id IN (SELECT id FROM quotes WHERE tenant_id = v_tenant_id);
    DELETE FROM quotes WHERE tenant_id = v_tenant_id;
    GET DIAGNOSTICS v_count = ROW_COUNT;
    RAISE NOTICE 'Deleted % quotes', v_count;

    -- 10. Bill Payments
    DELETE FROM bill_payment_allocations WHERE tenant_id = v_tenant_id;
    DELETE FROM bill_payments WHERE tenant_id = v_tenant_id;
    GET DIAGNOSTICS v_count = ROW_COUNT;
    RAISE NOTICE 'Deleted % bill_payments', v_count;

    -- 11. Bills
    DELETE FROM bill_items WHERE bill_id IN (SELECT id FROM bills WHERE tenant_id = v_tenant_id);
    DELETE FROM bills WHERE tenant_id = v_tenant_id;
    GET DIAGNOSTICS v_count = ROW_COUNT;
    RAISE NOTICE 'Deleted % bills', v_count;

    -- 12. Purchase Orders
    DELETE FROM purchase_order_items WHERE purchase_order_id IN (SELECT id FROM purchase_orders WHERE tenant_id = v_tenant_id);
    DELETE FROM purchase_orders WHERE tenant_id = v_tenant_id;
    GET DIAGNOSTICS v_count = ROW_COUNT;
    RAISE NOTICE 'Deleted % purchase_orders', v_count;

    -- 13. Production Orders
    DELETE FROM production_completions WHERE production_order_id IN (SELECT id FROM production_orders WHERE tenant_id = v_tenant_id);
    DELETE FROM production_order_labor WHERE production_order_id IN (SELECT id FROM production_orders WHERE tenant_id = v_tenant_id);
    DELETE FROM production_order_materials WHERE production_order_id IN (SELECT id FROM production_orders WHERE tenant_id = v_tenant_id);
    DELETE FROM production_orders WHERE tenant_id = v_tenant_id;
    GET DIAGNOSTICS v_count = ROW_COUNT;
    RAISE NOTICE 'Deleted % production_orders', v_count;

    -- 14. Stock Transfers
    DELETE FROM stock_transfer_items WHERE stock_transfer_id IN (SELECT id FROM stock_transfers WHERE tenant_id = v_tenant_id);
    DELETE FROM stock_transfers WHERE tenant_id = v_tenant_id;
    GET DIAGNOSTICS v_count = ROW_COUNT;
    RAISE NOTICE 'Deleted % stock_transfers', v_count;

    -- 15. Stock Adjustments
    DELETE FROM stock_adjustment_items WHERE stock_adjustment_id IN (SELECT id FROM stock_adjustments WHERE tenant_id = v_tenant_id);
    DELETE FROM stock_adjustments WHERE tenant_id = v_tenant_id;
    GET DIAGNOSTICS v_count = ROW_COUNT;
    RAISE NOTICE 'Deleted % stock_adjustments', v_count;

    -- 16. Bank Transactions & Transfers
    DELETE FROM bank_transactions WHERE tenant_id = v_tenant_id;
    GET DIAGNOSTICS v_count = ROW_COUNT;
    RAISE NOTICE 'Deleted % bank_transactions', v_count;

    -- 17. Expenses
    DELETE FROM expense_items WHERE expense_id IN (SELECT id FROM expenses WHERE tenant_id = v_tenant_id);
    DELETE FROM expenses WHERE tenant_id = v_tenant_id;
    GET DIAGNOSTICS v_count = ROW_COUNT;
    RAISE NOTICE 'Deleted % expenses', v_count;

    -- 18. BOMs
    DELETE FROM bom_components WHERE bom_id IN (SELECT id FROM bill_of_materials WHERE tenant_id = v_tenant_id);
    DELETE FROM bill_of_materials WHERE tenant_id = v_tenant_id;
    GET DIAGNOSTICS v_count = ROW_COUNT;
    RAISE NOTICE 'Deleted % bill_of_materials', v_count;

    -- 19. Unit Conversions
    DELETE FROM unit_conversions WHERE tenant_id = v_tenant_id;
    GET DIAGNOSTICS v_count = ROW_COUNT;
    RAISE NOTICE 'Deleted % unit_conversions', v_count;

    -- 20. Persediaan (Stock Ledger)
    DELETE FROM persediaan WHERE tenant_id = v_tenant_id;
    GET DIAGNOSTICS v_count = ROW_COUNT;
    RAISE NOTICE 'Deleted % persediaan', v_count;

    -- 21. Products (Master Data - recreate fresh)
    DELETE FROM products WHERE tenant_id = v_tenant_id;
    GET DIAGNOSTICS v_count = ROW_COUNT;
    RAISE NOTICE 'Deleted % products', v_count;

    -- 22. Customers
    DELETE FROM customers WHERE tenant_id = v_tenant_id;
    GET DIAGNOSTICS v_count = ROW_COUNT;
    RAISE NOTICE 'Deleted % customers', v_count;

    -- 23. Vendors
    DELETE FROM vendors WHERE tenant_id = v_tenant_id;
    GET DIAGNOSTICS v_count = ROW_COUNT;
    RAISE NOTICE 'Deleted % vendors', v_count;

    -- 24. Warehouses
    DELETE FROM warehouses WHERE tenant_id = v_tenant_id;
    GET DIAGNOSTICS v_count = ROW_COUNT;
    RAISE NOTICE 'Deleted % warehouses', v_count;

    -- 25. Bank Accounts (keep system ones, delete seed ones)
    DELETE FROM bank_accounts WHERE tenant_id = v_tenant_id AND is_active = true;
    GET DIAGNOSTICS v_count = ROW_COUNT;
    RAISE NOTICE 'Deleted % bank_accounts', v_count;

    -- Reset sequences
    UPDATE quote_sequences SET last_number = 0 WHERE tenant_id = v_tenant_id;
    UPDATE sales_order_sequences SET last_number = 0 WHERE tenant_id = v_tenant_id;
    UPDATE sales_invoice_sequences SET last_number = 0 WHERE tenant_id = v_tenant_id;
    UPDATE sales_receipt_sequences SET last_number = 0 WHERE tenant_id = v_tenant_id;
    UPDATE credit_note_sequences SET last_number = 0 WHERE tenant_id = v_tenant_id;
    UPDATE purchase_order_sequences SET last_number = 0 WHERE tenant_id = v_tenant_id;
    UPDATE bill_sequences SET last_number = 0 WHERE tenant_id = v_tenant_id;
    UPDATE vendor_credit_sequences SET last_number = 0 WHERE tenant_id = v_tenant_id;
    UPDATE stock_adjustment_sequences SET last_number = 0 WHERE tenant_id = v_tenant_id;
    UPDATE stock_transfer_sequences SET last_number = 0 WHERE tenant_id = v_tenant_id;
    UPDATE customer_deposit_sequences SET last_number = 0 WHERE tenant_id = v_tenant_id;
    UPDATE journal_sequences SET last_number = 0 WHERE tenant_id = v_tenant_id;

    RAISE NOTICE '========================================';
    RAISE NOTICE 'CLEANUP: Completed for tenant %', v_tenant_id;
    RAISE NOTICE '========================================';
END $$;
