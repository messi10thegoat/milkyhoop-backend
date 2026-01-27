-- =============================================
-- EVLOGIA SEED: 99_verify.sql
-- Purpose: Verification queries and data assertions
-- Run this after all seed scripts to validate data integrity
-- =============================================

\echo '========================================'
\echo 'EVLOGIA SEED DATA VERIFICATION'
\echo '========================================'

-- ==========================================
-- 1. MASTER DATA COUNTS
-- ==========================================
\echo ''
\echo '--- MASTER DATA COUNTS ---'

SELECT 'warehouses' as entity, COUNT(*) as count FROM warehouses WHERE tenant_id = 'evlogia'
UNION ALL SELECT 'vendors', COUNT(*) FROM vendors WHERE tenant_id = 'evlogia'
UNION ALL SELECT 'customers', COUNT(*) FROM customers WHERE tenant_id = 'evlogia'
UNION ALL SELECT 'products', COUNT(*) FROM products WHERE tenant_id = 'evlogia'
UNION ALL SELECT 'unit_conversions', COUNT(*) FROM unit_conversions WHERE tenant_id = 'evlogia'
UNION ALL SELECT 'bom_headers', COUNT(*) FROM bom_headers WHERE tenant_id = 'evlogia'
ORDER BY entity;

-- ==========================================
-- 2. TRANSACTION COUNTS BY TYPE
-- ==========================================
\echo ''
\echo '--- TRANSACTION COUNTS ---'

SELECT 'purchase_orders' as doc_type, COUNT(*) as count FROM purchase_orders WHERE tenant_id = 'evlogia'
UNION ALL SELECT 'bills', COUNT(*) FROM bills WHERE tenant_id = 'evlogia'
UNION ALL SELECT 'production_orders', COUNT(*) FROM production_orders WHERE tenant_id = 'evlogia'
UNION ALL SELECT 'quotes', COUNT(*) FROM quotes WHERE tenant_id = 'evlogia'
UNION ALL SELECT 'sales_orders', COUNT(*) FROM sales_orders WHERE tenant_id = 'evlogia'
UNION ALL SELECT 'sales_invoices', COUNT(*) FROM sales_invoices WHERE tenant_id = 'evlogia'
UNION ALL SELECT 'sales_receipts', COUNT(*) FROM sales_receipts WHERE tenant_id = 'evlogia'
UNION ALL SELECT 'stock_adjustments', COUNT(*) FROM stock_adjustments WHERE tenant_id = 'evlogia'
UNION ALL SELECT 'stock_transfers', COUNT(*) FROM stock_transfers WHERE tenant_id = 'evlogia'
UNION ALL SELECT 'credit_notes', COUNT(*) FROM credit_notes WHERE tenant_id = 'evlogia'
UNION ALL SELECT 'vendor_credits', COUNT(*) FROM vendor_credits WHERE tenant_id = 'evlogia'
UNION ALL SELECT 'customer_deposits', COUNT(*) FROM customer_deposits WHERE tenant_id = 'evlogia'
UNION ALL SELECT 'payment_receipts', COUNT(*) FROM payment_receipts WHERE tenant_id = 'evlogia'
UNION ALL SELECT 'bill_payments', COUNT(*) FROM bill_payments WHERE tenant_id = 'evlogia'
ORDER BY doc_type;

-- ==========================================
-- 3. JOURNAL ENTRIES
-- ==========================================
\echo ''
\echo '--- JOURNAL ENTRIES ---'

SELECT 'journal_entries' as entity, COUNT(*) as count FROM journal_entries WHERE tenant_id = 'evlogia'
UNION ALL SELECT 'journal_lines', COUNT(*) FROM journal_lines jl
    JOIN journal_entries je ON jl.journal_id = je.id
    WHERE je.tenant_id = 'evlogia';

-- Journal entries by source type
\echo ''
\echo '--- JOURNAL ENTRIES BY SOURCE ---'

SELECT source_type, status, COUNT(*) as count
FROM journal_entries
WHERE tenant_id = 'evlogia'
GROUP BY source_type, status
ORDER BY source_type, status;

-- ==========================================
-- 4. TRIAL BALANCE CHECK
-- ==========================================
\echo ''
\echo '--- TRIAL BALANCE CHECK ---'

SELECT
    SUM(jl.debit) as total_debit,
    SUM(jl.credit) as total_credit,
    SUM(jl.debit) - SUM(jl.credit) as difference,
    CASE WHEN SUM(jl.debit) - SUM(jl.credit) = 0 THEN 'BALANCED ✓' ELSE 'UNBALANCED ✗' END as status
FROM journal_lines jl
JOIN journal_entries je ON jl.journal_id = je.id
WHERE je.tenant_id = 'evlogia' AND je.status = 'POSTED';

-- ==========================================
-- 5. STATUS DISTRIBUTIONS
-- ==========================================
\echo ''
\echo '--- PURCHASE ORDER STATUS ---'
SELECT status, COUNT(*) as count FROM purchase_orders WHERE tenant_id = 'evlogia' GROUP BY status ORDER BY status;

\echo ''
\echo '--- BILL STATUS ---'
SELECT status, COUNT(*) as count FROM bills WHERE tenant_id = 'evlogia' GROUP BY status ORDER BY status;

\echo ''
\echo '--- QUOTE STATUS ---'
SELECT status, COUNT(*) as count FROM quotes WHERE tenant_id = 'evlogia' GROUP BY status ORDER BY status;

\echo ''
\echo '--- SALES ORDER STATUS ---'
SELECT status, COUNT(*) as count FROM sales_orders WHERE tenant_id = 'evlogia' GROUP BY status ORDER BY status;

\echo ''
\echo '--- SALES INVOICE STATUS ---'
SELECT status, COUNT(*) as count FROM sales_invoices WHERE tenant_id = 'evlogia' GROUP BY status ORDER BY status;

\echo ''
\echo '--- PRODUCTION ORDER STATUS ---'
SELECT status, COUNT(*) as count FROM production_orders WHERE tenant_id = 'evlogia' GROUP BY status ORDER BY status;

-- ==========================================
-- 6. SUBLEDGER RECONCILIATION
-- ==========================================
\echo ''
\echo '--- AR SUBLEDGER vs GL ---'

SELECT
    (SELECT COALESCE(SUM(total_amount - amount_paid), 0)
     FROM sales_invoices
     WHERE tenant_id = 'evlogia' AND status NOT IN ('draft', 'void')) as ar_subledger,
    (SELECT COALESCE(SUM(jl.debit) - SUM(jl.credit), 0)
     FROM journal_lines jl
     JOIN journal_entries je ON jl.journal_id = je.id
     JOIN chart_of_accounts coa ON jl.account_id = coa.id
     WHERE je.tenant_id = 'evlogia'
     AND coa.account_code = '1-10300'
     AND je.status = 'POSTED') as gl_ar;

\echo ''
\echo '--- AP SUBLEDGER vs GL ---'

SELECT
    (SELECT COALESCE(SUM(amount - amount_paid), 0)
     FROM bills
     WHERE tenant_id = 'evlogia' AND status NOT IN ('draft', 'void')) as ap_subledger,
    (SELECT COALESCE(SUM(jl.credit) - SUM(jl.debit), 0)
     FROM journal_lines jl
     JOIN journal_entries je ON jl.journal_id = je.id
     JOIN chart_of_accounts coa ON jl.account_id = coa.id
     WHERE je.tenant_id = 'evlogia'
     AND coa.account_code = '2-10100'
     AND je.status = 'POSTED') as gl_ap;

-- ==========================================
-- 7. DATE RANGE CHECK
-- ==========================================
\echo ''
\echo '--- TRANSACTION DATE RANGES ---'

SELECT
    'purchase_orders' as doc_type,
    MIN(po_date) as min_date,
    MAX(po_date) as max_date
FROM purchase_orders WHERE tenant_id = 'evlogia'
UNION ALL
SELECT 'bills', MIN(bill_date), MAX(bill_date)
FROM bills WHERE tenant_id = 'evlogia'
UNION ALL
SELECT 'sales_invoices', MIN(invoice_date), MAX(invoice_date)
FROM sales_invoices WHERE tenant_id = 'evlogia'
UNION ALL
SELECT 'sales_receipts', MIN(receipt_date), MAX(receipt_date)
FROM sales_receipts WHERE tenant_id = 'evlogia'
ORDER BY doc_type;

-- ==========================================
-- 8. PRODUCT CATEGORIES
-- ==========================================
\echo ''
\echo '--- PRODUCT CATEGORIES ---'

SELECT
    CASE
        WHEN kode_produk LIKE 'KTN-%' OR kode_produk LIKE 'LNN-%' OR kode_produk LIKE 'DNM-%'
             OR kode_produk LIKE 'BTK-%' OR kode_produk LIKE 'TWL-%' OR kode_produk LIKE 'FLC-%' THEN 'Bahan Kain'
        WHEN kode_produk LIKE 'BNG-%' THEN 'Bahan Benang'
        WHEN kode_produk LIKE 'KNC-%' OR kode_produk LIKE 'RSL-%' OR kode_produk LIKE 'LBL-%' OR kode_produk LIKE 'PKG-%' THEN 'Aksesoris'
        WHEN kode_produk LIKE 'IMP-%' THEN 'FG Trading'
        WHEN kode_produk LIKE 'EVL-%' THEN 'FG Produksi'
        WHEN kode_produk LIKE 'SVC-%' THEN 'Services'
        ELSE 'Other'
    END as category,
    COUNT(*) as count
FROM products
WHERE tenant_id = 'evlogia'
GROUP BY category
ORDER BY category;

-- ==========================================
-- 9. SUMMARY TOTALS
-- ==========================================
\echo ''
\echo '--- SUMMARY TOTALS ---'

SELECT
    'Total PO Value' as metric,
    SUM(total_amount) as amount
FROM purchase_orders WHERE tenant_id = 'evlogia' AND status != 'cancelled'
UNION ALL
SELECT 'Total Bill Value', SUM(amount)
FROM bills WHERE tenant_id = 'evlogia' AND status NOT IN ('draft', 'void')
UNION ALL
SELECT 'Total Invoice Value', SUM(total_amount)
FROM sales_invoices WHERE tenant_id = 'evlogia' AND status NOT IN ('draft', 'void')
UNION ALL
SELECT 'Total POS Value', SUM(total_amount)
FROM sales_receipts WHERE tenant_id = 'evlogia' AND status = 'completed'
UNION ALL
SELECT 'Total AR Payments', SUM(amount)
FROM payment_receipts WHERE tenant_id = 'evlogia' AND status = 'posted'
UNION ALL
SELECT 'Total AP Payments', SUM(amount)
FROM bill_payments WHERE tenant_id = 'evlogia' AND status = 'posted';

\echo ''
\echo '========================================'
\echo 'VERIFICATION COMPLETE'
\echo '========================================'
