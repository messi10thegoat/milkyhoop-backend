-- =============================================
-- EVLOGIA SEED: run_all.sql
-- Purpose: Master script to run all seed scripts in order
-- Usage: \i /path/to/seed/run_all.sql
-- =============================================

\echo '========================================'
\echo 'EVLOGIA SEED DATA - MASTER SCRIPT'
\echo 'Fashion & Textile Business'
\echo 'Timeline: Nov 2025 - Jan 2026'
\echo '========================================'
\echo ''

-- Set timing on for performance monitoring
\timing on

-- ==========================================
-- PHASE 1: CLEANUP & SETUP
-- ==========================================
\echo '--- PHASE 1: CLEANUP & SETUP ---'
\ir 00_cleanup.sql
\ir 01_set_tenant.sql

-- ==========================================
-- PHASE 2: MASTER DATA
-- ==========================================
\echo ''
\echo '--- PHASE 2: MASTER DATA ---'
\ir 02_warehouses.sql
\ir 03_vendors.sql
\ir 04_customers.sql

-- ==========================================
-- PHASE 3: PRODUCTS & MANUFACTURING SETUP
-- ==========================================
\echo ''
\echo '--- PHASE 3: PRODUCTS & MANUFACTURING ---'
\ir 05_products.sql
\ir 06_unit_conversions.sql
\ir 07_bom.sql

-- ==========================================
-- PHASE 4: OPENING BALANCE
-- ==========================================
\echo ''
\echo '--- PHASE 4: OPENING BALANCE ---'
\ir 08_opening_balance.sql

-- ==========================================
-- PHASE 5: PURCHASING CYCLE
-- ==========================================
\echo ''
\echo '--- PHASE 5: PURCHASING CYCLE ---'
\ir 09_purchase_orders.sql
\ir 10_bills.sql

-- ==========================================
-- PHASE 6: MANUFACTURING
-- ==========================================
\echo ''
\echo '--- PHASE 6: MANUFACTURING ---'
\ir 11_production_orders.sql

-- ==========================================
-- PHASE 7: SALES CYCLE
-- ==========================================
\echo ''
\echo '--- PHASE 7: SALES CYCLE ---'
\ir 12_quotes.sql
\ir 13_sales_orders.sql
\ir 14_sales_invoices.sql
\ir 15_sales_receipts.sql

-- ==========================================
-- PHASE 8: INVENTORY OPERATIONS
-- ==========================================
\echo ''
\echo '--- PHASE 8: INVENTORY OPERATIONS ---'
\ir 16_stock_adjustments.sql
\ir 17_stock_transfers.sql

-- ==========================================
-- PHASE 9: CREDITS & RETURNS
-- ==========================================
\echo ''
\echo '--- PHASE 9: CREDITS & RETURNS ---'
\ir 18_credit_notes.sql
\ir 19_vendor_credits.sql
\ir 20_customer_deposits.sql

-- ==========================================
-- PHASE 10: BANKING & PAYMENTS
-- ==========================================
\echo ''
\echo '--- PHASE 10: BANKING & PAYMENTS ---'
\ir 21_payment_receipts.sql
\ir 22_bill_payments.sql

-- ==========================================
-- PHASE 11: VERIFICATION
-- ==========================================
\echo ''
\echo '--- PHASE 11: VERIFICATION ---'
\ir 99_verify.sql

\timing off

\echo ''
\echo '========================================'
\echo 'ALL SEED SCRIPTS COMPLETED SUCCESSFULLY!'
\echo '========================================'
\echo ''
\echo 'Data seeded for tenant: evlogia'
\echo 'Business: Fashion & Textile (Evlogia)'
\echo 'Timeline: November 2025 - January 2026'
\echo ''
\echo 'Expected counts (approximate):'
\echo '  - Products: 50'
\echo '  - Vendors: 15'
\echo '  - Customers: 25'
\echo '  - Purchase Orders: 100+'
\echo '  - Bills: 120+'
\echo '  - Production Orders: 50+'
\echo '  - Quotes: 50+'
\echo '  - Sales Orders: 80+'
\echo '  - Sales Invoices: 150+'
\echo '  - Sales Receipts (POS): 200+'
\echo '  - Stock Adjustments: 40+'
\echo '  - Stock Transfers: 20+'
\echo '  - Journal Entries: 500+'
\echo ''
\echo 'Run 99_verify.sql for detailed verification.'
\echo '========================================'
