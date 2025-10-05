import asyncio
import grpc
from app import ragindex_service_pb2 as pb
from app import ragindex_service_pb2_grpc as pb_grpc

async def test_indexing():
    async with grpc.aio.insecure_channel("localhost:5006") as channel:
        stub = pb_grpc.RagIndexServiceStub(channel)

        # 1️⃣ Index a document
        embedding = [0.1] * 768  # dummy embedding
        index_resp = await stub.IndexDocument(pb.IndexDocumentRequest(
            doc_id=1,
            embedding=embedding
        ))
        print("✅ Indexed:", index_resp)

        # 2️⃣ Search document
        search_resp = await stub.SearchDocument(pb.SearchDocumentRequest(
            embedding=embedding,
            top_k=3
        ))
        print("✅ Search Results:")
        for result in search_resp.results:
            print(f"  Doc ID: {result.doc_id}, Score: {result.score}")

if __name__ == "__main__":
    asyncio.run(test_indexing())
