# ACCOUNTING IRON LAWS — Compacted v3.5

**Konstitusi Backend Pure Ledger Accounting System MilkyHoop**
Status: 31/31 Laws Ratified | Updated: 2026-03-02

---

## Prinsip Dasar

> Tujuan utama: **kebenaran finansial, bukan kenyamanan pengguna.**
> Konflik UX vs Accounting Truth → Accounting Truth SELALU menang.

### Production Column Names

| Tabel | Column (ACTUAL) | BUKAN |
|-------|-----------------|-------|
| `journal_entries` | `journal_number`, `journal_date` | ~~number~~, ~~posting_date~~ |
| `journal_lines` | `journal_id` | ~~journal_entry_id~~ |
| `chart_of_accounts` | `account_code` | ~~code~~ |
| `warehouse_stock` | `item_id` | ~~product_id~~ |

---

## Laws 0–15: Core Foundation

### Law 0 — Separation of Concerns

| Layer | Tanggung Jawab | Constraint |
|-------|---------------|------------|
| LLM | Planning & Narration | TIDAK PERNAH write data |
| Kernel | Writes, validation, journal gen | Single gateway mutasi |
| Ledger | Append-only storage | Immutable setelah posted |
| Reports | Read-only dari ledger | Tidak ada data source lain |

```python
# ALLOWED: LLM → ActionPlan → Kernel executes
# FORBIDDEN: llm.insert_journal(), service.bypass_kernel()
```

### Law 1 — Ledger Supremacy

Semua laporan, saldo, analitik → derive dari `journal_entries` + `journal_lines`. Tidak ada tabel saldo authoritative. Tidak ada balance override.

```sql
-- ✅ SELECT SUM(debit) - SUM(credit) FROM journal_lines WHERE account_id = $1
-- ❌ UPDATE accounts SET balance = 1000000
-- ❌ SELECT current_balance FROM bank_accounts  (DEPRECATED v3.5)
```

### Law 2 — Journal Immutability

Posted journal: **no EDIT, no DELETE, only REVERSAL.** Enforced via DB trigger `trg_journal_immutable` yang block UPDATE/DELETE pada `journal_entries` WHERE `status='POSTED'`.

### Law 3 — Append-Only

Semua perubahan = INSERT journal baru. No UPDATE saldo, no PATCH angka. REVOKE UPDATE/DELETE on journal tables from app user.

### Law 4 — Double-Entry

`CHECK (total_debit = total_credit)` on `journal_entries`. Application-level validation sebelum INSERT. Tidak ada pengecualian.

### Law 5 — Period Lock

Closed period = immutable history. DB trigger `trg_check_period` block INSERT ke `journal_entries` jika period bukan OPEN. Tidak bisa post, edit, atau insert ke closed period.

### Law 6 — Source Traceability

`source_type` dan `source_id` NOT NULL di `journal_entries`. Extensible via `journal_source_types` reference table (INSERT new type, no schema change).

Registered source types: INVOICE, SALES_INVOICE_COGS, CASH_SALE, INVOICE_REVERSAL, BILL, PAYMENT_RECEIVED, PAYMENT_MADE, PAYMENT_BILL, EXPENSE, BANK_TRANSACTION, BANK_TRANSFER, RECONCILIATION_ADJUSTMENT, STOCK_ADJUSTMENT, MANUAL, ADJUSTMENT, REVERSAL, CLOSING, REVALUATION, OPENING, dll.

### Law 7 — No Balance Override

Tidak ada endpoint PUT/PATCH/POST `/accounts/{id}/balance` atau `/accounts/{id}/adjust`. Semua koreksi via reversal/adjusting journal. `bank_accounts.current_balance` cache DEPRECATED (v3.5).

### Law 8 — No Silent Mutation

Tidak ada background patch, hidden recalculation, atau cron yang ubah angka tanpa jurnal. FX adjustment → explicit revaluation journal.

### Law 9 — Deterministic Reporting

Query sama + parameter sama = hasil sama. Tidak ada floating-point untuk currency. Gunakan `Decimal` + `ROUND_HALF_UP`.

### Law 10 — AI Safety Boundary

LLM output = `ActionPlan` object only. Kernel yang execute. LLM tidak boleh output SQL, direct journal, atau balance calculation.

### Law 11 — Event-Sourced Financial State

Ledger replay → state IDENTIK. Semua derived state (balance, reports) rebuildable dari journal. Point-in-time reconstruction dimungkinkan.

### Law 12 — Audit Trail Immutability

Audit log append-only + checksum SHA-256. Trigger `trg_audit_immutable` block UPDATE/DELETE on `audit_logs`.

### Law 13 — Concurrency Safety

`pg_advisory_xact_lock(hashtext($1))` di semua 69 journal-creating paths (23 router files). Pattern: `{SOURCE_TYPE}:{entity_id}`. Tidak ada race condition, double-spend, atau duplicate posting.

### Law 14 — Idempotency

`idempotency_keys` table (V113). UNIQUE(tenant_id, idempotency_key). Safe untuk retry, webhook replay, AI agent retry.

### Law 15 — Disaster Recovery

Daily health check (06:00 UTC): 12 checks — journal balance, hash chain, bank sync, inventory, sequence, orphans, AR/AP invariant, COGS, opening balance, negative balance. Monthly restore test (1st, 03:00 UTC): decrypt, restore, verify trial balance + chain.

---

## Laws 16–21: Pure Ledger Architecture

### Law 16 — Pure Ledger Cross-Verification

**Semua angka derive dari `journal_entries` + `journal_lines`.** Dashboard, laporan, aging, saldo akun — semuanya.

| ❌ SILENT VIOLATION | ✅ PURE LEDGER |
|---------------------|----------------|
| `SELECT SUM(outstanding) FROM invoices` | `SUM(debit) - SUM(credit) FROM journal_lines WHERE account_id = $ar` |
| `SELECT balance FROM accounts` | `SUM(debit) - SUM(credit) FROM journal_lines WHERE status = 'POSTED'` |
| `SELECT current_balance FROM bank_accounts` | `compute_bank_balance()` from journal_lines |
| Dashboard baca dari tabel transaksi | Dashboard baca dari journal_lines |

**In-transaction cache** (contoh: `bills.amount_paid`) diperbolehkan untuk write-side validation SELAMA:
1. Update dalam transaksi yang sama dengan journal posting
2. READ path (dashboard, report, bot) tetap WAJIB dari journal_lines
3. Cache TIDAK PERNAH jadi source untuk reporting/display

**Bank balance cache (`bank_accounts.current_balance`) DEPRECATED as of v3.5.** Use `compute_bank_balance()` helper for all read paths. Column retained but no longer written to or read from.

### Law 17 — Currency Snapshot Immutability

Journal lines wajib: `currency_code CHAR(3)`, `exchange_rate DECIMAL(18,6)`, `amount_base DECIMAL(18,2)`. Immutable setelah posting. Revaluation = journal baru (`source_type = 'REVALUATION'`), bukan override.

### Law 18 — CoA Structural Integrity

`account_type`, `normal_balance`, `is_header` immutable jika sudah ada `journal_lines`. Trigger `trg_coa_structural_integrity` block perubahan. Koreksi: deprecate account lama → buat baru → reclassification journal.

### Law 19 — Source Object Financial Freeze

Field amount di invoices, bills, expenses, payments → frozen setelah journal POSTED. 5 triggers: `trg_invoice_freeze`, `trg_bill_freeze`, `trg_expense_freeze`, `trg_receive_payment_freeze`, `trg_bill_payment_freeze`. Edit = reversal + dokumen baru.

### Law 20 — Cryptographic Ledger Chaining

SHA-256 hash per journal entry: `content_hash = SHA256(content + previous_hash)`. Per-tenant chain. Trigger `assign_hash_and_sequence()`. Verification via `verify_chain_integrity()`. Requires DRAFT→lines→UPDATE POSTED pattern.

### Law 21 — Materialized View Safety

Materialized view = **read cache, bukan source of truth**. Harus rebuildable 100% dari journal. Refresh event-driven (on journal post). Fallback guarantee: jika view rusak, hitung langsung dari ledger.

**`bank_accounts.current_balance` cache DEPRECATED (v3.5).** Proven stale (BCA gap 37M, Batch 1). Use `compute_bank_balance()` journal helper instead.

---

## Laws 22–28: Hardening Layer

### Law 22 — Sequence Integrity

`chain_sequence` strictly monotonic per tenant, no gaps/duplicates. UNIQUE INDEX `idx_journal_sequence_uniq` on `(tenant_id, chain_sequence)`. Part of `assign_hash_and_sequence()` trigger.

### Law 23 — Transaction Atomicity

Journal posting WAJIB atomic: entry + lines + hash + sequence → commit atau rollback bersama. 64 posting paths across 27 router files use DRAFT→lines→UPDATE POSTED dalam single transaction.

### Law 24 — Tenant Isolation

RLS on 222 tables, 227 policies. `current_setting('app.tenant_id')`. `milkyadmin` role = NOBYPASSRLS. Middleware `rls_context.py` auto-set via contextvars. FK constraints include tenant_id.

### Law 25 — Numeric Precision

`DECIMAL(18,2)` untuk amount. `DECIMAL(18,6)` untuk exchange rate. 323 columns standardized. Tidak ada float, double, atau variable precision di financial paths.

### Law 26 — Reversal Uniqueness

Max 1 reversal per original journal. Trigger `trg_prevent_reverse_of_reversal`. Unique index `idx_je_single_reversal`. Tidak ada reverse-reversal chains.

### Law 27 — CoA Runtime Resolution

53 hardcoded account codes → replaced with `resolve_account_id()`. Helper: `app/services/resolve_account.py`. Frontend fetch dari `/api/accounts`, bukan hardcoded list. Hardcode hanya boleh di migration seed.

### Law 28 — Opening Balance Integrity

Opening balance via journal (`source_type = 'OPENING'`). Hanya di periode pertama. Trigger `trg_guard_opening_balance` block OPENING journal setelah ada operational transactions. Koreksi: reverse + re-post (kedua di periode pertama).

---

## Law 29 — AR/AP Module Purity

**Modul Payment HANYA melacak settlement obligasi. Payment Module = ledger-derived projection, bukan source of truth.**

### Arsitektur

```
Source of Truth     : journal_entries + journal_lines
Metadata Wrapper    : receive_payments, bill_payments (convenience, NOT authoritative)
UI Projection       : query journal_lines WHERE account_type IN ('RECEIVABLE','PAYABLE')
                      enriched with metadata from wrapper tables
```

- Angka dari journal. Metadata dari wrapper table.
- Jika `receive_payments.amount` ≠ journal credit ke Piutang → **journal yang benar**
- AR/AP aging, outstanding → journal CTE, bukan tabel payments

### AR Settlement (Penerimaan Pembayaran)

**HANYA** transaksi yang **mengurangi** Piutang Usaha (credit RECEIVABLE):

Full payment, partial, write-off, overpayment allocation, settlement dari rekonsiliasi, opening balance settlement, legal write-off.

**PENTING — Accrual ≠ Settlement:**
- `Dr. Piutang, Cr. Pendapatan` = CREATE obligation (debit RECEIVABLE) — **bukan settlement**
- `Dr. Bank, Cr. Piutang` = SETTLEMENT (credit RECEIVABLE) — **ini yang masuk modul**
- Filter: `account_type = 'RECEIVABLE' AND credit > 0`

### AP Settlement (Pembayaran Keluar)

**HANYA** transaksi yang **mengurangi** Hutang Usaha (debit PAYABLE):

Pelunasan vendor, partial, offset retur, write-off, settlement dari rekonsiliasi, opening balance settlement, debt restructuring.

**PENTING — Accrual ≠ Settlement:**
- `Dr. Persediaan, Cr. Hutang` = CREATE obligation (credit PAYABLE) — **bukan settlement**
- `Dr. Hutang, Cr. Bank` = SETTLEMENT (debit PAYABLE) — **ini yang masuk modul**
- Filter: `account_type = 'PAYABLE' AND debit > 0`

### Obligation Reference (bukan hanya invoice_id)

```python
settlement_reference = {
    "invoice_id", "bill_id",                    # Standard
    "credit_note_id", "vendor_credit_id",       # Offset
    "customer_deposit_id", "vendor_deposit_id",  # Advance
    "opening_balance_id",                        # Migration
    "manual_adjustment_id",                      # Court/restructuring (requires review)
}
# Rule: HARUS ada salah satu. Bukan "HARUS ada invoice_id".
```

### Yang DILARANG Masuk

Beban langsung, pembelian cash tanpa AP, transfer antar bank, setoran modal, pembayaran pajak tanpa AP, gaji tanpa hutang, bunga bank, refund tanpa credit note → **semua ke Modul Kas & Bank**.

### Enforcement

```sql
-- ✅ Query settlement — derive dari journal, enrich dari wrapper
SELECT je.journal_number, je.journal_date, jl.credit AS amount,
       rp.invoice_id, rp.customer_id  -- metadata only
FROM journal_lines jl
JOIN journal_entries je ON je.id = jl.journal_id
JOIN chart_of_accounts coa ON coa.id = jl.account_id
LEFT JOIN receive_payments rp ON rp.id::text = je.source_id
WHERE coa.account_type = 'RECEIVABLE' AND jl.credit > 0
  AND je.status = 'POSTED' AND je.tenant_id = $1;

-- ❌ FORBIDDEN
SELECT SUM(amount) FROM receive_payments;  -- violates Law 1
SELECT SUM(amount) FROM bill_payments;     -- violates Law 1
```

---

## Law 30 — Obligation Existence Guard

**Settlement TIDAK BOLEH ada tanpa underlying obligation object yang eksis di database.**

### Enforcement

```sql
-- DB-level: at least one reference required
ALTER TABLE receive_payments ADD CONSTRAINT chk_rp_obligation CHECK (
  invoice_id IS NOT NULL OR credit_note_id IS NOT NULL
  OR customer_deposit_id IS NOT NULL OR manual_adjustment_reference IS NOT NULL);

ALTER TABLE bill_payments ADD CONSTRAINT chk_bp_obligation CHECK (
  bill_id IS NOT NULL OR vendor_credit_id IS NOT NULL
  OR vendor_deposit_id IS NOT NULL OR manual_adjustment_reference IS NOT NULL);
```

### Cascade Protection

```sql
-- Cannot delete obligation that has settlements
CREATE OR REPLACE FUNCTION prevent_obligation_delete_with_settlement() RETURNS TRIGGER AS $$
BEGIN
  IF TG_TABLE_NAME='sales_invoices' AND EXISTS (SELECT 1 FROM receive_payments WHERE invoice_id=OLD.id) THEN
    RAISE EXCEPTION 'Law 30: Cannot delete invoice — has settlements. Use reversal.';
  ELSIF TG_TABLE_NAME='bills' AND EXISTS (SELECT 1 FROM bill_payments WHERE bill_id=OLD.id) THEN
    RAISE EXCEPTION 'Law 30: Cannot delete bill — has settlements. Use reversal.';
  END IF;
  RETURN OLD;
END; $$ LANGUAGE plpgsql;
```

---

## Law 31 — New Route Compliance Gate

**Setiap route, endpoint, atau code path baru yang CREATE, VOID, atau MODIFY journal entries WAJIB lulus checklist ini SEBELUM merge ke production.**

### The Gate

```
┌─────────────────────────────────────────────────────────────────┐
│  LAW 31 — COMPLIANCE GATE                                       │
│  Semua code path baru yang touch journal HARUS pass 7/7         │
│                                                                  │
│  □ 1. Advisory lock        (Law 13)                             │
│  □ 2. Single transaction   (Law 23)                             │
│  □ 3. DRAFT→POSTED         (Law 20)                             │
│  □ 4. Derived layer sync   (see matrix below)                   │
│  □ 5. Reversal cascade     (void = reverse ALL layers)          │
│  □ 6. Read path = journal  (Law 1, 16)                          │
│  □ 7. CoA runtime resolve  (Law 27)                             │
│                                                                  │
│  7/7 = merge. <7/7 = block.                                     │
└─────────────────────────────────────────────────────────────────┘
```

### Gate 4 — Derived Layer Sync Matrix

| CoA yang di-touch | Derived Layer | Skill Reference |
|-------------------|---------------|-----------------|
| Bank CoA | bank_transactions | milkyhoop-banksync Rule 1 |
| RECEIVABLE | receive_payments + allocations | milkyhoop-arap Rule 1 |
| PAYABLE | bill_payments_v2 + allocations | milkyhoop-arap Rule 1 |
| Persediaan (1-10600) | inventory_ledger | milkyhoop-inventory Rule 1 |
| HPP/COGS (5-10100) | inventory_ledger | milkyhoop-inventory Rule 1 |

### Gate 5 — Reversal Cascade Matrix

| Original Operation | Journal | Bank Mirror | Inventory | Wrapper Auto-void |
|--------------------|---------|-------------|-----------|-------------------|
| Bill (inventory) | ✅ | ✅ if bank | ✅ record_inventory_reversal() | ✅ AP trigger |
| Sales Invoice (inventory) | ✅ | ✅ if bank | ✅ inline | ✅ AR trigger |
| Payment | ✅ | ✅ mirror | N/A | ✅ trigger |
| Expense (bank) | ✅ | ✅ mirror | N/A | N/A |
| Stock Adjustment | ✅ | N/A | ✅ swap | N/A |

### Companion Skill Invocation

| Jika menyentuh... | WAJIB baca |
|-------------------|-----------|
| Bank, payment, transfer | milkyhoop-banksync |
| AR/AP, settlement | milkyhoop-arap |
| Inventory, COGS, stock | milkyhoop-inventory |
| Chat, bot, agent | milkyhoop-conversational |
| Journal creation | milkyhoop-ironlaws |
| Endpoint pattern, locks | milkyhoop-endpoint |

---

## Law Summary Matrix

| # | Law | Layer | One-Liner |
|---|-----|-------|-----------|
| 0 | Separation of Concerns | Architecture | LLM plans, Kernel writes, Ledger stores |
| 1 | Ledger Supremacy | Data | All truth from journal_lines |
| 2 | Journal Immutability | Data | No edit, no delete, only reversal |
| 3 | Append-Only | Data | All changes = INSERT journal |
| 4 | Double-Entry | Validation | total_debit = total_credit always |
| 5 | Period Lock | Validation | Closed period = immutable |
| 6 | Source Traceability | Audit | source_type + source_id NOT NULL |
| 7 | No Balance Override | API | No set/adjust balance endpoint |
| 8 | No Silent Mutation | Service | No background financial changes |
| 9 | Deterministic Reporting | Reporting | Same query = same result |
| 10 | AI Safety | Integration | LLM → ActionPlan only |
| 11 | Event-Sourced State | Architecture | Replay journals = identical state |
| 12 | Audit Immutability | Audit | Audit log append-only |
| 13 | Concurrency Safety | Runtime | Advisory locks on all write paths |
| 14 | Idempotency | Runtime | Same key = same result, no dupe |
| 15 | Disaster Recovery | Operations | Daily health + monthly restore test |
| 16 | Pure Ledger X-Verify | Architecture | All views from journal_lines |
| 17 | Currency Snapshot | Data | FX rate frozen at posting |
| 18 | CoA Integrity | Data | No type change after journals exist |
| 19 | Financial Freeze | Data | Source amounts frozen after posted |
| 20 | Crypto Chaining | Audit | SHA-256 hash chain per tenant |
| 21 | MView Safety | Performance | Cache ≠ source of truth |
| 22 | Sequence Integrity | Data | Monotonic, no gap/dupe |
| 23 | Transaction Atomicity | Runtime | Entry+lines+hash = 1 txn |
| 24 | Tenant Isolation | Security | RLS on 222 tables |
| 25 | Numeric Precision | Data | DECIMAL(18,2) amounts, (18,6) FX |
| 26 | Reversal Uniqueness | Data | Max 1 reversal per journal |
| 27 | CoA Runtime Resolution | Application | No hardcoded account codes |
| 28 | Opening Balance | Data | Frozen after operational txns |
| 29 | AR/AP Module Purity | Architecture | Payment = settlement only, ledger-derived |
| 30 | Obligation Existence | Data | No settlement without obligation |
| 31 | New Route Gate | Architecture | 7-point checklist before merge |

---

## Forbidden Features

| Feature | Law Violated |
|---------|-------------|
| Edit/delete posted journal | 2 |
| Set/override balance | 7 |
| Backdate to closed period | 5 |
| Silent recalculation / bg patch | 8 |
| LLM direct DB write | 0, 10 |
| Dashboard from transaction tables | 16 |
| Override posted FX rate | 17 |
| Change account type with journals | 18 |
| Edit amount after posted | 19 |
| Modify hash chain | 20 |
| MView as source of truth | 21 |
| Sequence gap/reuse | 22 |
| Non-atomic journal posting | 23 |
| Cross-tenant data access | 24 |
| Float/double for currency | 25 |
| Reverse-reversal chain | 26 |
| Hardcoded account code in app | 27 |
| Opening balance without journal | 28 |
| Opening balance after operational txn | 28 |
| Payment without obligation ref | 29, 30 |
| Settlement from non-AR/AP accounts | 29 |
| Route baru tanpa advisory lock | 13, 31 |
| Void tanpa reverse semua derived layers | 31 |
| Facade dengan connection sendiri di void | 23, 31 |
| Read path dari wrapper table | 1, 16, 31 |
| Read bank balance dari current_balance cache | 1, 16, 21 |

---

## Red Flags — Immediately Invoke This Skill

- "update balance", "delete journal", "edit posted transaction"
- "backdate to closed period", "quick fix on financial data"
- AI suggests direct SQL UPDATE on financial tables
- Dashboard reads from `invoices.outstanding` or `bills.amount_paid`
- Hardcoded account codes in application logic
- Opening balance after operational transactions
- Payment created without obligation reference
- Bot routes direct expense to create_bill_payment
- `receive_payments.amount` displayed as authoritative (should be from journal)
- Settlement journal with wrong direction (credit AP or debit AR)
- Route baru yang create journal tanpa advisory lock
- Void path yang skip inventory/bank/wrapper reversal
- Facade call yang buat DB connection sendiri
- GET endpoint yang SUM dari wrapper table
- Read bank balance dari current_balance cache

---

*Version: 3.5 | Last Updated: 2026-03-02 | Status: Ratified as Backend Constitution*
*Changelog: v3.5 — Law 31 New Route Compliance Gate. Bank cache deprecated (Law 21).
Health check 12 checks. DOCUMENT_INTAKE guard added.*
