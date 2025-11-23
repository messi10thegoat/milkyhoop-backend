# Transaction Service Investigation Summary

## Overview
Investigation of transaction_service gRPC interface for direct CreateTransaction calls.

---

## 1. Proto Message Structure

### Service Definition
**File:** `/root/milkyhoop-dev/protos/transaction_service.proto`

```protobuf
package transaction_service;

service TransactionService {
  rpc CreateTransaction (CreateTransactionRequest) returns (TransactionResponse);
  rpc UpdateTransaction (UpdateTransactionRequest) returns (TransactionResponse);
  rpc DeleteTransaction (DeleteTransactionRequest) returns (google.protobuf.Empty);
  rpc GetTransaction (GetTransactionRequest) returns (TransactionResponse);
  rpc ListTransactions (ListTransactionsRequest) returns (ListTransactionsResponse);
  rpc HealthCheck (google.protobuf.Empty) returns (HealthResponse);
}
```

### CreateTransactionRequest - Required Fields for Pembelian

```protobuf
message CreateTransactionRequest {
  // CORE FIELDS (Required)
  string tenant_id = 1;           // Required: tenant identifier
  string created_by = 2;          // Required: user_id
  string actor_role = 3;          // Required: 'owner', 'bendahara', 'staf_toko', etc.
  string jenis_transaksi = 4;     // Required: 'penjualan', 'pembelian', 'beban'

  // Transaction payload (oneof - use pembelian for purchase)
  oneof transaction_data {
    TransaksiPenjualan penjualan = 5;
    TransaksiPembelian pembelian = 6;
    TransaksiBeban beban = 7;
  }

  // Metadata (Optional but recommended)
  string raw_text = 8;            // Original user message
  string idempotency_key = 11;    // CRITICAL: Prevent duplicates

  // Payment info
  string rekening_type = 13;      // 'cash', 'transfer', 'bank_bca', etc.

  // SAK EMKM fields (auto-calculated or provided)
  int64 total_nominal = 14;       // Total in cents (or rupiah integers)
  string metode_pembayaran = 15;  // 'cash', 'transfer', 'tempo'
  string status_pembayaran = 16;  // 'lunas', 'dp', 'tempo'
}
```

### TransaksiPembelian - Purchase Payload

```protobuf
message TransaksiPembelian {
  string vendor_name = 1;         // Optional: supplier name
  string vendor_id = 2;           // Optional: supplier ID

  repeated ItemPembelian items = 3;  // REQUIRED: list of items

  int64 subtotal = 4;             // Sum of item subtotals
  int64 discount = 5;             // Discount amount
  int64 tax = 6;                  // PPN if applicable
  int64 total_nominal = 7;        // Final total

  string payment_method = 8;      // 'cash', 'transfer', 'piutang'
  string payment_status = 9;      // 'lunas', 'dp', 'belum_bayar'

  int64 amount_paid = 10;         // For partial payments
  int64 amount_due = 11;          // Remaining balance
  int64 due_date = 12;            // Unix timestamp

  string notes = 13;
  string kategori = 14;           // 'bahan_baku', 'barang_jadi', 'aset'
}
```

### ItemPembelian - Purchase Item

```protobuf
message ItemPembelian {
  string sku = 1;           // Product SKU (optional)
  string name = 2;          // Product name (REQUIRED)
  int32 quantity = 3;       // Quantity (REQUIRED)
  string unit = 4;          // Unit: 'pcs', 'kg', 'meter'
  int64 unit_price = 5;     // Price per unit (REQUIRED)
  int64 subtotal = 6;       // quantity * unit_price (REQUIRED)
}
```

---

## 2. Example Proto Message for Pembelian (Purchase)

```python
import grpc
from milkyhoop_protos import transaction_service_pb2 as pb
from milkyhoop_protos import transaction_service_pb2_grpc

# Connect to transaction service
channel = grpc.aio.insecure_channel("transaction_service:7020")
stub = transaction_service_pb2_grpc.TransactionServiceStub(channel)

# Create purchase request
request = pb.CreateTransactionRequest(
    # Core fields
    tenant_id="evlogia",
    created_by="d780b7fe-8b53-47e4-8ef1-aad067de0d58",  # user_id
    actor_role="owner",
    jenis_transaksi="pembelian",

    # Purchase payload
    pembelian=pb.TransaksiPembelian(
        vendor_name="Supplier ABC",
        items=[
            pb.ItemPembelian(
                sku="",
                name="laptop MacBook",
                quantity=5,
                unit="pcs",
                unit_price=15000000,  # Rp 15,000,000
                subtotal=75000000     # 5 * 15,000,000
            )
        ],
        subtotal=75000000,
        discount=0,
        tax=0,
        total_nominal=75000000,
        payment_method="transfer",
        payment_status="lunas",
        amount_paid=75000000,
        amount_due=0,
        notes=""
    ),

    # Metadata
    raw_text="beli 5 laptop MacBook @15jt",
    idempotency_key=f"tx_{uuid.uuid4().hex[:16]}",

    # Payment info
    rekening_type="transfer",
    metode_pembayaran="transfer",
    status_pembayaran="lunas",
    total_nominal=75000000
)

# Make the call
response = await stub.CreateTransaction(request)
```

---

## 3. Transaction Client Status

**Does transaction_client.py exist in API Gateway?**

**NO** - There is no `transaction_client.py` in `/root/milkyhoop-dev/backend/api_gateway/app/services/`

The transaction calls are currently made from:
- `tenant_orchestrator/app/handlers/transaction_handler.py` - uses inline gRPC stub

**Need to create:** A new `transaction_client.py` in API Gateway if direct calls from API Gateway are needed.

---

## 4. Reference Pattern - How Other Clients Connect

### Connection Pattern from auth_client.py

```python
class AuthClient:
    def __init__(self, host: str = "milkyhoop-dev-auth_service-1", port: int = 8013, timeout: float = 60.0):
        self.target = f"{host}:{port}"
        self.timeout = timeout
        self.channel: Optional[grpc.aio.Channel] = None
        self.stub: Optional[auth_service_pb2_grpc.AuthServiceStub] = None
        self._connect_lock = asyncio.Lock()

    async def connect(self):
        """Connect to service with persistent channel"""
        async with self._connect_lock:
            if self.channel is None or self.stub is None:
                self.channel = grpc.aio.insecure_channel(
                    self.target,
                    options=[
                        ('grpc.keepalive_time_ms', 10000),
                        ('grpc.keepalive_timeout_ms', 5000),
                        ('grpc.keepalive_permit_without_calls', True),
                        ('grpc.http2.max_pings_without_data', 0),
                    ]
                )
                self.stub = auth_service_pb2_grpc.AuthServiceStub(self.channel)
                logger.info(f"Connected to gRPC service at {self.target}")

    async def ensure_connected(self):
        """Ensure connection is established"""
        if self.channel is None or self.stub is None:
            await self.connect()

    async def close(self):
        """Close connection"""
        if self.channel:
            await self.channel.close()
            self.channel = None
            self.stub = None
```

### Method Pattern

```python
async def some_method(self, param1: str, param2: str) -> Dict[str, Any]:
    """Method description"""
    try:
        await self.ensure_connected()

        request = pb.SomeRequest(
            field1=param1,
            field2=param2
        )

        response = await self.stub.SomeMethod(request)

        return {
            "success": response.success,
            "message": response.message,
            "data": response.data if response.success else None
        }

    except Exception as e:
        logger.error(f"Method error: {str(e)}")
        return {
            "success": False,
            "message": f"Error: {str(e)}"
        }
```

---

## 5. Service Connection Details

| Service | Container Name | Port | Proto Package |
|---------|---------------|------|---------------|
| Transaction | milkyhoop-dev-transaction_service-1 | 7020 | transaction_service |
| Auth | milkyhoop-dev-auth_service-1 | 8013 | auth_service |
| Inventory | milkyhoop-dev-inventory_service-1 | 7040 | inventory_service |

---

## 6. gRPC Server Implementation Details

**File:** `/root/milkyhoop-dev/backend/services/transaction_service/app/grpc_server.py`

```python
class TransactionServicer(pb.TransactionServiceServicer):
    async def CreateTransaction(
        self,
        request: pb.CreateTransactionRequest,
        context: grpc.aio.ServicerContext
    ) -> pb.TransactionResponse:
        """Route to TransactionHandler"""
        return await TransactionHandler.handle_create_transaction(
            request=request,
            context=context,
            pb=pb,
            get_inventory_client_func=get_inventory_client,
            process_accounting_func=process_transaction_accounting
        )
```

The actual implementation is in `TransactionHandler.handle_create_transaction()` which:
1. Checks idempotency_key
2. Validates tenant/user
3. Extracts inventory impact
4. Generates transaction ID
5. Converts Proto to DB payload
6. Creates transaction record
7. Creates ItemTransaksi records
8. Creates outbox events for async processing
9. Returns TransactionResponse

---

## 7. Required Proto Imports

If creating a new client, you need:

```python
from milkyhoop_protos import transaction_service_pb2 as pb
from milkyhoop_protos import transaction_service_pb2_grpc
```

Proto files should be compiled to:
- `/root/milkyhoop-dev/backend/api_gateway/libs/milkyhoop_protos/transaction_service_pb2.py`
- `/root/milkyhoop-dev/backend/api_gateway/libs/milkyhoop_protos/transaction_service_pb2_grpc.py`

---

## 8. Next Steps (DO NOT IMPLEMENT YET)

1. Generate proto files for API Gateway if not exist
2. Create `transaction_client.py` following auth_client.py pattern
3. Add method for `create_transaction()` with pembelian support
4. Test direct gRPC call to transaction_service

---

## Notes

- All monetary values are in **integers (cents/rupiah)**, not floats
- `idempotency_key` is CRITICAL for preventing duplicate transactions
- Transaction status starts as 'draft', processed by outbox_worker
- Inventory and accounting updates happen asynchronously via outbox pattern
