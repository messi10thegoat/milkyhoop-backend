# 🏗️ TENANT MODE FLOW LOGIC - Complete Architecture Documentation

**Date:** November 16, 2025  
**Status:** Production Ready ✅  
**Version:** 4.0

---

## 📊 **ARCHITECTURE OVERVIEW**

```
User Chat Input (Natural Language)
    ↓
API Gateway (Port 8001) - FastAPI HTTP
    ↓
tenant_orchestrator (Port 7017:5017) - gRPC
    ↓
business_parser (Port 7018:5018) - Intent Classification
    ↓
┌─────────────────────────────────────────────────────────┐
│         ROUTING BASED ON INTENT                         │
└─────────────────────────────────────────────────────────┘
    ↓                    ↓                    ↓
transaction_service  reporting_service  inventory_service
  (Port 7020)          (Port 7030)         (Port 7040)
    ↓                    ↓                    ↓
PostgreSQL (Supabase) - Multi-tenant Database
    ↓
outbox_worker (Port 7060:5060) - Background Async
    ↓                    ↓
inventory_service   accounting_service
  (Update stock)      (Create journal)
```

---

## 🔄 **COMPLETE FLOW: Transaction Recording**

### **Phase 1: Request Processing (Synchronous - 2-3 seconds)**

#### **Step 1: API Gateway → tenant_orchestrator**
- **Endpoint:** `POST /api/tenant/{tenant_id}/chat`
- **Authentication:** JWT Bearer token
- **Request:**
  ```json
  {
    "message": "jual 10 kaos @45rb",
    "session_id": "uuid",
    "conversation_context": ""
  }
  ```
- **Action:** Convert HTTP → gRPC call to `tenant_orchestrator.ProcessTenantQuery`

#### **Step 2: tenant_orchestrator - Context Retrieval**
- **Service:** `conversation_manager.GetContext`
- **Purpose:** Get conversation history and context
- **Duration:** ~50-100ms
- **Output:** Context object with progress percentage

#### **Step 3: tenant_orchestrator - Intent Classification**
- **Service:** `business_parser.ClassifyIntent`
- **Input:** User message + context
- **Process:**
  1. LLM (GPT-3.5-turbo) classification
  2. Entity extraction (items, amounts, payment methods)
  3. Intent normalization
  4. Fallback to regex if LLM fails
- **Output:**
  ```json
  {
    "intent": "transaction_record",
    "entities": {
      "jenis_transaksi": "penjualan",
      "total_nominal": 450000,
      "items": [{
        "nama_produk": "kaos",
        "jumlah": 10,
        "harga_satuan": 45000,
        "subtotal": 450000
      }],
      "inventory_impact": {
        "is_tracked": true,
        "jenis_movement": "keluar",
        "items_inventory": [...]
      }
    },
    "confidence": 0.95
  }
  ```
- **Duration:** ~500-800ms

#### **Step 4: tenant_orchestrator - Routing**
- **Intent:** `transaction_record` → `TransactionHandler.handle_transaction_record`
- **Other intents:**
  - `financial_report` → `FinancialHandler.handle_financial_report`
  - `inventory_query` → `InventoryHandler.handle_inventory_query`
  - `retur_penjualan`, `retur_pembelian`, `pembayaran_hutang` → `TransactionHandler.handle_transaction_record`

#### **Step 5: TransactionHandler - Payload Building**
- **Location:** `backend/services/tenant_orchestrator/app/handlers/transaction_handler.py`
- **Actions:**
  1. Parse entities from `intent_response.entities_json`
  2. **Validate & Recalculate:**
     - Recalculate `total_nominal` from sum of item subtotals (fixes multi-item calculation)
     - Validate required fields (`jenis_transaksi`, `total_nominal`)
     - Allow negative `total_nominal` for returns
  3. **Build Proto Payload:**
     - `TransaksiPenjualan` for `penjualan`
     - `TransaksiPembelian` for `pembelian`
     - `TransaksiBeban` for `beban`, `pembayaran_hutang`
     - Negative totals for `retur_penjualan`, `retur_pembelian`
  4. **Build Inventory Impact:**
     - Query current stock for each product
     - Calculate `stok_setelah` = current_stock + jumlah_movement
     - Build `InventoryImpact` proto
  5. **Build Response Message:**
     - Format natural language confirmation
     - Include items, payment method, total
- **Duration:** ~200-300ms

#### **Step 6: transaction_service - Transaction Creation**
- **Service:** `transaction_service.CreateTransaction`
- **Location:** `backend/services/transaction_service/app/handlers/transaction_handler.py`
- **Actions:**
  1. **Idempotency Check:**
     - Query by `idempotency_key`
     - Return existing transaction if found
  2. **Validation:**
     - Validate `tenant_id`, `user_id` exist
     - Check `jenis_transaksi` constraint (database level)
  3. **Atomic Write (Transaction):**
     ```python
     # Single database transaction
     new_tx = await rls_prisma.transaksiharian.create(data={...})
     
     # Outbox Event 1: Inventory Update
     await rls_prisma.outbox.create(data={
         'eventType': 'inventory.update',
         'payload': {...},
         'processed': False
     })
     
     # Outbox Event 2: Accounting Journal
     await rls_prisma.outbox.create(data={
         'eventType': 'accounting.create',
         'payload': {...},
         'processed': False
     })
     ```
  4. **Return Response:**
     - Transaction ID
     - Success status
- **Duration:** ~2-3 seconds (database write + outbox creation)
- **Key:** NO synchronous calls to inventory/accounting (optimized!)

#### **Step 7: Response Chain**
- `transaction_service` → `tenant_orchestrator` → `API Gateway` → User
- **Total User-Facing Duration:** ~2-3 seconds ✅

---

### **Phase 2: Background Processing (Asynchronous - 5-7 seconds)**

#### **Step 8: outbox_worker - Event Polling**
- **Service:** `outbox_worker` (Port 7060:5060)
- **Location:** `backend/services/outbox_worker/app/workers/outbox_processor.py`
- **Configuration:**
  - Poll interval: 2 seconds
  - Batch size: 10 events
  - Max retries: 3 attempts
- **Process:**
  1. **Poll Unprocessed Events:**
     ```python
     events = await db.outbox.find_many(
         where={
             'processed': False,
             'retryCount': {'lt': 3}
         },
         order={'createdAt': 'asc'},
         take=10
     )
     ```
  2. **Process Each Event:**
     - Route by `eventType`:
       - `inventory.update` → `_handle_inventory_update()`
       - `accounting.create` → `_handle_accounting_create()`
  3. **Update Status:**
     - Success: `processed = True`, `processedAt = now()`
     - Failure: `retryCount++`, `errorMessage = ...`

#### **Step 9: Inventory Update (via outbox_worker)**
- **Service:** `inventory_service.ProcessInventoryImpact`
- **Port:** 7040
- **Process:**
  1. Receive `InventoryImpact` proto from outbox payload
  2. For each `ItemInventory`:
     - Update `persediaan` table
     - Calculate new stock: `stok_setelah = current_stock + jumlah_movement`
     - Update `lastMovementAt` timestamp
  3. Return success/failure
- **Duration:** ~1-2 seconds

#### **Step 10: Accounting Journal Creation (via outbox_worker)**
- **Service:** `accounting_service.ProcessTransaction`
- **Port:** 7050
- **Process:**
  1. Get Chart of Accounts mapping for transaction type
  2. Create `JurnalEntry` header:
     - `nomorJurnal`: `JE-YYYY-MM-NNN` (auto-increment)
     - `tanggalJurnal`: Transaction timestamp
     - `totalDebit` = `totalKredit` (double-entry validation)
  3. Create `JurnalDetail` lines:
     - Debit line: e.g., `Kas` (1-1100)
     - Credit line: e.g., `Pendapatan Penjualan` (4-1000)
  4. Validate: `totalDebit == totalKredit`
  5. Set status: `posted`
- **Duration:** ~2-3 seconds

---

## 📋 **COMPLETE FLOW: Financial Report Query**

### **Flow: "untung bulan ini berapa?"**

#### **Step 1-3: Same as Transaction Recording**
- API Gateway → tenant_orchestrator → business_parser
- **Intent:** `financial_report`
- **Entities:** `{"report_type": "laba_rugi", "periode_pelaporan": "2025-11"}`

#### **Step 4: Routing to FinancialHandler**
- **Handler:** `FinancialHandler.handle_financial_report`
- **Location:** `backend/services/tenant_orchestrator/app/handlers/financial_handler.py`

#### **Step 5: Call reporting_service**
- **Service:** `reporting_service.GetLabaRugi`
- **Port:** 7030
- **Process:**
  1. Build WHERE clause:
     - `tenantId = ...`
     - `status IN ['draft', 'approved']`
     - `timestamp BETWEEN start AND end` (from periode)
  2. Query transactions:
     - `penjualan` → Sum `totalNominal` = Pendapatan
     - `pembelian` → Sum `totalNominal` = HPP
     - `beban` → Sum `totalNominal` = Beban Operasional
  3. Calculate:
     - Laba Kotor = Pendapatan - HPP
     - Laba Bersih = Laba Kotor - Beban Operasional
  4. Format response with emoji and tree structure
- **Duration:** ~500-1000ms

#### **Step 6: Format & Return**
- Format natural language response:
  ```
  ✅ LABA BERSIH 2025-11: Rp70.500.000
  📊 Ringkasan:
  ├─ Pendapatan Penjualan    Rp350.000.000
  ├─ HPP (Pembelian)         Rp260.000.000
  ├─ Laba Kotor              Rp90.000.000
  ├─ Beban Operasional       Rp19.500.000
  └─ Laba Bersih              Rp70.500.000
  ```

---

## 🔧 **SERVICE INTERACTIONS**

### **tenant_orchestrator**
- **Role:** Thin routing layer
- **Responsibilities:**
  - Intent classification (via business_parser)
  - Route to appropriate handler
  - Format responses
  - Track service calls for monitoring
- **No Business Logic:** All logic in handlers/

### **business_parser**
- **Role:** NLP Intent Classification
- **Technology:** GPT-3.5-turbo + regex fallback
- **Output:** Intent + Entities (JSON)
- **Intents Supported:**
  - `transaction_record` (penjualan, pembelian, beban, modal, prive)
  - `retur_penjualan`, `retur_pembelian`
  - `pembayaran_hutang`
  - `financial_report`
  - `top_products`, `low_sell_products`
  - `inventory_query`
  - `query_transaksi`

### **transaction_service**
- **Role:** Transaction persistence
- **Responsibilities:**
  - Create/Update/Delete transactions
  - Idempotency handling
  - Outbox event creation
  - Multi-tenant isolation (RLS)
- **Optimization:** No synchronous inventory/accounting calls

### **outbox_worker**
- **Role:** Background event processor
- **Pattern:** Transactional Outbox
- **Responsibilities:**
  - Poll outbox table every 2 seconds
  - Process `inventory.update` events
  - Process `accounting.create` events
  - Retry logic (max 3 attempts)
  - Error logging

### **inventory_service**
- **Role:** Stock management
- **Responsibilities:**
  - Update `persediaan` table
  - Track stock movements
  - Support multi-warehouse (`lokasi_gudang`)
  - Calculate stock after movement

### **accounting_service**
- **Role:** Double-entry bookkeeping
- **Responsibilities:**
  - Auto-generate journal entries
  - Chart of Accounts (CoA) management
  - Double-entry validation (Debit = Credit)
  - Journal numbering (JE-YYYY-MM-NNN)

### **reporting_service**
- **Role:** Financial report generation
- **Reports:**
  - Laba Rugi (Income Statement)
  - Neraca (Balance Sheet)
  - Arus Kas (Cash Flow Statement)
  - Perubahan Ekuitas (Changes in Equity)
- **Data Source:** Direct query from `transaksi_harian` table

---

## 🎯 **KEY ARCHITECTURAL PATTERNS**

### **1. Transactional Outbox Pattern**
- **Problem:** Need to update inventory/accounting but don't want to block user response
- **Solution:** Write transaction + outbox events atomically, process async
- **Benefit:** 80% faster user response (15s → 3s)

### **2. Multi-Tenant Isolation (RLS)**
- **Implementation:** Row Level Security in PostgreSQL
- **Prisma Extension:** `RLSPrismaClient` sets `tenant_id` context
- **Benefit:** Automatic data isolation, no manual filtering needed

### **3. Idempotency**
- **Key:** `idempotency_key` = `tenant_{tenant_id}_{trace_id}`
- **Check:** Query existing transaction before create
- **Benefit:** Prevent duplicate transactions from retries

### **4. Intent-Based Routing**
- **Pattern:** business_parser classifies → tenant_orchestrator routes → handler processes
- **Benefit:** Clean separation, easy to add new intents

### **5. Proto-First API Design**
- **All Services:** gRPC with Protocol Buffers
- **Exception:** API Gateway (FastAPI HTTP)
- **Benefit:** Type safety, versioning, performance

---

## 📊 **PERFORMANCE METRICS**

### **Transaction Recording:**
- **User-Facing:** 2-3 seconds (was 15s before optimization)
- **Background:** 5-7 seconds (non-blocking)
- **Total:** ~8-10 seconds end-to-end

### **Financial Report:**
- **Query Time:** 500-1000ms
- **Formatting:** 50-100ms
- **Total:** ~1 second

### **Intent Classification:**
- **LLM Call:** 500-800ms
- **Fallback (regex):** 10-50ms
- **Average:** ~600ms

---

## 🔍 **ERROR HANDLING**

### **Transaction Service:**
- **Idempotency:** Return existing transaction if duplicate
- **Validation:** Database constraint violations return clear errors
- **Outbox:** Events created even if transaction fails (for audit)

### **Outbox Worker:**
- **Retry Logic:** Max 3 attempts per event
- **Error Logging:** Full stack trace + error message in `outbox.errorMessage`
- **Dead Letter:** Events with `retryCount >= 3` remain unprocessed (manual review)

### **Handler Errors:**
- **Catch-All:** All handler errors return user-friendly message
- **Logging:** Full error details in logs with `trace_id`
- **Fallback:** "Maaf, terjadi error..." message to user

---

## 🚀 **DEPLOYMENT & MONITORING**

### **Service Ports:**
- API Gateway: 8001 (HTTP)
- tenant_orchestrator: 7017:5017 (gRPC)
- business_parser: 7018:5018 (gRPC)
- transaction_service: 7020 (gRPC)
- reporting_service: 7030 (gRPC)
- inventory_service: 7040 (gRPC)
- accounting_service: 7050 (gRPC)
- outbox_worker: 7060:5060 (gRPC)

### **Health Checks:**
- All services: `HealthCheck` gRPC method
- API Gateway: `GET /health` HTTP endpoint

### **Logging:**
- All services: Structured logging with `trace_id`
- Format: `[trace_id] Message | key=value`
- Level: INFO (production), DEBUG (development)

---

## 📝 **NOTES FOR DEVELOPERS**

1. **Always use `trace_id`** for request tracking across services
2. **RLS Client:** Use `RLSPrismaClient(tenant_id=...)` for database queries
3. **Proto Messages:** Check proto files for exact field names
4. **Outbox Events:** Always set `processed = False` initially
5. **Error Handling:** Return user-friendly messages, log full details
6. **Testing:** Use curl with JWT token for integration testing

---

**Last Updated:** November 16, 2025  
**Maintained By:** MilkyHoop Team

