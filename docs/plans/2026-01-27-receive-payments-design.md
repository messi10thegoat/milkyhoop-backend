# Receive Payments Module Design

**Date:** 2026-01-27
**Status:** Approved
**Scope:** MVP + Deposit Integration

## Overview

Backend implementation for Receive Payment (Penerimaan Pembayaran) module. Handles customer payments for invoices with integration to existing customer_deposits module.

## Architecture Decision

**Hybrid Approach:**
- Separate `receive_payments` module for invoice payments
- Integration with existing `customer_deposits`:
  - Overpayment → auto-create customer_deposit
  - Apply deposit → create receive_payment with `source_type='deposit'`

**Rationale:**
| Aspect | Customer Deposits | Receive Payments |
|--------|-------------------|------------------|
| When | Before invoice (advance) | After invoice exists |
| Journal | Dr. Cash, Cr. Deposit Liability | Dr. Cash, Cr. A/R |
| Purpose | Advance payment | Invoice settlement |

## Scope

### Phase 1 - MVP + Deposit (Current)
- CRUD receive_payments
- Payment methods: cash, bank_transfer
- Invoice allocation
- Post → journal entry
- Void → reversing journal
- source_type: 'cash' | 'deposit'
- Overpayment → auto-create customer_deposit
- Apply deposit → reduce deposit balance

### Phase 2 - Deferred
- Check/giro support with clearing workflow

### Phase 3 - Deferred
- Multi-currency with exchange rate
- File attachments
- Tags

## Database Schema

### receive_payments

```sql
CREATE TABLE receive_payments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    payment_number VARCHAR(20) NOT NULL,

    -- Customer
    customer_id UUID NOT NULL,
    customer_name VARCHAR(255) NOT NULL,

    -- Payment details
    payment_date DATE NOT NULL,
    payment_method VARCHAR(20) NOT NULL,  -- 'cash', 'bank_transfer'
    bank_account_id UUID NOT NULL,
    bank_account_name VARCHAR(255) NOT NULL,

    -- Source tracking
    source_type VARCHAR(20) NOT NULL DEFAULT 'cash',  -- 'cash', 'deposit'
    source_deposit_id UUID,  -- FK to customer_deposits when source='deposit'

    -- Amounts (BIGINT - cents)
    total_amount BIGINT NOT NULL,
    allocated_amount BIGINT NOT NULL DEFAULT 0,
    unapplied_amount BIGINT NOT NULL DEFAULT 0,
    discount_amount BIGINT NOT NULL DEFAULT 0,
    discount_account_id UUID,

    -- Journal tracking
    journal_id UUID,
    journal_number VARCHAR(20),
    void_journal_id UUID,

    -- Status
    status VARCHAR(10) NOT NULL DEFAULT 'draft',

    -- Reference
    reference_number VARCHAR(100),
    notes TEXT,

    -- Audit
    created_at TIMESTAMPTZ DEFAULT NOW(),
    created_by UUID,
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    posted_at TIMESTAMPTZ,
    posted_by UUID,
    voided_at TIMESTAMPTZ,
    voided_by UUID,
    void_reason TEXT,

    UNIQUE(tenant_id, payment_number),
    CONSTRAINT valid_status CHECK (status IN ('draft', 'posted', 'voided')),
    CONSTRAINT valid_method CHECK (payment_method IN ('cash', 'bank_transfer')),
    CONSTRAINT valid_source CHECK (
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
    invoice_id UUID NOT NULL,
    invoice_number VARCHAR(20) NOT NULL,
    invoice_amount BIGINT NOT NULL,
    remaining_before BIGINT NOT NULL,
    amount_applied BIGINT NOT NULL,
    remaining_after BIGINT NOT NULL,

    UNIQUE(tenant_id, payment_id, invoice_id)
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

## API Endpoints

### Payment Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/receive-payments` | List payments with filters |
| GET | `/api/receive-payments/:id` | Get payment detail |
| POST | `/api/receive-payments` | Create payment |
| PUT | `/api/receive-payments/:id` | Update draft payment |
| DELETE | `/api/receive-payments/:id` | Delete draft payment |
| POST | `/api/receive-payments/:id/post` | Post draft payment |
| POST | `/api/receive-payments/:id/void` | Void posted payment |

### Customer Endpoints (additions)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/customers/:id/open-invoices` | Get unpaid invoices |
| GET | `/api/customers/:id/available-deposits` | Get deposits with balance |

## Pydantic Schemas

### Request

```python
class AllocationInput(BaseModel):
    invoice_id: str
    amount_applied: int

class CreateReceivePaymentRequest(BaseModel):
    customer_id: str
    payment_date: str  # YYYY-MM-DD
    payment_method: Literal["cash", "bank_transfer"]
    bank_account_id: str
    total_amount: int
    discount_amount: int = 0
    discount_account_id: Optional[str] = None
    source_type: Literal["cash", "deposit"] = "cash"
    source_deposit_id: Optional[str] = None
    reference_number: Optional[str] = None
    notes: Optional[str] = None
    allocations: List[AllocationInput] = []
    save_as_draft: bool = False

class VoidPaymentRequest(BaseModel):
    void_reason: str
```

### Response

```python
class AllocationResponse(BaseModel):
    invoice_id: str
    invoice_number: str
    invoice_amount: int
    remaining_before: int
    amount_applied: int
    remaining_after: int

class ReceivePaymentResponse(BaseModel):
    id: str
    payment_number: str
    customer_id: str
    customer_name: str
    payment_date: str
    payment_method: str
    bank_account_id: str
    bank_account_name: str
    source_type: str
    source_deposit_id: Optional[str]
    total_amount: int
    allocated_amount: int
    unapplied_amount: int
    discount_amount: int
    status: str
    journal_id: Optional[str]
    journal_number: Optional[str]
    reference_number: Optional[str]
    notes: Optional[str]
    allocations: List[AllocationResponse]
    created_at: str
    posted_at: Optional[str]

class ReceivePaymentListItem(BaseModel):
    id: str
    payment_number: str
    customer_name: str
    payment_date: str
    payment_method: str
    total_amount: int
    status: str
    invoice_count: int
```

## Journal Integration

### Scenario 1: Simple Payment
```
Dr. Bank/Kas (1-101xx)              5,000,000
    Cr. Piutang Usaha (1-10300)                 5,000,000
```

### Scenario 2: Payment with Discount
```
Dr. Bank/Kas (1-101xx)              4,800,000
Dr. Potongan Penjualan (6-xxxxx)      200,000
    Cr. Piutang Usaha (1-10300)                 5,000,000
```

### Scenario 3: Overpayment (creates deposit)
```
Dr. Bank/Kas (1-101xx)              6,000,000
    Cr. Piutang Usaha (1-10300)                 5,000,000
    Cr. Uang Muka Pelanggan (2-10200)           1,000,000

+ Auto-create customer_deposit with amount = 1,000,000
```

### Scenario 4: Payment from Deposit
```
Dr. Uang Muka Pelanggan (2-10200)   5,000,000
    Cr. Piutang Usaha (1-10300)                 5,000,000

+ Reduce customer_deposit.remaining_amount
+ Create customer_deposit_application record
```

### Void: Reversing Journal
- Exact opposite of original journal
- Restore invoice remaining amounts
- Reverse deposit effects

## Business Logic

### Create Payment Flow
1. Validate input (customer, bank account, invoices, deposit)
2. Validate amounts (allocations ≤ total, each ≤ invoice remaining)
3. Calculate: allocated_amount, unapplied_amount
4. Generate payment number (RCV-YYYY-NNNN)
5. If save_as_draft=false → execute POST flow

### Post Payment Flow (Transaction)
1. Create journal entry
2. Update invoice balances (paid_amount, remaining, status)
3. Handle deposit:
   - If source_type='deposit': reduce deposit, create application
   - If unapplied_amount > 0: create new deposit
4. Update payment: status='posted', journal_id, posted_at

### Void Payment Flow (Transaction)
1. Create reversing journal
2. Restore invoice balances
3. Reverse deposit effects
4. Update payment: status='voided', void_journal_id, voided_at

## Error Codes

| Code | HTTP | Description |
|------|------|-------------|
| CUSTOMER_NOT_FOUND | 404 | Customer with ID not found |
| INVOICE_NOT_FOUND | 404 | Invoice not found |
| OVER_ALLOCATION | 400 | Amount exceeds invoice remaining |
| INSUFFICIENT_DEPOSIT | 400 | Deposit balance insufficient |
| INVALID_STATUS | 400 | Operation not allowed for status |

## Files to Create/Modify

```
backend/
├── api_gateway/app/
│   ├── routers/
│   │   ├── receive_payments.py    # NEW
│   │   └── customers.py           # EDIT - add 2 endpoints
│   ├── schemas/
│   │   └── receive_payments.py    # NEW
│   └── main.py                    # EDIT - register router
│
├── migrations/
│   └── V085__receive_payments.sql # NEW
```

## Implementation Order

1. Migration (V085__receive_payments.sql)
2. Schemas (receive_payments.py)
3. Router - CRUD endpoints
4. Router - Post/Void with journal
5. Customer endpoints (open-invoices, available-deposits)
6. Deposit integration (overpayment, apply deposit)
