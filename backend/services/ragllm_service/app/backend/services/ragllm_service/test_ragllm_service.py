import asyncio
import grpc
from app import ragllm_service_pb2 as pb
from app import ragllm_service_pb2_grpc as pb_grpc

async def test_generate_embedding():
    async with grpc.aio.insecure_channel("localhost:5000") as channel:
        stub = pb_grpc.RagLlmServiceStub(channel)

        # Test: Generate embedding
        resp = await stub.GenerateEmbedding(pb.EmbeddingRequest(text="Hello world!"))
        print("✅ Embedding:", resp.embedding)

if __name__ == "__main__":
    asyncio.run(test_generate_embedding())
