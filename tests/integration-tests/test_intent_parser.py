import asyncio
import grpc

from milkyhoop_protos import intent_parser_pb2 as pb
from milkyhoop_protos import intent_parser_pb2_grpc as pb_grpc

async def test_connection():
    async with grpc.aio.insecure_channel("localhost:5002") as channel:
        stub = pb_grpc.IntentParserServiceStub(channel)
        request = pb.IntentParserRequest(user_id="user123", input="Halo dunia")
        response = await stub.DoSomething(request)
        print("✅ Response:", response)

if __name__ == "__main__":
    asyncio.run(test_connection())
