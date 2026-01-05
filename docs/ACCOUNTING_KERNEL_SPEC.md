# MilkyHoop Accounting Kernel Architecture
## Final Production-Ready Specification v1.0

**Date:** January 4, 2026
**Status:** Final Draft
**Version:** 1.0

---

## Table of Contents

1. [Overview](#1-overview)
2. [Core Principles](#2-core-principles)
3. [System Architecture](#3-system-architecture)
4. [Database Schema](#4-database-schema)
5. [Service Architecture](#5-service-architecture)
6. [Core Interfaces (gRPC/REST)](#6-core-interfaces)
7. [Auto-Posting Rules](#7-auto-posting-rules)
8. [Default Chart of Accounts](#8-default-chart-of-accounts)
9. [Report Generation](#9-report-generation)
10. [Event Flow & Integration](#10-event-flow--integration)
11. [Consumer-Safe Implementation](#11-consumer-safe-implementation)
12. [Scalability & Performance](#12-scalability--performance)
13. [Security & Tenant Isolation](#13-security--tenant-isolation)
14. [Operational Considerations](#14-operational-considerations)
15. [Validation Checklist](#15-validation-checklist)
16. [Tech Stack](#16-tech-stack)
17. [Implementation Roadmap](#17-implementation-roadmap)

---

## 1. Overview

### 1.1 Purpose

MilkyHoop Accounting Kernel adalah engine akuntansi inti yang menangani:
- Double-entry bookkeeping
- General Ledger management
- Accounts Receivable (AR) / Accounts Payable (AP)
- Financial reporting (P&L, Balance Sheet, Cash Flow)

### 1.2 Design Goals

| Goal | Description |
|------|-------------|
| **Modular** | Setiap komponen terpisah, bisa di-scale sendiri |
| **Scalable** | Handle growth dari UMKM hingga enterprise |
| **Best Practice** | Ikut standar industri (QuickBooks, Xero, Accurate) |
| **Easy to Upgrade** | Extensible tanpa breaking change |
| **Audit-Ready** | Full traceability & compliance |

### 1.3 High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                      BUSINESS LAYER                             │
│  (POS, Pembelian, Penjualan, Expense, Inventory)                │
└─────────────────────────┬───────────────────────────────────────┘
                          │ Events (Invoice, Bill, Payment, etc.)
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│                   ACCOUNTING GATEWAY                            │
│              (API / Event Consumer / Validator)                 │
└─────────────────────────┬───────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│                   ACCOUNTING KERNEL                             │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐               │
│  │ CoA Service │ │Journal Svc  │ │ Ledger Svc  │               │
│  └─────────────┘ └─────────────┘ └─────────────┘               │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐               │
│  │  AR Service │ │ AP Service  │ │ Cash Service│               │
│  └─────────────┘ └─────────────┘ └─────────────┘               │
└─────────────────────────┬───────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│                    REPORTING ENGINE                             │
│        (P&L, Balance Sheet, Cash Flow, Aging, Trial Balance)    │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. Core Principles

### 2.1 Fundamental Rules (NON-NEGOTIABLE)

| Principle | Rule | Rationale |
|-----------|------|-----------|
| **Double-entry** | Setiap transaksi: `SUM(debit) = SUM(credit)` | Standar akuntansi internasional |
| **Append-only** | Journal TIDAK BOLEH edit/delete | Audit trail integrity |
| **Source-traceable** | Setiap journal punya source (Invoice, Bill, etc.) | Reconciliation & audit |
| **Ledger = Truth** | Semua report derivasi dari ledger | Single source of truth |
| **Idempotent** | Posting ulang dengan `trace_id` sama = skip | Exactly-once guarantee |
| **Atomic** | Journal header + lines dalam 1 transaction | Data consistency |

### 2.2 What This Means

```
❌ TIDAK BOLEH:
- Edit journal entry yang sudah posted
- Delete journal entry
- Buat report dari data transaksi langsung (bypass ledger)
- Hardcode account mapping
- Campur business logic & accounting logic

✅ HARUS:
- Void journal = buat reversing journal baru
- Semua report baca dari ledger/trial balance
- Account mapping via CoA service
- Separation of concerns
```

---

## 3. System Architecture

### 3.1 Service Topology

```
┌─────────────────────────────────────────────────────────────────┐
│                    EXTERNAL SERVICES                            │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐           │
│  │ Invoice  │ │   Bill   │ │ Payment  │ │   POS    │           │
│  │ Service  │ │ Service  │ │ Service  │ │ Service  │           │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘           │
└───────┼────────────┼────────────┼────────────┼──────────────────┘
        │            │            │            │
        ▼            ▼            ▼            ▼
┌─────────────────────────────────────────────────────────────────┐
│                      KAFKA / EVENT BUS                          │
│  Topics: invoice.created, bill.created, payment.received        │
└─────────────────────────┬───────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│                 ACCOUNTING KERNEL SERVICE                       │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │                  Event Consumer                          │   │
│  │         (Idempotent, Consumer-Safe)                      │   │
│  └─────────────────────────┬───────────────────────────────┘   │
│                            │                                    │
│  ┌─────────────────────────▼───────────────────────────────┐   │
│  │                  Posting Router                          │   │
│  │    (Route to correct posting rule by event type)         │   │
│  └─────────────────────────┬───────────────────────────────┘   │
│                            │                                    │
│  ┌────────────┬────────────┼────────────┬────────────┐         │
│  ▼            ▼            ▼            ▼            ▼         │
│ ┌────┐     ┌────┐     ┌────────┐   ┌────┐      ┌────┐         │
│ │CoA │     │Jrnl│     │ Ledger │   │ AR │      │ AP │         │
│ │Svc │     │Svc │     │  Svc   │   │Svc │      │Svc │         │
│ └────┘     └────┘     └────────┘   └────┘      └────┘         │
│                                                                │
└────────────────────────────┬───────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                    POSTGRESQL DATABASE                          │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐            │
│  │     CoA      │ │   Journal    │ │   Subledger  │            │
│  │    Tables    │ │    Tables    │ │   (AR/AP)    │            │
│  └──────────────┘ └──────────────┘ └──────────────┘            │
│  ┌──────────────┐ ┌──────────────┐                             │
│  │   Balances   │ │    Fiscal    │                             │
│  │  Read Model  │ │   Periods    │                             │
│  └──────────────┘ └──────────────┘                             │
└─────────────────────────────────────────────────────────────────┘
```

---

## 4. Database Schema

### 4.1 Chart of Accounts (CoA)

```sql
CREATE TABLE chart_of_accounts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL,
    code            VARCHAR(20) NOT NULL,      -- "1-10001"
    name            VARCHAR(100) NOT NULL,     -- "Kas"
    type            VARCHAR(20) NOT NULL,      -- ASSET, LIABILITY, EQUITY, INCOME, EXPENSE
    normal_balance  VARCHAR(10) NOT NULL,      -- DEBIT, CREDIT
    parent_id       UUID REFERENCES chart_of_accounts(id),
    is_active       BOOLEAN DEFAULT true,
    is_system       BOOLEAN DEFAULT false,     -- System account, cannot delete
    metadata        JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now(),

    UNIQUE(tenant_id, code)
);

CREATE INDEX idx_coa_tenant ON chart_of_accounts(tenant_id);
CREATE INDEX idx_coa_type ON chart_of_accounts(tenant_id, type);
CREATE INDEX idx_coa_parent ON chart_of_accounts(parent_id);

ALTER TABLE chart_of_accounts
ADD CONSTRAINT chk_coa_type
CHECK (type IN ('ASSET', 'LIABILITY', 'EQUITY', 'INCOME', 'EXPENSE'));

ALTER TABLE chart_of_accounts
ADD CONSTRAINT chk_coa_normal_balance
CHECK (normal_balance IN ('DEBIT', 'CREDIT'));
```

### 4.2 Journal Entries (Header) - Partitioned

```sql
CREATE TABLE journal_entries (
    id              UUID NOT NULL DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL,
    journal_number  VARCHAR(50) NOT NULL,      -- "JV-2026-0001"
    journal_date    DATE NOT NULL,
    description     TEXT,

    -- Source tracking
    source_type     VARCHAR(30) NOT NULL,      -- INVOICE, BILL, PAYMENT, POS, ADJUSTMENT, MANUAL
    source_id       UUID,
    trace_id        UUID NOT NULL,             -- Idempotency key
    source_snapshot JSONB,                     -- Full source payload for audit

    -- Status
    status          VARCHAR(20) DEFAULT 'POSTED',
    voided_by       UUID,

    -- Audit
    posted_at       TIMESTAMPTZ,
    posted_by       UUID,
    created_at      TIMESTAMPTZ DEFAULT now(),
    version         INT DEFAULT 1,             -- Optimistic locking

    PRIMARY KEY (id, journal_date),
    UNIQUE(tenant_id, trace_id, journal_date)
) PARTITION BY RANGE (journal_date);

ALTER TABLE journal_entries
ADD CONSTRAINT chk_journal_status
CHECK (status IN ('DRAFT', 'POSTED', 'VOID'));

-- Create monthly partitions (example)
CREATE TABLE journal_entries_2026_01
PARTITION OF journal_entries
FOR VALUES FROM ('2026-01-01') TO ('2026-02-01');

CREATE TABLE journal_entries_2026_02
PARTITION OF journal_entries
FOR VALUES FROM ('2026-02-01') TO ('2026-03-01');
```

### 4.3 Journal Lines (Detail)

```sql
CREATE TABLE journal_lines (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    journal_id      UUID NOT NULL,
    journal_date    DATE NOT NULL,             -- For partition reference
    account_id      UUID NOT NULL REFERENCES chart_of_accounts(id),
    line_number     INT NOT NULL,              -- Stable ordering
    description     TEXT,
    debit           DECIMAL(24,6) DEFAULT 0,   -- High precision for HPP/conversion
    credit          DECIMAL(24,6) DEFAULT 0,

    -- Optional dimensions
    department_id   UUID,
    project_id      UUID,

    -- Currency support (future)
    currency        CHAR(3) DEFAULT 'IDR',
    exchange_rate   DECIMAL(18,8) DEFAULT 1,
    amount_local    DECIMAL(24,6),

    FOREIGN KEY (journal_id, journal_date)
        REFERENCES journal_entries(id, journal_date),

    CONSTRAINT chk_debit_credit CHECK (
        (debit >= 0 AND credit >= 0) AND
        ((debit > 0 AND credit = 0) OR (credit > 0 AND debit = 0) OR (debit = 0 AND credit = 0))
    )
);

CREATE INDEX idx_journal_lines_journal ON journal_lines(journal_id);
CREATE INDEX idx_journal_lines_account ON journal_lines(account_id);
CREATE INDEX idx_journal_lines_account_date ON journal_lines(account_id, journal_date);
```

### 4.4 Accounts Receivable (Subledger)

```sql
CREATE TABLE accounts_receivable (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL,
    customer_id     UUID NOT NULL,

    -- Source
    source_type     VARCHAR(30) NOT NULL,      -- INVOICE, POS
    source_id       UUID NOT NULL,
    source_number   VARCHAR(50),

    -- Amount
    amount          DECIMAL(24,6) NOT NULL,
    balance         DECIMAL(24,6) NOT NULL,    -- Remaining unpaid
    currency        CHAR(3) DEFAULT 'IDR',

    -- Dates
    issue_date      DATE NOT NULL,
    due_date        DATE NOT NULL,

    -- Status
    status          VARCHAR(20) DEFAULT 'OPEN', -- OPEN, PARTIAL, PAID, VOID

    -- Link to journal
    journal_id      UUID,
    journal_date    DATE,

    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now(),

    FOREIGN KEY (journal_id, journal_date)
        REFERENCES journal_entries(id, journal_date)
);

CREATE INDEX idx_ar_tenant_customer ON accounts_receivable(tenant_id, customer_id);
CREATE INDEX idx_ar_status ON accounts_receivable(tenant_id, status);
CREATE INDEX idx_ar_due_date ON accounts_receivable(tenant_id, due_date);
CREATE INDEX idx_ar_source ON accounts_receivable(tenant_id, source_type, source_id);
```

### 4.5 Accounts Payable (Subledger)

```sql
CREATE TABLE accounts_payable (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL,
    supplier_id     UUID NOT NULL,

    -- Source
    source_type     VARCHAR(30) NOT NULL,      -- BILL
    source_id       UUID NOT NULL,
    source_number   VARCHAR(50),

    -- Amount
    amount          DECIMAL(24,6) NOT NULL,
    balance         DECIMAL(24,6) NOT NULL,
    currency        CHAR(3) DEFAULT 'IDR',

    -- Dates
    issue_date      DATE NOT NULL,
    due_date        DATE NOT NULL,

    -- Status
    status          VARCHAR(20) DEFAULT 'OPEN',

    -- Link to journal
    journal_id      UUID,
    journal_date    DATE,

    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now(),

    FOREIGN KEY (journal_id, journal_date)
        REFERENCES journal_entries(id, journal_date)
);

CREATE INDEX idx_ap_tenant_supplier ON accounts_payable(tenant_id, supplier_id);
CREATE INDEX idx_ap_status ON accounts_payable(tenant_id, status);
CREATE INDEX idx_ap_due_date ON accounts_payable(tenant_id, due_date);
```

### 4.6 Fiscal Periods

```sql
CREATE TABLE fiscal_periods (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL,
    period_name     VARCHAR(20) NOT NULL,      -- "2026-01"
    start_date      DATE NOT NULL,
    end_date        DATE NOT NULL,
    is_closed       BOOLEAN DEFAULT false,
    closed_at       TIMESTAMPTZ,
    closed_by       UUID,
    closing_journal_id UUID,                   -- Pointer to closing entry

    -- Snapshot balances at close
    opening_balances JSONB,
    closing_balances JSONB,

    UNIQUE(tenant_id, period_name)
);

CREATE INDEX idx_fiscal_tenant ON fiscal_periods(tenant_id);
CREATE INDEX idx_fiscal_dates ON fiscal_periods(tenant_id, start_date, end_date);
```

### 4.7 Account Balances (Materialized Read Model)

```sql
CREATE TABLE account_balances_daily (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL,
    account_id      UUID NOT NULL REFERENCES chart_of_accounts(id),
    balance_date    DATE NOT NULL,

    -- Running balances
    opening_debit   DECIMAL(24,6) DEFAULT 0,
    opening_credit  DECIMAL(24,6) DEFAULT 0,
    period_debit    DECIMAL(24,6) DEFAULT 0,
    period_credit   DECIMAL(24,6) DEFAULT 0,
    closing_debit   DECIMAL(24,6) DEFAULT 0,
    closing_credit  DECIMAL(24,6) DEFAULT 0,

    -- Net balance (computed)
    net_balance     DECIMAL(24,6) DEFAULT 0,

    updated_at      TIMESTAMPTZ DEFAULT now(),

    UNIQUE(tenant_id, account_id, balance_date)
);

CREATE INDEX idx_balances_tenant_date ON account_balances_daily(tenant_id, balance_date);
CREATE INDEX idx_balances_account ON account_balances_daily(account_id, balance_date);
```

### 4.8 Outbox Table (Event Publishing)

```sql
CREATE TABLE accounting_outbox (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL,
    event_type      VARCHAR(100) NOT NULL,     -- accounting.journal.posted
    event_key       VARCHAR(100),              -- Kafka key
    payload         JSONB NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT now(),
    published_at    TIMESTAMPTZ,
    is_published    BOOLEAN DEFAULT false
);

CREATE INDEX idx_outbox_unpublished ON accounting_outbox(is_published, created_at)
WHERE is_published = false;
```

### 4.9 Row Level Security (Tenant Isolation)

```sql
-- Enable RLS on all tables
ALTER TABLE chart_of_accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE journal_entries ENABLE ROW LEVEL SECURITY;
ALTER TABLE journal_lines ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounts_receivable ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounts_payable ENABLE ROW LEVEL SECURITY;
ALTER TABLE fiscal_periods ENABLE ROW LEVEL SECURITY;
ALTER TABLE account_balances_daily ENABLE ROW LEVEL SECURITY;

-- Create policies
CREATE POLICY tenant_isolation_coa ON chart_of_accounts
    USING (tenant_id = current_setting('app.tenant_id')::uuid);

CREATE POLICY tenant_isolation_journal ON journal_entries
    USING (tenant_id = current_setting('app.tenant_id')::uuid);

CREATE POLICY tenant_isolation_ar ON accounts_receivable
    USING (tenant_id = current_setting('app.tenant_id')::uuid);

CREATE POLICY tenant_isolation_ap ON accounts_payable
    USING (tenant_id = current_setting('app.tenant_id')::uuid);

CREATE POLICY tenant_isolation_fiscal ON fiscal_periods
    USING (tenant_id = current_setting('app.tenant_id')::uuid);

CREATE POLICY tenant_isolation_balances ON account_balances_daily
    USING (tenant_id = current_setting('app.tenant_id')::uuid);
```

---

## 5. Service Architecture

### 5.1 Directory Structure

```
accounting_kernel/
├── __init__.py
├── config.py
├── constants.py
│
├── services/
│   ├── __init__.py
│   ├── coa_service.py           # Chart of Accounts
│   ├── journal_service.py       # Journal Entry (CORE)
│   ├── ledger_service.py        # General Ledger (READ)
│   ├── ar_service.py            # Accounts Receivable
│   ├── ap_service.py            # Accounts Payable
│   ├── cash_service.py          # Cash Movement
│   └── period_service.py        # Fiscal Period
│
├── posting/
│   ├── __init__.py
│   ├── base_posting.py          # Abstract base
│   ├── invoice_posting.py
│   ├── bill_posting.py
│   ├── payment_received_posting.py
│   ├── bill_payment_posting.py
│   ├── pos_posting.py
│   └── adjustment_posting.py
│
├── reports/
│   ├── __init__.py
│   ├── trial_balance.py
│   ├── profit_loss.py
│   ├── balance_sheet.py
│   ├── cash_flow.py
│   ├── ar_aging.py
│   └── ap_aging.py
│
├── consumers/
│   ├── __init__.py
│   ├── base_consumer.py
│   ├── invoice_consumer.py
│   ├── bill_consumer.py
│   └── payment_consumer.py
│
├── validators/
│   ├── __init__.py
│   ├── double_entry_validator.py
│   ├── period_validator.py
│   └── account_validator.py
│
├── models/
│   ├── __init__.py
│   ├── coa.py
│   ├── journal.py
│   ├── ar.py
│   ├── ap.py
│   └── fiscal_period.py
│
├── repositories/
│   ├── __init__.py
│   ├── coa_repository.py
│   ├── journal_repository.py
│   ├── ar_repository.py
│   ├── ap_repository.py
│   └── balance_repository.py
│
├── grpc/
│   ├── __init__.py
│   ├── protos/
│   │   ├── accounting.proto
│   │   ├── journal.proto
│   │   └── ledger.proto
│   └── servicers/
│       ├── journal_servicer.py
│       └── ledger_servicer.py
│
└── utils/
    ├── __init__.py
    ├── number_generator.py
    ├── date_utils.py
    └── currency_utils.py
```

### 5.2 Service Responsibilities

| Service | Responsibility |
|---------|----------------|
| **CoA Service** | CRUD Chart of Accounts, resolve by code |
| **Journal Service** | Create/void journal entries, validate double-entry |
| **Ledger Service** | Read-only ledger queries, trial balance |
| **AR Service** | Manage receivables, apply payments, aging |
| **AP Service** | Manage payables, apply payments, aging |
| **Cash Service** | Track cash movements |
| **Period Service** | Fiscal period management, close period |

---

## 6. Core Interfaces

### 6.1 Journal Service (gRPC)

```protobuf
syntax = "proto3";

package milkyhoop.accounting.v1;

service JournalService {
    rpc CreateJournal(CreateJournalRequest) returns (JournalResponse);
    rpc GetJournal(GetJournalRequest) returns (JournalResponse);
    rpc VoidJournal(VoidJournalRequest) returns (JournalResponse);
    rpc ListJournals(ListJournalsRequest) returns (ListJournalsResponse);
}

message CreateJournalRequest {
    string tenant_id = 1;
    string journal_date = 2;
    string description = 3;
    string source_type = 4;
    string source_id = 5;
    string trace_id = 6;
    string posted_by = 7;
    bytes source_snapshot = 8;
    repeated JournalLineInput lines = 9;
}

message JournalLineInput {
    string account_code = 1;
    string description = 2;
    double debit = 3;
    double credit = 4;
    optional string department_id = 5;
    optional string project_id = 6;
}

message JournalResponse {
    string journal_id = 1;
    string journal_number = 2;
    string status = 3;
    string message = 4;
    bool is_duplicate = 5;
}

message VoidJournalRequest {
    string tenant_id = 1;
    string journal_id = 2;
    string voided_by = 3;
    string reason = 4;
}
```

### 6.2 Ledger Service (gRPC)

```protobuf
service LedgerService {
    rpc GetTrialBalance(TrialBalanceRequest) returns (TrialBalanceResponse);
    rpc GetAccountLedger(AccountLedgerRequest) returns (AccountLedgerResponse);
    rpc GetAccountBalance(AccountBalanceRequest) returns (AccountBalanceResponse);
}

message TrialBalanceRequest {
    string tenant_id = 1;
    string as_of_date = 2;
}

message TrialBalanceResponse {
    repeated TrialBalanceRow rows = 1;
    double total_debit = 2;
    double total_credit = 3;
    bool is_balanced = 4;
}

message TrialBalanceRow {
    string account_code = 1;
    string account_name = 2;
    string account_type = 3;
    double debit = 4;
    double credit = 5;
    double balance = 6;
}
```

---

## 7. Auto-Posting Rules

### 7.1 Invoice → Journal

```python
class InvoicePosting(BasePosting):
    """
    Invoice creates:
    - Debit: Accounts Receivable (Piutang)
    - Credit: Sales Revenue (Pendapatan)
    - Credit: PPN Keluaran (if taxable)
    """

    def post(self, invoice: Invoice) -> JournalEntry:
        lines = []

        # Debit: AR (total including tax)
        lines.append(JournalLine(
            account_code=self.resolve_ar_account(invoice.customer_id),
            debit=invoice.grand_total,
            credit=Decimal('0'),
            description=f"Piutang - {invoice.customer_name}"
        ))

        # Credit: Revenue (subtotal after discount)
        lines.append(JournalLine(
            account_code=self.config.SALES_REVENUE_ACCOUNT,  # "4-10100"
            debit=Decimal('0'),
            credit=invoice.subtotal_after_discount,
            description=f"Penjualan - {invoice.number}"
        ))

        # Credit: PPN Keluaran (if applicable)
        if invoice.tax_amount > 0:
            lines.append(JournalLine(
                account_code=self.config.VAT_OUTPUT_ACCOUNT,  # "2-10400"
                debit=Decimal('0'),
                credit=invoice.tax_amount,
                description=f"PPN Keluaran - {invoice.number}"
            ))

        return self.journal_service.create_journal(
            tenant_id=invoice.tenant_id,
            journal_date=invoice.date,
            description=f"Invoice {invoice.number} - {invoice.customer_name}",
            source_type="INVOICE",
            source_id=invoice.id,
            trace_id=f"INV-{invoice.id}",
            posted_by=invoice.created_by,
            source_snapshot=invoice.to_dict(),
            lines=lines
        )
```

### 7.2 Bill → Journal

```python
class BillPosting(BasePosting):
    """
    Bill creates:
    - Debit: Inventory/Expense per item
    - Debit: PPN Masukan (if taxable)
    - Credit: Accounts Payable (Hutang)
    """

    def post(self, bill: Bill) -> JournalEntry:
        lines = []

        # Debit: Inventory/Expense per item
        for item in bill.items:
            lines.append(JournalLine(
                account_code=self.resolve_item_account(item),
                debit=item.subtotal_after_discount,
                credit=Decimal('0'),
                description=f"{item.product_name} x {item.quantity}"
            ))

        # Debit: PPN Masukan (if applicable)
        if bill.tax_amount > 0:
            lines.append(JournalLine(
                account_code=self.config.VAT_INPUT_ACCOUNT,  # "1-10500"
                debit=bill.tax_amount,
                credit=Decimal('0'),
                description=f"PPN Masukan - {bill.number}"
            ))

        # Credit: AP (total)
        lines.append(JournalLine(
            account_code=self.resolve_ap_account(bill.supplier_id),
            debit=Decimal('0'),
            credit=bill.grand_total,
            description=f"Hutang - {bill.supplier_name}"
        ))

        return self.journal_service.create_journal(...)
```

### 7.3 Payment Received → Journal

```python
class PaymentReceivedPosting(BasePosting):
    """
    Payment received creates:
    - Debit: Cash/Bank
    - Credit: Accounts Receivable
    """

    def post(self, payment: Payment) -> JournalEntry:
        lines = [
            JournalLine(
                account_code=self.resolve_cash_account(payment.payment_method),
                debit=payment.amount,
                credit=Decimal('0'),
                description=f"Terima pembayaran - {payment.customer_name}"
            ),
            JournalLine(
                account_code=self.resolve_ar_account(payment.customer_id),
                debit=Decimal('0'),
                credit=payment.amount,
                description=f"Pelunasan piutang - {payment.invoice_numbers}"
            )
        ]

        return self.journal_service.create_journal(...)
```

### 7.4 Account Resolution Logic

```python
class AccountResolver:
    """Resolve account codes based on context"""

    def resolve_cash_account(self, payment_method: str) -> str:
        mapping = {
            "CASH": "1-10100",      # Kas
            "TRANSFER": "1-10200",   # Bank
            "GIRO": "1-10200",
            "QRIS": "1-10200",
        }
        return mapping.get(payment_method, "1-10100")

    def resolve_item_account(self, item: BillItem) -> str:
        if item.is_inventory:
            return "1-10400"  # Persediaan
        else:
            return item.expense_account or "5-10100"  # HPP default
```

---

## 8. Default Chart of Accounts

### 8.1 Standard Indonesia CoA

```python
DEFAULT_COA_INDONESIA = [
    # ═══════════════════════════════════════════════════════════
    # ASSETS (1-xxxxx) - Normal: DEBIT
    # ═══════════════════════════════════════════════════════════
    {"code": "1-00000", "name": "ASET", "type": "ASSET", "normal": "DEBIT", "parent": None, "is_system": True},

    # Current Assets
    {"code": "1-10000", "name": "Aset Lancar", "type": "ASSET", "normal": "DEBIT", "parent": "1-00000"},
    {"code": "1-10100", "name": "Kas", "type": "ASSET", "normal": "DEBIT", "parent": "1-10000", "is_system": True},
    {"code": "1-10200", "name": "Bank", "type": "ASSET", "normal": "DEBIT", "parent": "1-10000"},
    {"code": "1-10201", "name": "Bank BCA", "type": "ASSET", "normal": "DEBIT", "parent": "1-10200"},
    {"code": "1-10202", "name": "Bank Mandiri", "type": "ASSET", "normal": "DEBIT", "parent": "1-10200"},
    {"code": "1-10300", "name": "Piutang Usaha", "type": "ASSET", "normal": "DEBIT", "parent": "1-10000", "is_system": True},
    {"code": "1-10400", "name": "Persediaan Barang", "type": "ASSET", "normal": "DEBIT", "parent": "1-10000", "is_system": True},
    {"code": "1-10500", "name": "PPN Masukan", "type": "ASSET", "normal": "DEBIT", "parent": "1-10000"},
    {"code": "1-10600", "name": "Biaya Dibayar Dimuka", "type": "ASSET", "normal": "DEBIT", "parent": "1-10000"},

    # Fixed Assets
    {"code": "1-20000", "name": "Aset Tetap", "type": "ASSET", "normal": "DEBIT", "parent": "1-00000"},
    {"code": "1-20100", "name": "Peralatan", "type": "ASSET", "normal": "DEBIT", "parent": "1-20000"},
    {"code": "1-20200", "name": "Kendaraan", "type": "ASSET", "normal": "DEBIT", "parent": "1-20000"},
    {"code": "1-20900", "name": "Akum. Penyusutan", "type": "ASSET", "normal": "CREDIT", "parent": "1-20000"},

    # ═══════════════════════════════════════════════════════════
    # LIABILITIES (2-xxxxx) - Normal: CREDIT
    # ═══════════════════════════════════════════════════════════
    {"code": "2-00000", "name": "KEWAJIBAN", "type": "LIABILITY", "normal": "CREDIT", "parent": None, "is_system": True},

    # Current Liabilities
    {"code": "2-10000", "name": "Kewajiban Lancar", "type": "LIABILITY", "normal": "CREDIT", "parent": "2-00000"},
    {"code": "2-10100", "name": "Hutang Usaha", "type": "LIABILITY", "normal": "CREDIT", "parent": "2-10000", "is_system": True},
    {"code": "2-10200", "name": "Hutang Bank", "type": "LIABILITY", "normal": "CREDIT", "parent": "2-10000"},
    {"code": "2-10300", "name": "Hutang Gaji", "type": "LIABILITY", "normal": "CREDIT", "parent": "2-10000"},
    {"code": "2-10400", "name": "PPN Keluaran", "type": "LIABILITY", "normal": "CREDIT", "parent": "2-10000"},
    {"code": "2-10500", "name": "Hutang Pajak", "type": "LIABILITY", "normal": "CREDIT", "parent": "2-10000"},

    # Long-term Liabilities
    {"code": "2-20000", "name": "Kewajiban Jangka Panjang", "type": "LIABILITY", "normal": "CREDIT", "parent": "2-00000"},
    {"code": "2-20100", "name": "Hutang Bank Jk. Panjang", "type": "LIABILITY", "normal": "CREDIT", "parent": "2-20000"},

    # ═══════════════════════════════════════════════════════════
    # EQUITY (3-xxxxx) - Normal: CREDIT
    # ═══════════════════════════════════════════════════════════
    {"code": "3-00000", "name": "MODAL", "type": "EQUITY", "normal": "CREDIT", "parent": None, "is_system": True},
    {"code": "3-10000", "name": "Modal Disetor", "type": "EQUITY", "normal": "CREDIT", "parent": "3-00000"},
    {"code": "3-20000", "name": "Laba Ditahan", "type": "EQUITY", "normal": "CREDIT", "parent": "3-00000", "is_system": True},
    {"code": "3-30000", "name": "Laba Tahun Berjalan", "type": "EQUITY", "normal": "CREDIT", "parent": "3-00000", "is_system": True},
    {"code": "3-40000", "name": "Prive", "type": "EQUITY", "normal": "DEBIT", "parent": "3-00000"},

    # ═══════════════════════════════════════════════════════════
    # INCOME (4-xxxxx) - Normal: CREDIT
    # ═══════════════════════════════════════════════════════════
    {"code": "4-00000", "name": "PENDAPATAN", "type": "INCOME", "normal": "CREDIT", "parent": None, "is_system": True},
    {"code": "4-10000", "name": "Pendapatan Usaha", "type": "INCOME", "normal": "CREDIT", "parent": "4-00000"},
    {"code": "4-10100", "name": "Penjualan", "type": "INCOME", "normal": "CREDIT", "parent": "4-10000", "is_system": True},
    {"code": "4-10200", "name": "Diskon Penjualan", "type": "INCOME", "normal": "DEBIT", "parent": "4-10000"},
    {"code": "4-10300", "name": "Retur Penjualan", "type": "INCOME", "normal": "DEBIT", "parent": "4-10000"},
    {"code": "4-20000", "name": "Pendapatan Lain-lain", "type": "INCOME", "normal": "CREDIT", "parent": "4-00000"},

    # ═══════════════════════════════════════════════════════════
    # EXPENSES (5-xxxxx) - Normal: DEBIT
    # ═══════════════════════════════════════════════════════════
    {"code": "5-00000", "name": "BEBAN", "type": "EXPENSE", "normal": "DEBIT", "parent": None, "is_system": True},

    # Cost of Goods Sold
    {"code": "5-10000", "name": "Harga Pokok Penjualan", "type": "EXPENSE", "normal": "DEBIT", "parent": "5-00000"},
    {"code": "5-10100", "name": "HPP Barang Dagang", "type": "EXPENSE", "normal": "DEBIT", "parent": "5-10000", "is_system": True},
    {"code": "5-10200", "name": "Diskon Pembelian", "type": "EXPENSE", "normal": "CREDIT", "parent": "5-10000"},
    {"code": "5-10300", "name": "Retur Pembelian", "type": "EXPENSE", "normal": "CREDIT", "parent": "5-10000"},

    # Operating Expenses
    {"code": "5-20000", "name": "Beban Operasional", "type": "EXPENSE", "normal": "DEBIT", "parent": "5-00000"},
    {"code": "5-20100", "name": "Beban Gaji", "type": "EXPENSE", "normal": "DEBIT", "parent": "5-20000"},
    {"code": "5-20200", "name": "Beban Sewa", "type": "EXPENSE", "normal": "DEBIT", "parent": "5-20000"},
    {"code": "5-20300", "name": "Beban Listrik & Air", "type": "EXPENSE", "normal": "DEBIT", "parent": "5-20000"},
    {"code": "5-20400", "name": "Beban Telepon & Internet", "type": "EXPENSE", "normal": "DEBIT", "parent": "5-20000"},
    {"code": "5-20500", "name": "Beban Pengiriman", "type": "EXPENSE", "normal": "DEBIT", "parent": "5-20000"},
    {"code": "5-20600", "name": "Beban Perlengkapan", "type": "EXPENSE", "normal": "DEBIT", "parent": "5-20000"},
    {"code": "5-20700", "name": "Beban Penyusutan", "type": "EXPENSE", "normal": "DEBIT", "parent": "5-20000"},
    {"code": "5-20800", "name": "Beban Administrasi", "type": "EXPENSE", "normal": "DEBIT", "parent": "5-20000"},
    {"code": "5-20900", "name": "Beban Lain-lain", "type": "EXPENSE", "normal": "DEBIT", "parent": "5-20000"},

    # Non-Operating Expenses
    {"code": "5-30000", "name": "Beban Non-Operasional", "type": "EXPENSE", "normal": "DEBIT", "parent": "5-00000"},
    {"code": "5-30100", "name": "Beban Bunga", "type": "EXPENSE", "normal": "DEBIT", "parent": "5-30000"},
    {"code": "5-30200", "name": "Beban Pajak", "type": "EXPENSE", "normal": "DEBIT", "parent": "5-30000"},
]
```

---

## 9. Report Generation

### 9.1 Trial Balance

```python
class TrialBalanceReport:
    """
    Trial Balance = Sum all debit/credit per account
    Must always be balanced (total debit = total credit)
    """

    def generate(self, tenant_id: str, as_of_date: date) -> TrialBalance:
        query = """
            SELECT
                c.code,
                c.name,
                c.type,
                c.normal_balance,
                COALESCE(SUM(jl.debit), 0) as total_debit,
                COALESCE(SUM(jl.credit), 0) as total_credit,
                CASE
                    WHEN c.normal_balance = 'DEBIT'
                    THEN COALESCE(SUM(jl.debit), 0) - COALESCE(SUM(jl.credit), 0)
                    ELSE COALESCE(SUM(jl.credit), 0) - COALESCE(SUM(jl.debit), 0)
                END as balance
            FROM chart_of_accounts c
            LEFT JOIN journal_lines jl ON jl.account_id = c.id
            LEFT JOIN journal_entries je ON je.id = jl.journal_id
                AND je.journal_date <= :as_of_date
                AND je.status = 'POSTED'
            WHERE c.tenant_id = :tenant_id
                AND c.is_active = true
            GROUP BY c.id, c.code, c.name, c.type, c.normal_balance
            HAVING COALESCE(SUM(jl.debit), 0) != 0
                OR COALESCE(SUM(jl.credit), 0) != 0
            ORDER BY c.code
        """

        rows = self.db.execute(query, {
            "tenant_id": tenant_id,
            "as_of_date": as_of_date
        }).fetchall()

        total_debit = sum(r.total_debit for r in rows)
        total_credit = sum(r.total_credit for r in rows)

        return TrialBalance(
            as_of_date=as_of_date,
            rows=rows,
            total_debit=total_debit,
            total_credit=total_credit,
            is_balanced=(total_debit == total_credit)
        )
```

### 9.2 Profit & Loss

```python
class ProfitLossReport:
    """
    P&L = Income - Expense for a period
    """

    def generate(self, tenant_id: str, start_date: date, end_date: date) -> ProfitLoss:
        # Income accounts query
        income_query = """
            SELECT
                c.code, c.name,
                COALESCE(SUM(jl.credit), 0) - COALESCE(SUM(jl.debit), 0) as amount
            FROM chart_of_accounts c
            LEFT JOIN journal_lines jl ON jl.account_id = c.id
            LEFT JOIN journal_entries je ON je.id = jl.journal_id
                AND je.journal_date BETWEEN :start_date AND :end_date
                AND je.status = 'POSTED'
            WHERE c.tenant_id = :tenant_id AND c.type = 'INCOME' AND c.is_active = true
            GROUP BY c.id ORDER BY c.code
        """

        # Expense accounts query (similar structure)
        ...

        return ProfitLoss(
            period_start=start_date,
            period_end=end_date,
            income=income_rows,
            expenses=expense_rows,
            total_income=total_income,
            total_expense=total_expense,
            net_income=total_income - total_expense
        )
```

### 9.3 Balance Sheet

```python
class BalanceSheetReport:
    """
    Balance Sheet: Assets = Liabilities + Equity
    Point-in-time snapshot
    """

    def generate(self, tenant_id: str, as_of_date: date) -> BalanceSheet:
        # Query accounts with balances by type
        ...

        # Add current period net income to equity
        pnl = self.get_ytd_net_income(tenant_id, as_of_date)
        total_equity += pnl

        return BalanceSheet(
            as_of_date=as_of_date,
            assets=assets,
            liabilities=liabilities,
            equity=equity,
            total_assets=total_assets,
            total_liabilities=total_liabilities,
            total_equity=total_equity,
            is_balanced=(total_assets == total_liabilities + total_equity)
        )
```

### 9.4 AR/AP Aging

```python
class AgingReport:
    """
    Aging report for AR or AP
    Buckets: Current, 1-30, 31-60, 61-90, 90+
    """

    def generate_ar_aging(self, tenant_id: str, as_of_date: date) -> AgingReport:
        query = """
            SELECT
                ar.customer_id, c.name as customer_name,
                ar.source_number, ar.due_date, ar.balance,
                CASE
                    WHEN ar.due_date >= :as_of_date THEN 'CURRENT'
                    WHEN :as_of_date - ar.due_date BETWEEN 1 AND 30 THEN '1-30'
                    WHEN :as_of_date - ar.due_date BETWEEN 31 AND 60 THEN '31-60'
                    WHEN :as_of_date - ar.due_date BETWEEN 61 AND 90 THEN '61-90'
                    ELSE '90+'
                END as bucket
            FROM accounts_receivable ar
            JOIN customers c ON c.id = ar.customer_id
            WHERE ar.tenant_id = :tenant_id
                AND ar.status IN ('OPEN', 'PARTIAL')
            ORDER BY ar.due_date
        """
        ...
```

---

## 10. Event Flow & Integration

### 10.1 Kafka Topics

| Topic | Producer | Consumer | Purpose |
|-------|----------|----------|---------|
| `invoice.created` | Invoice Service | Accounting | Create AR + Journal |
| `invoice.voided` | Invoice Service | Accounting | Void AR + Reverse Journal |
| `bill.created` | Bill Service | Accounting | Create AP + Journal |
| `payment.received` | Payment Service | Accounting | Apply AR + Journal |
| `payment.bill` | Payment Service | Accounting | Apply AP + Journal |
| `pos.completed` | POS Service | Accounting | Create Revenue Journal |
| `accounting.journal.posted` | Accounting | Reports, Audit | Downstream processing |

### 10.2 Outbox Pattern

```python
class OutboxPublisher:
    def publish_in_transaction(self, tx, event_type: str, key: str, payload: dict):
        tx.execute("""
            INSERT INTO accounting_outbox (tenant_id, event_type, event_key, payload)
            VALUES (:tenant_id, :event_type, :event_key, :payload)
        """, {...})

class OutboxPoller:
    async def poll_and_publish(self):
        while True:
            events = self.db.execute("""
                SELECT id, event_type, event_key, payload
                FROM accounting_outbox
                WHERE is_published = false
                ORDER BY created_at LIMIT 100
                FOR UPDATE SKIP LOCKED
            """).fetchall()

            for event in events:
                await self.kafka.send(...)
                self.db.execute("UPDATE accounting_outbox SET is_published = true...")

            await asyncio.sleep(1)
```

---

## 11. Consumer-Safe Implementation

### 11.1 Idempotent Journal Creation

```python
def create_journal_consumer_safe(event: dict) -> str:
    """Exactly-once semantics via ON CONFLICT"""

    with db.transaction() as tx:
        # Set tenant context for RLS
        tx.execute("SET app.tenant_id = :tenant_id", {...})

        # Idempotent insert via ON CONFLICT
        result = tx.execute("""
            INSERT INTO journal_entries (...)
            VALUES (...)
            ON CONFLICT (tenant_id, trace_id, journal_date) DO NOTHING
            RETURNING id, journal_number
        """, {...})

        if result.rowcount == 0:
            # Duplicate - return existing
            existing = tx.execute("""
                SELECT id, journal_number FROM journal_entries
                WHERE tenant_id = :tenant_id AND trace_id = :trace_id
            """, {...}).fetchone()
            return JournalResult(is_duplicate=True, ...)

        # New journal - insert lines
        for line in lines:
            tx.execute("INSERT INTO journal_lines (...) VALUES (...)", {...})

        # Validate double-entry
        validate_double_entry(tx, journal_id)

        return JournalResult(is_duplicate=False, ...)
```

---

## 12. Scalability & Performance

### 12.1 Performance Targets

| Metric | Target | Strategy |
|--------|--------|----------|
| Journal creation | < 50ms | Batch insert lines, async outbox |
| Trial Balance | < 200ms | Daily balance cache |
| P&L Report | < 500ms | Aggregate from cached balances |
| AR Aging | < 100ms | Indexed due_date |

### 12.2 Partitioning Strategy

- Journal entries partitioned by `journal_date` (monthly)
- Auto-create partitions 3 months ahead
- Archive old partitions to cold storage

### 12.3 Read Model (CQRS)

- `account_balances_daily` updated async after journal posted
- Reports read from cached balances, not raw journal lines
- Balance recalculation triggered by background worker

---

## 13. Security & Tenant Isolation

### 13.1 RLS Implementation

```sql
-- All tables have RLS enabled
-- Policies require app.tenant_id session variable
CREATE POLICY tenant_isolation ON table_name
    USING (tenant_id = current_setting('app.tenant_id')::uuid);
```

### 13.2 API Security

- JWT validation required for all endpoints
- tenant_id from JWT must match request tenant_id
- Audit log for all write operations

---

## 14. Operational Considerations

### 14.1 Monitoring

- Journal creation rate per tenant
- Double-entry validation failures
- Outbox lag (unpublished events)
- Balance reconciliation errors

### 14.2 Backup Strategy

- PostgreSQL WAL streaming
- Daily logical backups
- Point-in-time recovery capability

---

## 15. Validation Checklist

Before go-live:

- [ ] Trial Balance always balanced after each journal
- [ ] Duplicate events produce same result (idempotency)
- [ ] RLS prevents cross-tenant access
- [ ] Journal void creates reversing entry
- [ ] Reports derive from ledger (not source tables)
- [ ] Fiscal period close locks prior journals

---

## 16. Tech Stack

| Component | Technology |
|-----------|------------|
| Language | Python 3.11+ |
| Framework | FastAPI + gRPC |
| Database | PostgreSQL 15+ |
| ORM | SQLAlchemy 2.0 (async) |
| Message Queue | Kafka |
| Cache | Redis |
| Containerization | Docker |

---

## 17. Implementation Roadmap

### Phase 1: Foundation (Week 1-2)
- Database schema migration
- CoA Service + Default accounts
- Journal Service (create, void)
- Double-entry validator

### Phase 2: Core Accounting (Week 3-4)
- Ledger Service (trial balance, account ledger)
- Auto-posting rules (Invoice, Bill, Payment)
- Event consumers (Kafka)

### Phase 3: Subledger (Week 5-6)
- AR Service + Payment application
- AP Service + Bill payment
- Aging reports

### Phase 4: Reporting (Week 7-8)
- P&L Report
- Balance Sheet
- Cash Flow Statement
- Period closing

### Phase 5: Integration (Week 9-10)
- Connect existing transaction flow
- Migrate historical data
- Performance testing
- Go-live

---

*End of Specification*
