import asyncio
import grpc
from app import business_extractor_pb2 as pb
from app import business_extractor_pb2_grpc as pb_grpc

async def run():
    async with grpc.aio.insecure_channel("localhost:5000") as channel:
        stub = pb_grpc.HoopRegistryStub(channel)
        
        request = pb.RegisterHoopRequest(
            name="ShowMenu",
            description="Menampilkan daftar menu",
            input_schema='{"type": "object", "properties": {}}',
            output_schema='{"type": "object", "properties": {}}',
            version="v1.0",
            target_service="chatbot_service",
            owner="admin"
        )

        response = await stub.RegisterHoop(request)
        print("✅ Response:")
        print("Status:", response.status)
        print("Message:", response.message)

if __name__ == "__main__":
    asyncio.run(run())
