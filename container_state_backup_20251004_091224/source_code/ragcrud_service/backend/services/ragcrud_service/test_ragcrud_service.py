import asyncio
import grpc
from app import ragcrud_service_pb2 as pb
from app import ragcrud_service_pb2_grpc as pb_grpc

async def test_rag_crud():
    async with grpc.aio.insecure_channel("localhost:5001") as channel:
        stub = pb_grpc.RagCrudServiceStub(channel)

        # 1️⃣ Create
        create_resp = await stub.CreateRagDocument(pb.CreateRagDocumentRequest(
            tenant_id="tenant_001",
            title="Sample Title",
            content="Sample Content"
        ))
        print("✅ Created:", create_resp)

        # 2️⃣ List
        list_resp = await stub.ListRagDocuments(pb.ListRagDocumentsRequest())
        print("✅ List:", list_resp)

        # 3️⃣ Get
        get_resp = await stub.GetRagDocument(pb.GetRagDocumentRequest(id=create_resp.id))
        print("✅ Get:", get_resp)

        # 4️⃣ Update
        update_resp = await stub.UpdateRagDocument(pb.UpdateRagDocumentRequest(
            id=create_resp.id,
            title="Updated Title",
            content="Updated Content"
        ))
        print("✅ Updated:", update_resp)

        # 5️⃣ Delete
        delete_resp = await stub.DeleteRagDocument(pb.DeleteRagDocumentRequest(id=create_resp.id))
        print("✅ Deleted:", delete_resp)

if __name__ == "__main__":
    asyncio.run(test_rag_crud())
