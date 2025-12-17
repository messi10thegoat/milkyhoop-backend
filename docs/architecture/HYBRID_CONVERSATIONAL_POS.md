# Hybrid Conversational POS Architecture

## Executive Summary

The **Hybrid Conversational POS** feature enables users to input natural language sales commands (e.g., "Jual esse 10, kongbap 10 tunai") and have the system automatically:

1. Parse the sales intent
2. Extract product items, quantities, and payment method
3. Open the POS interface with pre-filled data
4. Ready for immediate checkout

### Key Benefits

| Metric | Hybrid Path (Regex) | Traditional Path (LLM) |
|--------|---------------------|------------------------|
| Response Time | ~10ms | ~1500ms |
| Processing | Deterministic | Probabilistic |
| Dependencies | None (API Gateway only) | gRPC → Tenant Orchestrator → LLM |

This architecture provides **150x faster response** for common sales transactions while maintaining full backward compatibility with the LLM-based intent classification for complex queries.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              USER INPUT                                      │
│                    "Jual esse 10, kongbap 5 tunai"                          │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           API GATEWAY                                        │
│                     tenant_chat.py endpoint                                  │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │                  PHASE 0: parse_sales_intent()                       │   │
│  │                     Regex-based detection                            │   │
│  │                        (~2-5ms)                                      │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                    │                                         │
│                    ┌───────────────┴───────────────┐                        │
│                    │                               │                        │
│             confidence >= 0.5              confidence < 0.5                 │
│                    │                               │                        │
│                    ▼                               ▼                        │
│  ┌─────────────────────────┐      ┌─────────────────────────────────┐      │
│  │   FAST PATH (10ms)      │      │   NORMAL PATH (1500ms)          │      │
│  │   Return action payload │      │   gRPC → Tenant Orchestrator    │      │
│  │   {type: "open_pos"}    │      │   LLM-based classification      │      │
│  └─────────────────────────┘      └─────────────────────────────────┘      │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                            FRONTEND                                          │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │                      ChatPanel                                       │   │
│  │              Detects action.type === "open_pos"                      │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                    │                               │                        │
│              Desktop Mode                    Mobile Mode                    │
│                    │                               │                        │
│                    ▼                               ▼                        │
│  ┌─────────────────────────┐      ┌─────────────────────────────────┐      │
│  │   Dashboard (State)     │      │   ChatPanel (Local State)       │      │
│  │   50-50 Split View      │      │   Full-screen Modal             │      │
│  └─────────────────────────┘      └─────────────────────────────────┘      │
│                    │                               │                        │
│                    └───────────────┬───────────────┘                        │
│                                    ▼                                        │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │                    SalesTransaction                                  │   │
│  │              useEffect: Process initialItems                         │   │
│  │              - Search products by query                              │   │
│  │              - Deduplicate cart entries                              │   │
│  │              - Set payment method                                    │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Backend Architecture

### Source File
`backend/api_gateway/app/routers/tenant_chat.py`

### Core Function: `parse_sales_intent()`

**Location:** Lines 30-310

This function performs regex-based sales intent detection and returns structured data for POS prefill.

#### Sales Trigger Keywords

```python
SALES_TRIGGERS = [
    r"^jual\s+",           # "jual ..." (sell)
    r"^transaksi\s+",      # "transaksi ..." (transaction)
    r"^penjualan\s+",      # "penjualan ..." (sales)
    r"^catat\s+penjualan", # "catat penjualan ..." (record sales)
    r"\bbeli\s+",          # "... beli ..." (customer buying)
    r"^kasir\s+",          # "kasir ..." (cashier)
]
```

#### Supported Units

```python
UNITS = r"(pcs|botol|bungkus|kg|gram|g|dus|box|karton|lusin|liter|l|pack|sachet|biji|buah|unit)"
```

### Item Extraction Strategies

The parser uses three sequential strategies:

#### Strategy A: Separator-Based Parsing
For input with commas, newlines, or "dan"/"and":

```
Input:  "jual esse 4, kongbap 5"
Split:  ["esse 4", "kongbap 5"]
Result: [{productQuery: "Esse", qty: 4}, {productQuery: "Kongbap", qty: 5}]
```

**Patterns within each segment:**
- Pattern A: `product qty [unit]` → "Aqua 2 botol"
- Pattern B: `qty [unit] product` → "2 botol Aqua"
- Pattern C: `product_only` → "Aqua" (defaults to qty=1)

#### Strategy B: Token-by-Token Parsing
For space-separated alternating qty-product patterns:

```
Input:  "jual 5 esse 6 kongbap"
Tokens: ["5", "esse", "6", "kongbap"]
Parse:  5→esse, 6→kongbap
Result: [{productQuery: "Esse", qty: 5}, {productQuery: "Kongbap", qty: 6}]
```

Supports both directions:
- Qty-first: "5 esse" → Esse x5
- Product-first: "esse 5" → Esse x5

#### Strategy C: Fallback
When no quantity detected, assumes qty=1:

```
Input:  "jual aqua"
Result: [{productQuery: "Aqua", qty: 1, unit: "pcs"}]
```

### Payment Method Detection

```python
Payment Patterns:
- Cash:     r"\b(cash|tunai|kontan)\b"
- QRIS:     r"\b(qris|qr|scan)\b"
- Credit:   r"\b(bon|hutang|kredit|piutang)\b"
- Transfer: r"\b(transfer|tf|bank)\b"
```

### Customer Name Extraction

```python
Pattern: r"\b(bu|pak|ibu|bapak|mbak|mas)\s+([A-Za-z]+)"
Example: "Bu Siti beli beras" → customer_name: "Bu Siti"
```

### Confidence Calculation

```python
confidence = 0.0
if items:           confidence += 0.5   # Has items (primary signal)
if payment_method:  confidence += 0.25  # Has payment method
if customer_name:   confidence += 0.15  # Has customer info
if is_sales:        confidence += 0.1   # Has sales keyword
# Capped at 1.0
```

**Threshold:** `confidence >= 0.5` triggers the fast path

### Return Structure

```python
{
    "intent": "sales_pos",
    "items": [
        {"productQuery": str, "qty": float, "unit": str}
    ],
    "payment_method": str | None,  # "tunai", "qris", "hutang", "transfer"
    "customer_name": str | None,   # "Bu Siti"
    "confidence": float,           # 0.0 - 1.0
    "raw_message": str
}
```

---

## API Contract

### Endpoint
```
POST /api/tenant/{tenant_id}/chat
```

### Request Schema
```json
{
    "message": "jual esse 10, kongbap 5 tunai",
    "session_id": "optional-session-id",
    "conversation_context": ""
}
```

### Response Schema (Sales Intent Detected)

```json
{
    "status": "success",
    "milky_response": "Siap! Membuka POS dengan Esse x10, Kongbap x5 (tunai)...",
    "intent": "sales_pos",
    "trace_id": "",
    "action": {
        "type": "open_pos",
        "payload": {
            "items": [
                {"productQuery": "Esse", "qty": 10, "unit": "pcs"},
                {"productQuery": "Kongbap", "qty": 5, "unit": "pcs"}
            ],
            "paymentMethod": "tunai",
            "customerName": null,
            "navigateTo": "pos"
        }
    },
    "confidence_metadata": {
        "confidence_score": 0.85,
        "route_taken": "sales_intent_shortcut",
        "processing_time_ms": 3.21
    }
}
```

### Response Schema (Normal Flow)

When confidence < 0.5, falls through to LLM-based processing:

```json
{
    "status": "success",
    "milky_response": "[Response from tenant_orchestrator]",
    "intent": "[classified_intent]",
    "trace_id": "[uuid]"
}
```

---

## Frontend Architecture

### Files Involved

| File | Purpose |
|------|---------|
| `ChatPanel/index.tsx` | Action detection & routing |
| `Dashboard.tsx` | State lifting for desktop mode |
| `SalesTransaction.tsx` | Prefill processing & cart deduplication |

### ChatPanel: Action Handling

**Location:** `frontend/web/src/components/app/ChatPanel/index.tsx` (Lines 722-737)

```typescript
// Handle action payload from backend
if (response.action?.type === 'open_pos' && response.action?.payload) {
  const { items, paymentMethod } = response.action.payload;

  if (isDesktop && onPanelToggle) {
    // Desktop: Lift state to Dashboard, then open panel
    onPOSPrefillData?.({ items, paymentMethod });
    onPanelToggle('pos');
  } else {
    // Mobile: Use local state for full-screen modal
    setPOSPrefillData({ items, paymentMethod });
    setIsSalesOpen(true);
  }
}
```

### Dashboard: State Management (Desktop)

**Location:** `frontend/web/src/pages/Dashboard.tsx` (Lines 77-81, 540, 457-459)

```typescript
// State lifting for desktop split-view
const [posPrefillData, setPOSPrefillData] = useState<{
  items?: Array<{productQuery: string; qty: number; unit?: string}>;
  paymentMethod?: string;
} | null>(null);

// Pass to ChatPanel
<ChatPanel
  onPOSPrefillData={setPOSPrefillData}
  onPanelToggle={(panel) => setActivePanel(panel)}
/>

// Pass to SalesTransaction
<SalesTransaction
  initialItems={posPrefillData?.items}
  initialPaymentMethod={posPrefillData?.paymentMethod}
  onPrefillProcessed={() => setPOSPrefillData(null)}
/>
```

### SalesTransaction: Prefill Processing

**Location:** `frontend/web/src/components/app/SalesTransaction/SalesTransaction.tsx` (Lines 163-237)

#### Prefill Props Interface

```typescript
interface PrefillItem {
  productQuery: string;
  qty: number;
  unit?: string;
}

interface SalesTransactionProps {
  // ... existing props
  initialItems?: PrefillItem[];
  initialPaymentMethod?: 'tunai' | 'qris' | 'hutang';
  onPrefillProcessed?: () => void;
}
```

#### Processing Logic

```typescript
useEffect(() => {
  if (!initialItems || initialItems.length === 0) return;

  const processInitialItems = async () => {
    for (const item of initialItems) {
      // 1. Search product by query
      const response = await fetch(
        `/api/products/search/pos?q=${encodeURIComponent(item.productQuery)}&limit=1`
      );
      const data = await response.json();

      if (data.products && data.products.length > 0) {
        const product = data.products[0];

        // 2. DEDUPLICATION: Check if product already in cart
        setCart(prevCart => {
          const existingIndex = prevCart.findIndex(
            cartItem => cartItem.id === product.id ||
                        cartItem.name.toLowerCase() === product.name.toLowerCase()
          );

          if (existingIndex >= 0) {
            // Product exists → ADD quantity
            const newCart = [...prevCart];
            newCart[existingIndex].qty += item.qty;
            return newCart;
          } else {
            // New product → ADD to cart
            return [...prevCart, {
              id: product.id,
              productId: product.id,
              name: product.name,
              price: product.harga_jual,
              qty: item.qty,
              barcode: product.barcode || ''
            }];
          }
        });

        soundFeedback.itemAdded();
      }
    }

    onPrefillProcessed?.();
  };

  processInitialItems();
}, [initialItems]);
```

### Cart Deduplication Logic

The deduplication uses a two-key matching strategy:

1. **ID Match:** `cartItem.id === product.id`
2. **Name Match (fallback):** `cartItem.name.toLowerCase() === product.name.toLowerCase()`

**Behavior:**
- If product exists in cart → Quantities are **added** (not replaced)
- If product is new → Added as new cart item
- Case-insensitive name matching handles variations

**Example:**
```
Cart: [{ name: "Aqua", qty: 5 }]
Prefill: [{ productQuery: "aqua", qty: 3 }]
Result: [{ name: "Aqua", qty: 8 }]  // 5 + 3 = 8
```

---

## Data Flow Diagram

```
┌──────────────────────────────────────────────────────────────────────────┐
│ USER: "jual esse 10, kongbap 5 tunai"                                    │
└──────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌──────────────────────────────────────────────────────────────────────────┐
│ API GATEWAY: POST /api/tenant/{tenant_id}/chat                           │
│ ─────────────────────────────────────────────────────────────────────    │
│ 1. parse_sales_intent(message)                                           │
│ 2. confidence = 0.85 (>= 0.5) → FAST PATH                               │
│ 3. Build action payload                                                  │
│ 4. Return response with action                                           │
└──────────────────────────────────────────────────────────────────────────┘
                                    │
                    ┌───────────────┴───────────────┐
                    │ Response                       │
                    │ {                              │
                    │   action: {                    │
                    │     type: "open_pos",          │
                    │     payload: {                 │
                    │       items: [...],            │
                    │       paymentMethod: "tunai"   │
                    │     }                          │
                    │   }                            │
                    │ }                              │
                    └───────────────────────────────┘
                                    │
                                    ▼
┌──────────────────────────────────────────────────────────────────────────┐
│ CHATPANEL: handleSend() response handler                                 │
│ ─────────────────────────────────────────────────────────────────────    │
│ if (response.action?.type === 'open_pos') {                             │
│   const { items, paymentMethod } = response.action.payload;             │
└──────────────────────────────────────────────────────────────────────────┘
                                    │
                    ┌───────────────┴───────────────┐
                    │                               │
              DESKTOP (≥1024px)               MOBILE (<1024px)
                    │                               │
                    ▼                               ▼
┌─────────────────────────────┐   ┌─────────────────────────────┐
│ onPOSPrefillData(payload)   │   │ setPOSPrefillData(payload)  │
│ → Dashboard.setPOSPrefillData│   │ setIsSalesOpen(true)        │
│ onPanelToggle('pos')        │   │ → Render Modal              │
│ → setActivePanel('pos')     │   │                             │
└─────────────────────────────┘   └─────────────────────────────┘
                    │                               │
                    └───────────────┬───────────────┘
                                    ▼
┌──────────────────────────────────────────────────────────────────────────┐
│ SALESTRANSACTION: useEffect on initialItems                              │
│ ─────────────────────────────────────────────────────────────────────    │
│ for (item of initialItems) {                                             │
│   1. GET /api/products/search/pos?q={item.productQuery}                  │
│   2. Check deduplication (ID or name match)                              │
│   3. Add/update cart item                                                │
│   4. Play sound feedback                                                 │
│ }                                                                        │
│ onPrefillProcessed() → Clear prefill state                               │
└──────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌──────────────────────────────────────────────────────────────────────────┐
│ POS READY: Cart populated, payment method selected                       │
│ ─────────────────────────────────────────────────────────────────────    │
│ Cart: [Esse x10, Kongbap x5]                                             │
│ Payment: Tunai (pre-selected)                                            │
│ User: Review and complete transaction                                    │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## Supported Query Patterns

| Pattern | Example | Result |
|---------|---------|--------|
| Single product | `jual aqua` | Aqua x1 |
| Product + qty | `jual aqua 10` | Aqua x10 |
| Qty-first | `jual 10 aqua` | Aqua x10 |
| With unit | `jual 2 botol aqua` | Aqua x2 botol |
| Comma-separated | `jual esse 4, kongbap 5` | Esse x4, Kongbap x5 |
| "dan" separator | `jual esse 4 dan kongbap 5` | Esse x4, Kongbap x5 |
| Space-separated | `jual esse 4 kongbap 5` | Esse x4, Kongbap x5 |
| Qty-first multiple | `jual 4 esse 5 kongbap` | Esse x4, Kongbap x5 |
| Mixed patterns | `jual esse 3, 5 aqua` | Esse x3, Aqua x5 |
| With payment | `jual esse 10 tunai` | Esse x10 [tunai] |
| With customer | `bu siti beli beras 5kg` | Beras x5 kg, customer: Bu Siti |
| Full command | `jual esse 10, kongbap 5 qris` | Esse x10, Kongbap x5 [qris] |

### Payment Method Keywords

| Method | Keywords |
|--------|----------|
| Cash | `tunai`, `cash`, `kontan` |
| QRIS | `qris`, `qr`, `scan` |
| Credit/Debt | `bon`, `hutang`, `kredit`, `piutang` |
| Transfer | `transfer`, `tf`, `bank` |

---

## Testing

### Test Script

```bash
#!/bin/bash
TOKEN="[YOUR_JWT_TOKEN]"
DEVICE_ID="test-device"

test_case() {
  local msg="$1"
  echo "Testing: $msg"
  curl -s -X POST "http://localhost:8001/api/tenant/evlogia/chat" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -H "X-Device-ID: $DEVICE_ID" \
    -d "{\"message\": \"$msg\", \"session_id\": \"test-$(date +%s)\"}" \
    | python3 -c "
import sys,json
d=json.load(sys.stdin)
action = d.get('action', {})
if action.get('type') == 'open_pos':
    items = action.get('payload', {}).get('items', [])
    pm = action.get('payload', {}).get('paymentMethod', '')
    result = ', '.join([f\"{i['productQuery']} x{i['qty']}\" for i in items])
    print(f'  → {result}' + (f' [{pm}]' if pm else ''))
else:
    print('  → No POS action')
"
}

test_case "jual esse"
test_case "jual esse 10"
test_case "jual 10 esse"
test_case "jual esse 4, kongbap 5"
test_case "jual 4 esse 5 kongbap"
test_case "jual esse 10 tunai"
```

### Expected Output

```
Testing: jual esse
  → Esse x1
Testing: jual esse 10
  → Esse x10
Testing: jual 10 esse
  → Esse x10
Testing: jual esse 4, kongbap 5
  → Esse x4, Kongbap x5
Testing: jual 4 esse 5 kongbap
  → Esse x4, Kongbap x5
Testing: jual esse 10 tunai
  → Esse x10 [tunai]
```

---

## Performance Metrics

| Phase | Duration |
|-------|----------|
| Regex parsing | 1-3ms |
| Confidence calculation | <1ms |
| Response building | 1-2ms |
| **Total Backend** | **~5ms** |
| Network RTT | ~5ms |
| **Total User-Facing** | **~10ms** |

Compare with LLM-based path: ~1500ms

---

## Related Documentation

- [Tenant Mode Flow Logic](./TENANT_MODE_FLOW_LOGIC.md) - Complete tenant orchestration flow
- [API Gateway](../backend/API-GATEWAY.md) - HTTP endpoints and authentication
- [Smart Login](../backend/SMART-LOGIN.md) - Session management and device enforcement

---

## Code References

| Component | File | Lines |
|-----------|------|-------|
| Sales Intent Parser | `backend/api_gateway/app/routers/tenant_chat.py` | 30-310 |
| Chat Endpoint | `backend/api_gateway/app/routers/tenant_chat.py` | 377-501 |
| ChatPanel Action Handler | `frontend/web/src/components/app/ChatPanel/index.tsx` | 722-737 |
| Dashboard State | `frontend/web/src/pages/Dashboard.tsx` | 77-81 |
| SalesTransaction Prefill | `frontend/web/src/components/app/SalesTransaction/SalesTransaction.tsx` | 163-237 |

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | 2025-12-16 | Initial implementation with regex-based parser |
