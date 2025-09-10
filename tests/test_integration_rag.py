import pytest
import asyncio
import grpc
import numpy as np

# ✅ Import stub gRPC dari hasil generate milkyhoop_protos
from milkyhoop_protos import ragcrud_service_pb2 as crud_pb
from milkyhoop_protos import ragcrud_service_pb2_grpc as crud_pb_grpc
from milkyhoop_protos import ragindex_service_pb2 as index_pb
from milkyhoop_protos import ragindex_service_pb2_grpc as index_pb_grpc
from milkyhoop_protos import ragllm_service_pb2 as llm_pb
from milkyhoop_protos import ragllm_service_pb2_grpc as llm_pb_grpc

@pytest.mark.asyncio
async def test_integration_rag():
    # ✅ 1. Create document di ragcrud_service
    async with grpc.aio.insecure_channel("localhost:5001") as channel_crud:
        crud_stub = crud_pb_grpc.RagCrudServiceStub(channel_crud)
        create_resp = await crud_stub.CreateRagDocument(crud_pb.CreateRagDocumentRequest(
            tenant_id="tenant_001",
            title="Promo Cappuccino",
            content="Promo diskon 20% untuk Cappuccino di Cafe Anna!"
        ))
        print("✅ Created doc:", f"id: {create_resp.id}, title: {create_resp.title}")

    # ✅ 2. Index document di ragindex_service
    async with grpc.aio.insecure_channel("localhost:5006") as channel_index:
        index_stub = index_pb_grpc.RagIndexServiceStub(channel_index)
        embedding = np.random.rand(768).astype(np.float32).tolist()
        index_resp = await index_stub.IndexDocument(index_pb.IndexDocumentRequest(
            doc_id=create_resp.id,
            embedding=embedding
        ))
        print("✅ Indexed doc:", index_resp.status)

    # ✅ 3. Generate embedding di ragllm_service
    async with grpc.aio.insecure_channel("localhost:5000") as channel_llm:
        llm_stub = llm_pb_grpc.RagLlmServiceStub(channel_llm)
        llm_resp = await llm_stub.GenerateEmbedding(llm_pb.EmbeddingRequest(
            text="Apakah ada promo cappuccino?"
        ))
        print("✅ LLM Embedding sample:", llm_resp.embedding[:10], "... (truncated)")

    print("🎉 Integration test selesai! Semua modul sinkron & live.")
