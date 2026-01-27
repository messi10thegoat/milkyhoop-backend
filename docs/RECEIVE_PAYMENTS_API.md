# Receive Payments API Documentation

> **Version:** 1.0.0
> **Release Date:** 2026-01-27
> **Status:** Production Ready

## Overview

Receive Payments (Penerimaan Pembayaran) adalah modul untuk mencatat pembayaran dari pelanggan atas faktur penjualan. Modul ini terintegrasi dengan:
- **Sales Invoices** - Alokasi pembayaran ke faktur
- **Customer Deposits** - Pembayaran dari deposit atau overpayment menjadi deposit
- **Accounting Kernel** - Pembuatan jurnal otomatis

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                      API Gateway (FastAPI)                       │
├─────────────────────────────────────────────────────────────────┤
│  /api/receive-payments              │  /api/customers           │
│  - List, Create, Update, Delete     │  - /{id}/open-invoices    │
│  - Post, Void                       │  - /{id}/available-deposits│
├─────────────────────────────────────────────────────────────────┤
│                    Business Logic Layer                          │
│  ┌──────────────────┐  ┌──────────────────┐  ┌───────────────┐  │
│  │ Payment Workflow │  │ Invoice Alloc    │  │ Deposit Integ │  │
│  │ draft→posted→void│  │ Update balances  │  │ Create/Apply  │  │
│  └──────────────────┘  └──────────────────┘  └───────────────┘  │
├─────────────────────────────────────────────────────────────────┤
│                    Accounting Kernel (Layer 0)                   │
│  Journal Entry Creation │ Double-Entry Bookkeeping               │
├─────────────────────────────────────────────────────────────────┤
│                      PostgreSQL + RLS                            │
│  receive_payments │ receive_payment_allocations │ sequences      │
└─────────────────────────────────────────────────────────────────┘
```

## API Endpoints

### Base URL: `/api/receive-payments`

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | List payments with filters |
| GET | `/summary` | Get summary statistics |
| GET | `/{payment_id}` | Get payment detail |
| POST | `/` | Create new payment |
| PUT | `/{payment_id}` | Update draft payment |
| DELETE | `/{payment_id}` | Delete draft payment |
| POST | `/{payment_id}/post` | Post draft to ledger |
| POST | `/{payment_id}/void` | Void posted payment |

### Customer Supporting Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/customers/{id}/open-invoices` | Get unpaid invoices |
| GET | `/api/customers/{id}/available-deposits` | Get deposits with balance |

---

## Endpoint Details

### 1. List Payments

```http
GET /api/receive-payments
Authorization: Bearer {token}
```

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `status` | string | - | Filter: `draft`, `posted`, `voided` |
| `customer_id` | string | - | Filter by customer |
| `start_date` | date | - | Payment date from (YYYY-MM-DD) |
| `end_date` | date | - | Payment date to (YYYY-MM-DD) |
| `search` | string | - | Search payment number or customer name |
| `sort_by` | string | `created_at` | Sort field |
| `sort_order` | string | `desc` | `asc` or `desc` |
| `skip` | int | 0 | Pagination offset |
| `limit` | int | 20 | Items per page (max 100) |

**Response:**

```json
{
  "items": [
    {
      "id": "5d1f9e6b-0986-4365-940b-e848872b1585",
      "payment_number": "RCV-2026-0001",
      "customer_id": "8551df78-e1b6-4ab4-989b-e92405e8b196",
      "customer_name": "CV Maju Terus",
      "payment_date": "2026-01-27",
      "payment_method": "bank_transfer",
      "source_type": "cash",
      "total_amount": 5000000,
      "allocated_amount": 5000000,
      "unapplied_amount": 0,
      "status": "posted",
      "invoice_count": 1,
      "created_at": "2026-01-27T13:44:27.405047Z"
    }
  ],
  "total": 1,
  "has_more": false
}
```

---

### 2. Get Summary

```http
GET /api/receive-payments/summary
Authorization: Bearer {token}
```

**Response:**

```json
{
  "success": true,
  "data": {
    "total": 10,
    "draft_count": 2,
    "posted_count": 7,
    "voided_count": 1,
    "total_received": 50000000,
    "total_allocated": 45000000,
    "total_unapplied": 5000000
  }
}
```

---

### 3. Get Payment Detail

```http
GET /api/receive-payments/{payment_id}
Authorization: Bearer {token}
```

**Response:**

```json
{
  "success": true,
  "data": {
    "id": "5d1f9e6b-0986-4365-940b-e848872b1585",
    "payment_number": "RCV-2026-0001",
    "customer_id": "8551df78-e1b6-4ab4-989b-e92405e8b196",
    "customer_name": "CV Maju Terus",
    "payment_date": "2026-01-27",
    "payment_method": "bank_transfer",
    "bank_account_id": "c507c354-2c7f-4aaf-ba2e-594b972ed0c5",
    "bank_account_name": "Bank BCA",
    "source_type": "cash",
    "source_deposit_id": null,
    "total_amount": 5000000,
    "allocated_amount": 5000000,
    "unapplied_amount": 0,
    "discount_amount": 0,
    "discount_account_id": null,
    "status": "posted",
    "reference_number": "TRF-202601270001",
    "notes": "E2E Test Payment",
    "journal_id": "ef424b3d-e773-4d50-9c69-a016f05028a6",
    "journal_number": "RCV-2601-0001",
    "allocations": [
      {
        "id": "a1b2c3d4-...",
        "invoice_id": "24d93c67-9493-4ab2-aff3-c198c28a6826",
        "invoice_number": "INV-2512-P20",
        "invoice_amount": 14629333,
        "remaining_before": 5115862,
        "amount_applied": 5000000,
        "remaining_after": 115862
      }
    ],
    "created_at": "2026-01-27T13:44:27.405047Z",
    "posted_at": "2026-01-27T13:44:27.405047Z",
    "created_deposit_id": null
  }
}
```

---

### 4. Create Payment

```http
POST /api/receive-payments
Authorization: Bearer {token}
Content-Type: application/json
```

**Request Body:**

```json
{
  "customer_id": "8551df78-e1b6-4ab4-989b-e92405e8b196",
  "customer_name": "CV Maju Terus",
  "payment_date": "2026-01-27",
  "payment_method": "bank_transfer",
  "bank_account_id": "c507c354-2c7f-4aaf-ba2e-594b972ed0c5",
  "bank_account_name": "Bank BCA",
  "total_amount": 5000000,
  "source_type": "cash",
  "source_deposit_id": null,
  "discount_amount": 0,
  "discount_account_id": null,
  "reference_number": "TRF-202601270001",
  "notes": "Pembayaran invoice Januari",
  "allocations": [
    {
      "invoice_id": "24d93c67-9493-4ab2-aff3-c198c28a6826",
      "amount_applied": 5000000
    }
  ],
  "save_as_draft": false
}
```

**Field Descriptions:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `customer_id` | string | Yes | Customer ID (VARCHAR) |
| `customer_name` | string | Yes | Customer display name |
| `payment_date` | date | Yes | Payment date (YYYY-MM-DD) |
| `payment_method` | string | Yes | `cash` or `bank_transfer` |
| `bank_account_id` | UUID | Yes | Chart of Accounts ID for bank/cash |
| `bank_account_name` | string | Yes | Bank account display name |
| `total_amount` | int | Yes | Total payment amount (IDR) |
| `source_type` | string | No | `cash` (default) or `deposit` |
| `source_deposit_id` | UUID | Conditional | Required if source_type='deposit' |
| `discount_amount` | int | No | Sales discount amount |
| `discount_account_id` | UUID | Conditional | Required if discount_amount > 0 |
| `reference_number` | string | No | External reference (transfer number) |
| `notes` | string | No | Payment notes |
| `allocations` | array | No | Invoice allocations |
| `save_as_draft` | bool | No | If true, save as draft (don't post) |

**Response (201 Created):**

```json
{
  "success": true,
  "message": "Receive payment created and posted",
  "data": {
    "id": "5d1f9e6b-0986-4365-940b-e848872b1585",
    "payment_number": "RCV-2026-0001",
    "total_amount": 5000000,
    "allocated_amount": 5000000,
    "unapplied_amount": 0,
    "status": "posted",
    "journal_id": "ef424b3d-e773-4d50-9c69-a016f05028a6"
  }
}
```

---

### 5. Update Draft Payment

```http
PUT /api/receive-payments/{payment_id}
Authorization: Bearer {token}
Content-Type: application/json
```

**Request Body:** Same as Create, but only for `draft` status payments.

**Response:**

```json
{
  "success": true,
  "message": "Receive payment updated",
  "data": {
    "id": "5d1f9e6b-...",
    "payment_number": "RCV-2026-0001",
    "status": "draft"
  }
}
```

---

### 6. Delete Draft Payment

```http
DELETE /api/receive-payments/{payment_id}
Authorization: Bearer {token}
```

**Response:**

```json
{
  "success": true,
  "message": "Receive payment deleted"
}
```

**Note:** Only `draft` payments can be deleted.

---

### 7. Post Payment

```http
POST /api/receive-payments/{payment_id}/post
Authorization: Bearer {token}
```

**Behavior:**
1. Creates journal entry (Dr. Bank, Cr. A/R)
2. Updates invoice paid amounts
3. If overpayment → creates customer deposit
4. If source='deposit' → reduces deposit balance
5. Changes status to `posted`

**Response:**

```json
{
  "success": true,
  "message": "Payment posted successfully",
  "data": {
    "id": "5d1f9e6b-...",
    "status": "posted",
    "journal_id": "ef424b3d-...",
    "journal_number": "RCV-2601-0001"
  }
}
```

---

### 8. Void Payment

```http
POST /api/receive-payments/{payment_id}/void
Authorization: Bearer {token}
Content-Type: application/json
```

**Request Body:**

```json
{
  "void_reason": "Salah input nominal"
}
```

**Behavior:**
1. Creates reversing journal entry
2. Restores invoice balances
3. Reverses deposit effects (if any)
4. Changes status to `voided`

**Response:**

```json
{
  "success": true,
  "message": "Payment voided successfully",
  "data": {
    "id": "5d1f9e6b-...",
    "status": "voided",
    "void_journal_id": "abc123-..."
  }
}
```

---

### 9. Get Customer Open Invoices

```http
GET /api/customers/{customer_id}/open-invoices
Authorization: Bearer {token}
```

**Response:**

```json
{
  "invoices": [
    {
      "id": "24d93c67-9493-4ab2-aff3-c198c28a6826",
      "invoice_number": "INV-2512-P20",
      "invoice_date": "2025-12-06",
      "due_date": "2026-01-05",
      "total_amount": 14629333,
      "paid_amount": 9513471,
      "remaining_amount": 5115862,
      "is_overdue": true,
      "overdue_days": 22
    }
  ],
  "summary": {
    "total_outstanding": 15220910,
    "total_overdue": 12077260,
    "invoice_count": 3
  }
}
```

---

### 10. Get Customer Available Deposits

```http
GET /api/customers/{customer_id}/available-deposits
Authorization: Bearer {token}
```

**Response:**

```json
{
  "deposits": [
    {
      "id": "dep-uuid-...",
      "deposit_number": "DEP-2026-0001",
      "deposit_date": "2026-01-15",
      "original_amount": 10000000,
      "applied_amount": 5000000,
      "remaining_amount": 5000000
    }
  ],
  "total_available": 5000000
}
```

---

## Database Schema

### receive_payments

```sql
CREATE TABLE receive_payments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    payment_number VARCHAR(50) NOT NULL,

    -- Customer (customers.id is VARCHAR(255))
    customer_id VARCHAR(255) REFERENCES customers(id),
    customer_name VARCHAR(255) NOT NULL,

    -- Payment details
    payment_date DATE NOT NULL,
    payment_method VARCHAR(30) NOT NULL,  -- 'cash', 'bank_transfer'
    bank_account_id UUID NOT NULL,        -- CoA account for cash/bank
    bank_account_name VARCHAR(255) NOT NULL,

    -- Source tracking
    source_type VARCHAR(20) NOT NULL DEFAULT 'cash',  -- 'cash', 'deposit'
    source_deposit_id UUID REFERENCES customer_deposits(id),

    -- Amounts (BIGINT for IDR)
    total_amount BIGINT NOT NULL,
    allocated_amount BIGINT NOT NULL DEFAULT 0,
    unapplied_amount BIGINT NOT NULL DEFAULT 0,
    discount_amount BIGINT NOT NULL DEFAULT 0,
    discount_account_id UUID,

    -- Status: draft -> posted -> voided
    status VARCHAR(20) DEFAULT 'draft',

    -- Reference & notes
    reference_number VARCHAR(100),
    notes TEXT,

    -- Accounting integration
    journal_id UUID,
    journal_number VARCHAR(50),
    void_journal_id UUID,

    -- Overpayment creates deposit
    created_deposit_id UUID REFERENCES customer_deposits(id),

    -- Audit
    posted_at TIMESTAMPTZ,
    posted_by UUID,
    voided_at TIMESTAMPTZ,
    voided_by UUID,
    void_reason TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    created_by UUID,

    CONSTRAINT uq_rcv_payments_tenant_number UNIQUE(tenant_id, payment_number),
    CONSTRAINT chk_rcv_payment_status CHECK (status IN ('draft', 'posted', 'voided')),
    CONSTRAINT chk_rcv_payment_method CHECK (payment_method IN ('cash', 'bank_transfer')),
    CONSTRAINT chk_rcv_payment_source CHECK (source_type IN ('cash', 'deposit')),
    CONSTRAINT chk_rcv_payment_source_valid CHECK (
        (source_type = 'cash' AND source_deposit_id IS NULL) OR
        (source_type = 'deposit' AND source_deposit_id IS NOT NULL)
    )
);
```

### receive_payment_allocations

```sql
CREATE TABLE receive_payment_allocations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    payment_id UUID NOT NULL REFERENCES receive_payments(id) ON DELETE CASCADE,

    -- Invoice reference
    invoice_id UUID NOT NULL REFERENCES sales_invoices(id),
    invoice_number VARCHAR(50) NOT NULL,

    -- Allocation details
    invoice_amount BIGINT NOT NULL,      -- Original invoice total
    remaining_before BIGINT NOT NULL,    -- Remaining before this payment
    amount_applied BIGINT NOT NULL,      -- Amount applied from this payment
    remaining_after BIGINT NOT NULL,     -- Remaining after this payment

    created_at TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT uq_rcv_payment_allocation UNIQUE(tenant_id, payment_id, invoice_id)
);
```

### receive_payment_sequences

```sql
CREATE TABLE receive_payment_sequences (
    tenant_id TEXT PRIMARY KEY,
    last_number INT NOT NULL DEFAULT 0,
    year INT NOT NULL
);
```

### Indexes

```sql
CREATE INDEX idx_rcv_payments_tenant_status ON receive_payments(tenant_id, status);
CREATE INDEX idx_rcv_payments_tenant_date ON receive_payments(tenant_id, payment_date);
CREATE INDEX idx_rcv_payments_customer ON receive_payments(tenant_id, customer_id);
CREATE INDEX idx_rcv_payments_number ON receive_payments(tenant_id, payment_number);
CREATE INDEX idx_rcv_payments_source_deposit ON receive_payments(source_deposit_id)
    WHERE source_deposit_id IS NOT NULL;

CREATE INDEX idx_rcv_alloc_payment ON receive_payment_allocations(payment_id);
CREATE INDEX idx_rcv_alloc_invoice ON receive_payment_allocations(invoice_id);
```

---

## Journal Integration

### Scenario 1: Simple Payment

Customer pays Rp 5,000,000 for invoice.

```
Dr. Bank BCA (1-10201)              5,000,000
    Cr. Piutang Usaha (1-10400)                 5,000,000
```

### Scenario 2: Payment with Discount

Customer pays Rp 4,800,000 for Rp 5,000,000 invoice with Rp 200,000 discount.

```
Dr. Bank BCA (1-10201)              4,800,000
Dr. Potongan Penjualan (6-xxxxx)      200,000
    Cr. Piutang Usaha (1-10400)                 5,000,000
```

### Scenario 3: Overpayment (creates deposit)

Customer pays Rp 6,000,000 for Rp 5,000,000 invoice.

```
Dr. Bank BCA (1-10201)              6,000,000
    Cr. Piutang Usaha (1-10400)                 5,000,000
    Cr. Uang Muka Pelanggan (2-10400)           1,000,000

+ Auto-create customer_deposit with amount = 1,000,000
```

### Scenario 4: Payment from Deposit

Using Rp 5,000,000 deposit to pay invoice.

```
Dr. Uang Muka Pelanggan (2-10400)   5,000,000
    Cr. Piutang Usaha (1-10400)                 5,000,000

+ Reduce customer_deposit.remaining_amount by 5,000,000
+ Create customer_deposit_application record
```

### Void Payment (Reversing Journal)

Exact opposite of original journal entries.

---

## Business Logic

### Payment Number Format

```
RCV-YYYY-NNNN
Example: RCV-2026-0001
```

Auto-generated per tenant, resets each year.

### Validation Rules

1. **Customer** must exist
2. **Bank Account** must be ASSET type (Kas/Bank)
3. **Allocation amounts** cannot exceed invoice remaining
4. **Total allocations** cannot exceed payment amount
5. **Source deposit** must have sufficient balance
6. **Posted payments** cannot be modified (only voided)

### Status Workflow

```
draft ──► posted ──► voided
  │         ▲
  └─────────┘
   (if save_as_draft=false, auto-post)
```

### Deposit Integration

| Scenario | Behavior |
|----------|----------|
| `source_type='cash'` + overpayment | Auto-create customer_deposit |
| `source_type='deposit'` | Reduce deposit balance, create application record |
| Void payment with deposit | Reverse all deposit effects |

---

## Error Codes

| Code | HTTP | Description |
|------|------|-------------|
| `CUSTOMER_NOT_FOUND` | 400 | Customer ID not found |
| `BANK_ACCOUNT_NOT_FOUND` | 400 | Bank account not found in CoA |
| `INVALID_BANK_ACCOUNT` | 400 | Account is not ASSET type |
| `INVOICE_NOT_FOUND` | 400 | Invoice ID not found |
| `OVER_ALLOCATION` | 400 | Amount exceeds invoice remaining |
| `TOTAL_EXCEEDS_PAYMENT` | 400 | Allocations exceed payment amount |
| `DEPOSIT_NOT_FOUND` | 400 | Source deposit not found |
| `INSUFFICIENT_DEPOSIT` | 400 | Deposit balance insufficient |
| `INVALID_STATUS` | 400 | Operation not allowed for status |
| `PAYMENT_NOT_FOUND` | 404 | Payment ID not found |
| `ALREADY_POSTED` | 400 | Cannot modify posted payment |
| `ALREADY_VOIDED` | 400 | Payment already voided |

---

## Files

### Created/Modified

| File | Description |
|------|-------------|
| `backend/migrations/V087__receive_payments.sql` | Database migration |
| `backend/api_gateway/app/schemas/receive_payments.py` | Pydantic schemas |
| `backend/api_gateway/app/routers/receive_payments.py` | API endpoints |
| `backend/api_gateway/app/routers/customers.py` | Added supporting endpoints |
| `backend/api_gateway/app/main.py` | Router registration |

### Configuration

| Constant | Value | Description |
|----------|-------|-------------|
| `AR_ACCOUNT` | `1-10400` | Piutang Usaha (Accounts Receivable) |
| `CUSTOMER_DEPOSIT_ACCOUNT` | `2-10400` | Uang Muka Pelanggan (Liability) |

---

## Usage Examples

### Create and Post Payment

```bash
# Create payment
curl -X POST "https://api.milkyhoop.com/api/receive-payments" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "customer_id": "cust-123",
    "customer_name": "CV Maju Terus",
    "payment_date": "2026-01-27",
    "payment_method": "bank_transfer",
    "bank_account_id": "coa-bank-uuid",
    "bank_account_name": "Bank BCA",
    "total_amount": 5000000,
    "source_type": "cash",
    "allocations": [
      {"invoice_id": "inv-uuid", "amount_applied": 5000000}
    ],
    "save_as_draft": false
  }'
```

### Get Open Invoices for Payment Form

```bash
curl "https://api.milkyhoop.com/api/customers/cust-123/open-invoices" \
  -H "Authorization: Bearer $TOKEN"
```

### Pay from Deposit

```bash
curl -X POST "https://api.milkyhoop.com/api/receive-payments" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "customer_id": "cust-123",
    "customer_name": "CV Maju Terus",
    "payment_date": "2026-01-27",
    "payment_method": "bank_transfer",
    "bank_account_id": "coa-bank-uuid",
    "bank_account_name": "Bank BCA",
    "total_amount": 5000000,
    "source_type": "deposit",
    "source_deposit_id": "deposit-uuid",
    "allocations": [
      {"invoice_id": "inv-uuid", "amount_applied": 5000000}
    ]
  }'
```

### Void Payment

```bash
curl -X POST "https://api.milkyhoop.com/api/receive-payments/{id}/void" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"void_reason": "Salah input nominal"}'
```

---

## E2E Test Results (2026-01-27)

| Test | Status | Notes |
|------|--------|-------|
| GET /api/receive-payments | ✅ Pass | Returns list |
| GET /api/receive-payments/summary | ✅ Pass | Returns statistics |
| GET /api/customers/{id}/open-invoices | ✅ Pass | Returns 3 invoices |
| GET /api/customers/{id}/available-deposits | ✅ Pass | Returns deposits |
| POST /api/receive-payments | ✅ Pass | Created RCV-2026-0001 |
| Journal Entry Created | ✅ Pass | RCV-2601-0001 |

### Sample Payment Created

```json
{
  "id": "5d1f9e6b-0986-4365-940b-e848872b1585",
  "payment_number": "RCV-2026-0001",
  "customer_name": "CV Maju Terus",
  "total_amount": 5000000,
  "allocated_amount": 5000000,
  "status": "posted",
  "journal_number": "RCV-2601-0001"
}
```

---

## Related Documentation

- [Receive Payments Design](./plans/2026-01-27-receive-payments-design.md)
- [Receive Payments Implementation Plan](./plans/2026-01-27-receive-payments-implementation.md)
- [Customer Deposits API](./CUSTOMER_DEPOSITS_API.md)
- [Sales Invoices API](./SALES_INVOICES_API.md)
- [Accounting Kernel API](./ACCOUNTING_KERNEL_API.md)

---

*Last Updated: 2026-01-27*
*Author: Claude Opus 4.5*
