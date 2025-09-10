import asyncio
import grpc
from milkyhoop_protos import ragcrud_service_pb2 as crud_pb
from milkyhoop_protos import ragcrud_service_pb2_grpc as crud_pb_grpc

async def get_doc(doc_id):
    async with grpc.aio.insecure_channel("ragcrud_service:5001") as channel:
        stub = crud_pb_grpc.RagCrudServiceStub(channel)
        request = crud_pb.GetRagDocumentRequest(id=doc_id)
        response = await stub.GetRagDocument(request)
        print(f"Dokumen ID {doc_id}:")
        print(response.content)

if __name__ == "__main__":
    import sys
    doc_id = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    asyncio.run(get_doc(doc_id))
