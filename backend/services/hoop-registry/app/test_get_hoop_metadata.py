import asyncio
import grpc
from app import hoop_registry_pb2 as pb
from app import hoop_registry_pb2_grpc as pb_grpc

async def run():
    async with grpc.aio.insecure_channel("localhost:5000") as channel:
        stub = pb_grpc.HoopRegistryStub(channel)
        
        request = pb.GetHoopMetadataRequest(
            name="ShowMenu"
        )

        try:
            response = await stub.GetHoopMetadata(request)
            print("✅ Metadata found:")
            print("Name:", response.name)
            print("Description:", response.description)
            print("Input Schema:", response.input_schema)
            print("Output Schema:", response.output_schema)
            print("Version:", response.version)
            print("Target Service:", response.target_service)
            print("Owner:", response.owner)
            print("Created At:", response.created_at)
        except grpc.aio.AioRpcError as e:
            print("❌ Error:", e.details())

if __name__ == "__main__":
    asyncio.run(run())
