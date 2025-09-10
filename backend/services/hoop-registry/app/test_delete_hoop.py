import asyncio
import grpc
from app import hoop_registry_pb2 as pb
from app import hoop_registry_pb2_grpc as pb_grpc

async def run():
    async with grpc.aio.insecure_channel("localhost:5000") as channel:
        stub = pb_grpc.HoopRegistryStub(channel)
        
        request = pb.DeleteHoopRequest(
            name="ShowMenu"
        )

        response = await stub.DeleteHoop(request)
        print("✅ Response:")
        print("Status:", response.status)
        print("Message:", response.message)

if __name__ == "__main__":
    asyncio.run(run())
