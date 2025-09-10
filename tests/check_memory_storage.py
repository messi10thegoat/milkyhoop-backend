import asyncio
import grpc
import json
import sys
sys.path.append('backend/api_gateway/libs')

from milkyhoop_protos import memory_service_pb2_grpc, memory_service_pb2

async def check_stored_memory():
    async with grpc.aio.insecure_channel("localhost:5000") as channel:
        stub = memory_service_pb2_grpc.MemoryServiceStub(channel)
        
        get_request = memory_service_pb2.GetMemoryRequest(
            user_id="arif",
            tenant_id="konsultanpsikologi", 
            key="last_faq_action"
        )
        
        response = await stub.GetMemory(get_request)
        print(f"Memory found: {response.found}")
        if response.found:
            print(f"Stored context: {response.value}")

asyncio.run(check_stored_memory())
