import asyncio
import grpc
import json
import sys
sys.path.append('backend/api_gateway/libs')

from milkyhoop_protos import memory_service_pb2_grpc, memory_service_pb2

async def test_memory_service():
    """Test Memory Service CRUD operations"""
    print("🧪 Testing Memory Service Standalone...")
    
    # Connect to Memory Service
    memory_host = "localhost:5000"
    
    try:
        async with grpc.aio.insecure_channel(memory_host) as channel:
            stub = memory_service_pb2_grpc.MemoryServiceStub(channel)
            
            # Test data
            user_id = "test_user"
            tenant_id = "test_tenant"
            test_key = "last_faq_action"
            test_value = {
                "intent": "faq_create",
                "entity": "harga konsultasi",
                "content": "Rp 200rb per sesi",
                "timestamp": "2025-07-14T07:30:00"
            }
            
            print(f"📝 Test 1: Store Memory")
            store_request = memory_service_pb2.StoreMemoryRequest(
                user_id=user_id,
                tenant_id=tenant_id,
                key=test_key,
                value=json.dumps(test_value),
                ttl=3600
            )
            
            store_response = await stub.StoreMemory(store_request)
            print(f"   Result: {store_response.success} - {store_response.message}")
            
            print(f"📖 Test 2: Get Memory")
            get_request = memory_service_pb2.GetMemoryRequest(
                user_id=user_id,
                tenant_id=tenant_id,
                key=test_key
            )
            
            get_response = await stub.GetMemory(get_request)
            print(f"   Found: {get_response.found}")
            if get_response.found:
                retrieved_value = json.loads(get_response.value)
                print(f"   Value: {retrieved_value}")
            
            print(f"✏️ Test 3: Update Memory")
            updated_value = test_value.copy()
            updated_value["content"] = "Rp 250rb per sesi"
            updated_value["updated"] = True
            
            update_request = memory_service_pb2.UpdateMemoryRequest(
                user_id=user_id,
                tenant_id=tenant_id,
                key=test_key,
                value=json.dumps(updated_value)
            )
            
            update_response = await stub.UpdateMemory(update_request)
            print(f"   Result: {update_response.success} - {update_response.message}")
            
            print(f"📋 Test 4: List Memories")
            list_request = memory_service_pb2.ListMemoriesRequest(
                user_id=user_id,
                tenant_id=tenant_id
            )
            
            list_response = await stub.ListMemories(list_request)
            print(f"   Count: {list_response.count}")
            for memory in list_response.memories:
                print(f"   Key: {memory.key}, Value: {memory.value[:50]}...")
            
            print(f"🗑️ Test 5: Clear Memory")
            clear_request = memory_service_pb2.ClearMemoryRequest(
                user_id=user_id,
                tenant_id=tenant_id,
                key=test_key
            )
            
            clear_response = await stub.ClearMemory(clear_request)
            print(f"   Result: {clear_response.success} - {clear_response.message}")
            
            print("✅ All tests completed!")
            
    except Exception as e:
        print(f"❌ Test failed: {e}")

if __name__ == "__main__":
    asyncio.run(test_memory_service())
