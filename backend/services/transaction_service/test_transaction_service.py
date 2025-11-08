"""
Test Client for Transaction Service
Test CreateTransaction with Indonesian conversation payload
"""

import asyncio
import grpc
from datetime import datetime

from app import transaction_service_pb2 as pb
from app import transaction_service_pb2_grpc as pb_grpc


async def test_create_penjualan():
    """
    Test CreateTransaction with penjualan (sales) data.
    Simulates: "Tadi jual baju 5 pcs @ 150rb ke Ibu Sari, dibayar cash"
    """
    print("=" * 60)
    print("TEST 1: Create Penjualan Transaction")
    print("=" * 60)
    
    async with grpc.aio.insecure_channel("localhost:7020") as channel:
        stub = pb_grpc.TransactionServiceStub(channel)
        
        # Prepare penjualan data
        penjualan = pb.TransaksiPenjualan(
            customer_name="Ibu Sari",
            customer_id="cust_001",
            items=[
                pb.ItemPenjualan(
                    sku="BJ001",
                    name="Baju Batik",
                    quantity=5,
                    unit="pcs",
                    unit_price=150000,
                    subtotal=750000
                )
            ],
            subtotal=750000,
            discount=0,
            tax=0,
            total_nominal=750000,
            payment_method="cash",
            payment_status="paid",
            amount_paid=750000,
            amount_due=0,
            notes="Pelanggan reguler"
        )
        
        # Create request
        request = pb.CreateTransactionRequest(
            tenant_id="konsultanpsikologi",
            created_by="135f057c-7993-4040-8aaf-b6a96637d9a3",
            actor_role="owner",
            jenis_transaksi="penjualan",
            penjualan=penjualan,
            raw_text="Tadi jual baju 5 pcs @ 150rb ke Ibu Sari, dibayar cash",
            idempotency_key=f"milkyhoop_konsultan_{int(datetime.now().timestamp())}_001",
            rekening_id="rek_cash_001",
            rekening_type="pribadi"
        )
        
        try:
            response = await stub.CreateTransaction(request)
            print(f"✅ SUCCESS")
            print(f"Transaction ID: {response.transaction.id}")
            print(f"Status: {response.transaction.status}")
            print(f"Message: {response.message}")
            print(f"Idempotency Key: {response.transaction.idempotency_key}")
            
            return response.transaction.id
        except grpc.RpcError as e:
            print(f"❌ FAILED: {e.code()} - {e.details()}")
            return None


async def test_create_beban():
    """
    Test CreateTransaction with beban (expense) data.
    Simulates: "Bayar listrik bulan ini 500rb"
    """
    print("\n" + "=" * 60)
    print("TEST 2: Create Beban Transaction")
    print("=" * 60)
    
    async with grpc.aio.insecure_channel("localhost:7020") as channel:
        stub = pb_grpc.TransactionServiceStub(channel)
        
        # Prepare beban data
        beban = pb.TransaksiBeban(
            kategori="Utilitas",
            deskripsi="Pembayaran listrik bulan Oktober",
            nominal=500000,
            payment_method="transfer",
            recipient="PLN",
            is_reimbursement=False,
            notes="Tagihan listrik kantor"
        )
        
        request = pb.CreateTransactionRequest(
            tenant_id="konsultanpsikologi",
            created_by="135f057c-7993-4040-8aaf-b6a96637d9a3",
            actor_role="owner",
            jenis_transaksi="beban",
            beban=beban,
            raw_text="Bayar listrik bulan ini 500rb",
            idempotency_key=f"milkyhoop_konsultan_{int(datetime.now().timestamp())}_002",
            rekening_id="rek_bank_001",
            rekening_type="bisnis"
        )
        
        try:
            response = await stub.CreateTransaction(request)
            print(f"✅ SUCCESS")
            print(f"Transaction ID: {response.transaction.id}")
            print(f"Jenis: {response.transaction.jenis_transaksi}")
            print(f"Message: {response.message}")
            
            return response.transaction.id
        except grpc.RpcError as e:
            print(f"❌ FAILED: {e.code()} - {e.details()}")
            return None


async def test_idempotency():
    """
    Test idempotency - create same transaction twice with same key.
    """
    print("\n" + "=" * 60)
    print("TEST 3: Idempotency Check")
    print("=" * 60)
    
    async with grpc.aio.insecure_channel("localhost:7020") as channel:
        stub = pb_grpc.TransactionServiceStub(channel)
        
        idempotency_key = f"test_idempotency_{int(datetime.now().timestamp())}"
        
        penjualan = pb.TransaksiPenjualan(
            customer_name="Test Customer",
            items=[
                pb.ItemPenjualan(
                    sku="TEST001",
                    name="Test Item",
                    quantity=1,
                    unit="pcs",
                    unit_price=100000,
                    subtotal=100000
                )
            ],
            subtotal=100000,
            total_nominal=100000,
            payment_method="cash",
            payment_status="paid"
        )
        
        request = pb.CreateTransactionRequest(
            tenant_id="konsultanpsikologi",
            created_by="135f057c-7993-4040-8aaf-b6a96637d9a3",
            actor_role="staff",
            jenis_transaksi="penjualan",
            penjualan=penjualan,
            idempotency_key=idempotency_key
        )
        
        # First call
        try:
            response1 = await stub.CreateTransaction(request)
            tx_id_1 = response1.transaction.id
            print(f"✅ First call: Transaction created {tx_id_1}")
        except grpc.RpcError as e:
            print(f"❌ First call failed: {e.details()}")
            return
        
        # Second call (should return same transaction)
        try:
            response2 = await stub.CreateTransaction(request)
            tx_id_2 = response2.transaction.id
            
            if tx_id_1 == tx_id_2:
                print(f"✅ Idempotency working: Same transaction returned {tx_id_2}")
                print(f"   Message: {response2.message}")
            else:
                print(f"❌ Idempotency FAILED: Different IDs ({tx_id_1} vs {tx_id_2})")
        except grpc.RpcError as e:
            print(f"❌ Second call failed: {e.details()}")


async def test_list_transactions():
    """
    Test ListTransactions with filters.
    """
    print("\n" + "=" * 60)
    print("TEST 4: List Transactions")
    print("=" * 60)
    
    async with grpc.aio.insecure_channel("localhost:7020") as channel:
        stub = pb_grpc.TransactionServiceStub(channel)
        
        request = pb.ListTransactionsRequest(
            tenant_id="konsultanpsikologi",
            jenis_transaksi="",  # All types
            status="",  # All statuses
            page=1,
            page_size=10
        )
        
        try:
            response = await stub.ListTransactions(request)
            print(f"✅ SUCCESS")
            print(f"Total count: {response.total_count}")
            print(f"Page: {response.page}/{((response.total_count - 1) // response.page_size) + 1}")
            print(f"Transactions returned: {len(response.transactions)}")
            
            for i, tx in enumerate(response.transactions, 1):
                print(f"\n  {i}. {tx.id}")
                print(f"     Type: {tx.jenis_transaksi}")
                print(f"     Status: {tx.status}")
                print(f"     Created by: {tx.created_by}")
                
        except grpc.RpcError as e:
            print(f"❌ FAILED: {e.code()} - {e.details()}")


async def test_health_check():
    """
    Test HealthCheck endpoint.
    """
    print("\n" + "=" * 60)
    print("TEST 5: Health Check")
    print("=" * 60)
    
    from google.protobuf import empty_pb2
    
    async with grpc.aio.insecure_channel("localhost:7020") as channel:
        stub = pb_grpc.TransactionServiceStub(channel)
        
        try:
            response = await stub.HealthCheck(empty_pb2.Empty())
            print(f"✅ Service Status: {response.status}")
            print(f"   Version: {response.version}")
            print(f"   Timestamp: {response.timestamp}")
        except grpc.RpcError as e:
            print(f"❌ FAILED: {e.code()} - {e.details()}")


async def main():
    """
    Run all tests sequentially.
    """
    print("\n" + "🚀" * 30)
    print("TRANSACTION SERVICE - COMPREHENSIVE TEST")
    print("🚀" * 30 + "\n")
    
    # Test 1: Create penjualan
    tx_id_1 = await test_create_penjualan()
    
    # Test 2: Create beban
    tx_id_2 = await test_create_beban()
    
    # Test 3: Idempotency
    await test_idempotency()
    
    # Test 4: List transactions
    await test_list_transactions()
    
    # Test 5: Health check
    await test_health_check()
    
    print("\n" + "✅" * 30)
    print("ALL TESTS COMPLETED")
    print("✅" * 30 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
