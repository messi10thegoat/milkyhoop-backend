import asyncio
import grpc
from milkyhoop_protos import ragllm_service_pb2, ragllm_service_pb2_grpc

async def main():
    question = "Saya tidak bisa coding, apakah tetap bisa menggunakan MilkyHoop?"
    channel = grpc.aio.insecure_channel("localhost:5000")
    stub = ragllm_service_pb2_grpc.RagLlmServiceStub(channel)


    request = ragllm_service_pb2.GenerateAnswerRequest(
        tenant_id="milkyhoop-system",
        question=question
    )

    response = await stub.GenerateAnswer(request)
    print("✅ Jawaban dari RAG LLM:")
    print(response.answer)

asyncio.run(main())
