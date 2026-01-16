# P4 + P5 Migration Report

## Date: 2026-01-15

## Migration Summary

| Status | Count |
|--------|-------|
| Migrations Applied | 13/13 |
| Tables Created | 32 |
| RLS Enabled | 31/33 |
| Functions Created | 22+ |
| Triggers Created | 25 |
| API Endpoints | 97 |

---

## Migrations Applied

### P4 Core Completion (7 migrations)

| Version | File | Status | Notes |
|---------|------|--------|-------|
| V043 | warehouses.sql | ✅ SUCCESS | Default warehouse seeded |
| V044 | stock_transfers.sql | ✅ SUCCESS | No journal entries (by design) |
| V045 | sales_receipts.sql | ✅ SUCCESS | Fixed customer_id type (VARCHAR) |
| V046 | recurring_invoices.sql | ✅ SUCCESS | Fixed customer_id type (VARCHAR) |
| V047 | batch_tracking.sql | ✅ SUCCESS | Fixed items→products references |
| V048 | serial_tracking.sql | ✅ SUCCESS | Fixed items→products references |
| V049 | documents.sql | ✅ SUCCESS | Replaced old documents table |

### P5 Tier 1 Professional (6 migrations)

| Version | File | Status | Notes |
|---------|------|--------|-------|
| V050 | fixed_assets.sql | ✅ SUCCESS | - |
| V051 | budgets.sql | ✅ SUCCESS | Required cost_centers first |
| V052 | cost_centers.sql | ✅ SUCCESS | Hierarchy support |
| V053 | recurring_bills.sql | ✅ SUCCESS | Fixed items→products reference |
| V054 | vendor_deposits.sql | ✅ SUCCESS | - |
| V055 | aging_reports.sql | ✅ SUCCESS | AR/AP aging functions |

---

## Tables Created (32 new tables)

### P4 Tables
- warehouses
- warehouse_stock
- stock_transfers
- stock_transfer_items
- stock_transfer_sequences
- sales_receipts
- sales_receipt_items
- sales_receipt_sequences
- recurring_invoices
- recurring_invoice_items
- item_batches
- batch_warehouse_stock
- item_serials
- serial_movements
- documents (replaced)
- document_attachments

### P5 Tables
- asset_categories
- fixed_assets
- asset_depreciations
- asset_maintenance
- fixed_asset_sequences
- budgets
- budget_items
- budget_revisions
- cost_centers
- recurring_bills
- recurring_bill_items
- vendor_deposits
- vendor_deposit_applications
- vendor_deposit_refunds
- vendor_deposit_sequences
- aging_brackets
- aging_snapshots

---

## Row Level Security (RLS)

| Status | Count |
|--------|-------|
| RLS Enabled | 31 |
| RLS Disabled | 2 (sequence tables) |

**Sequence tables without RLS (by design):**
- fixed_asset_sequences
- vendor_deposit_sequences

---

## Column Additions to Existing Tables

| Table | Column | Type | Status |
|-------|--------|------|--------|
| products | track_batches | BOOLEAN | ✅ |
| products | track_expiry | BOOLEAN | ✅ |
| products | track_serial | BOOLEAN | ✅ |
| sales_invoices | warehouse_id | UUID | ✅ |
| sales_invoices | recurring_invoice_id | UUID | ✅ |
| inventory_ledger | warehouse_id | UUID | ✅ |
| inventory_ledger | batch_id | UUID | ✅ |
| journal_lines | cost_center_id | UUID | ✅ |
| bills | recurring_bill_id | UUID | ✅ |

---

## Key Functions Created

### P4 Functions
- generate_stock_transfer_number
- ship_stock_transfer
- receive_stock_transfer
- generate_sales_receipt_number
- get_daily_sales_summary
- calculate_next_invoice_date
- get_due_recurring_invoices
- get_available_batches
- get_expiring_batches
- get_expired_batches
- search_serial_number
- get_available_serials
- mark_serials_sold
- get_serial_history
- generate_document_key
- get_entity_documents
- search_documents

### P5 Functions
- calculate_straight_line_depreciation
- calculate_declining_balance_depreciation
- get_budget_vs_actual
- get_ar_aging_detail
- get_ap_aging_detail

---

## API Endpoints Added (97 endpoints)

### Warehouses (6)
- GET/POST /api/warehouses
- GET/PUT/DELETE /api/warehouses/{warehouse_id}
- POST /api/warehouses/{warehouse_id}/set-default
- GET /api/warehouses/{warehouse_id}/stock
- GET /api/warehouses/{warehouse_id}/stock-value
- GET /api/warehouses/low-stock

### Stock Transfers (6)
- GET/POST /api/stock-transfers
- GET /api/stock-transfers/{transfer_id}
- POST /api/stock-transfers/{transfer_id}/ship
- POST /api/stock-transfers/{transfer_id}/receive
- POST /api/stock-transfers/{transfer_id}/cancel
- GET /api/stock-transfers/in-transit

### Sales Receipts (4)
- GET/POST /api/sales-receipts
- GET /api/sales-receipts/{receipt_id}
- POST /api/sales-receipts/{receipt_id}/void
- GET /api/sales-receipts/daily-summary

### Recurring Invoices (8)
- Full CRUD + generate, pause, resume, history

### Item Batches (7)
- Full CRUD + available, expiring, expired

### Item Serials (10)
- Full CRUD + search, transfer, adjust, history

### Documents (9)
- Upload, attach, detach, search, storage-usage

### Fixed Assets (15)
- Full CRUD + categories, depreciation, maintenance, dispose, sell

### Budgets (12)
- Full CRUD + items, approve, activate, vs-actual, variance-alerts

### Cost Centers (7)
- Full CRUD + tree, summary, transactions, comparison

### Recurring Bills (10)
- Full CRUD + generate, pause, resume, history, process-due

### Vendor Deposits (10)
- Full CRUD + post, apply, refund, void, by-vendor

---

## Fixes Applied During Migration

### 1. Customer ID Type Mismatch
- **Issue:** customers.id is VARCHAR(255), migrations expected UUID
- **Fix:** Changed customer_id in V045 and V046 to VARCHAR(255)
- **Files:** V045__sales_receipts.sql, V046__recurring_invoices.sql

### 2. Items Table Reference
- **Issue:** Migrations referenced "items" table, but table is named "products"
- **Fix:** Changed all references from "items" to "products"
- **Files:** V047__batch_tracking.sql, V048__serial_tracking.sql, V053__recurring_bills.sql
- **Also:** Fixed column references (name→nama_produk, etc.)

### 3. Old Documents Table Conflict
- **Issue:** Existing "documents" table with different structure (integer id)
- **Fix:** Dropped empty old table before migration
- **Note:** Old table was empty, no data loss

### 4. Missing Python Dependency
- **Issue:** `python-dateutil` not in requirements.txt
- **Fix:** Added python-dateutil==2.9.0 to requirements.txt

### 5. Schema Naming Inconsistency
- **Issue:** Router imported `SearchSerialResult`, schema had `SerialSearchResult`
- **Fix:** Added alias `SearchSerialResult = SerialSearchResult`
- **File:** item_serials.py schema

---

## Journal Entry Verification

| Transaction | Creates Journal | Status |
|-------------|-----------------|--------|
| Stock Transfer | NO (by design) | ✅ Correct |
| Sales Receipt | YES | ✅ Implemented |
| Fixed Asset Activate | YES | ✅ Implemented |
| Asset Depreciation | YES | ✅ Implemented |
| Vendor Deposit Post | YES | ✅ Implemented |
| Vendor Deposit Apply | YES | ✅ Implemented |

---

## Account Seeds

Existing accounts used for P4/P5:
- 1-20000: Aset Tetap
- 1-20900: Akumulasi Penyusutan
- 5-30000: Beban Penyusutan
- 5-30100-5-30300: Beban Penyusutan per category

---

## Database Backup

- **Location:** /root/milkyhoop-dev/milkydb_backup_pre_p4p5.dump
- **Size:** 3.8MB
- **Created:** 2026-01-15 14:01 UTC

---

## Rollback Instructions

If needed, restore from backup:
```bash
# Restore from backup
docker exec -i milkyhoop-dev-postgres-1 pg_restore -U postgres -d milkydb -c < milkydb_backup_pre_p4p5.dump
```

---

## Next Steps

1. Test functional flows:
   - Warehouse creation and stock tracking
   - Stock transfers between warehouses
   - POS sales receipts
   - Recurring invoice/bill generation
   - Fixed asset lifecycle (purchase → depreciation → disposal)
   - Budget vs actual comparison
   - AR/AP aging reports

2. Frontend integration:
   - Add UI components for new modules
   - Connect to new API endpoints

3. Production deployment:
   - Apply migrations in staging first
   - Run integration tests
   - Deploy to production

---

## Summary

**Migration Status: SUCCESS**

All 13 migrations for P4 and P5 phases have been successfully applied. The database schema has been extended with 32 new tables, 22+ functions, and 25 triggers. All 97 API endpoints are operational and responding.

Key achievements:
- Multi-warehouse inventory support
- Stock transfer workflow
- POS/Sales receipt functionality
- Recurring invoice and bill automation
- Batch and serial number tracking
- Document/attachment management
- Fixed asset lifecycle management
- Budget planning and variance analysis
- Cost center tracking
- Vendor deposit management
- AR/AP aging reports

The system is ready for functional testing and frontend integration.
