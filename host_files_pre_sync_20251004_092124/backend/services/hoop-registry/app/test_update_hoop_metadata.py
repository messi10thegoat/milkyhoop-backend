import asyncio
import grpc
from app import hoop_registry_pb2 as pb
from app import hoop_registry_pb2_grpc as pb_grpc

async def run():
    async with grpc.aio.insecure_channel("localhost:5000") as channel:
        stub = pb_grpc.HoopRegistryStub(channel)
        
        request = pb.UpdateHoopMetadataRequest(
            name="ShowMenu",
            description="Updated description",
            input_schema='{"type": "object", "properties": {"query": {"type": "string"}}}',
            output_schema='{"type": "object", "properties": {"menu": {"type": "array"}}}',
            version="v1.1",
            target_service="chatbot_service",
            owner="admin_updated"
        )

        response = await stub.UpdateHoopMetadata(request)
        print("✅ Response:")
        print("Status:", response.status)
        print("Message:", response.message)

if __name__ == "__main__":
    asyncio.run(run())
