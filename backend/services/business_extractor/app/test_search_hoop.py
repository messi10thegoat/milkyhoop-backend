import asyncio
import grpc
from app import business_extractor_pb2 as pb
from app import business_extractor_pb2_grpc as pb_grpc

async def run():
    async with grpc.aio.insecure_channel("localhost:5000") as channel:
        stub = pb_grpc.HoopRegistryStub(channel)
        
        request = pb.SearchHoopRequest(
            keyword="menu"
        )

        response = await stub.SearchHoop(request)
        print(f"✅ Found {len(response.hoops)} hoops matching 'menu':")
        for hoop in response.hoops:
            print("📦", hoop.name, "-", hoop.description)

if __name__ == "__main__":
    asyncio.run(run())
