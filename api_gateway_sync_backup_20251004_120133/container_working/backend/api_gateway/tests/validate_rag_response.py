import asyncio
import grpc
import os
import sys

sys.path.append(os.path.abspath("./backend/api_gateway/libs"))

from milkyhoop_protos.ragllm_service_pb2 import RagRequest
from milkyhoop_protos.ragllm_service_pb2_grpc import RagLLMServiceStub

async def test_rag_response(query: str):
    target = os.getenv("RAG_GRPC_HOST", "ragllm_service:5007")
    async with grpc.aio.insecure_channel(target) as channel:
        stub = RagLLMServiceStub(channel)
        response = await stub.GenerateRagResponse(RagRequest(
            user_id="devtest",
            session_id="sess-devtest",
            tenant_id="milkyhoop-system",
            query=query
        ))
        print("=== RAG RESPONSE ===")
        print(response.message)

if __name__ == "__main__":
    query = "Kalau kamu adalah alumni yang membaca buku ini, perasaan apa yang muncul?"
    asyncio.run(test_rag_response(query))
