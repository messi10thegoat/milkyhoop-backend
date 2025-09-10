import asyncio
import grpc
from openai import OpenAI
from milkyhoop_protos import ragindex_service_pb2 as index_pb
from milkyhoop_protos import ragindex_service_pb2_grpc as index_pb_grpc

import os
openai_api_key = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=openai_api_key)

async def generate_embedding(text: str):
    response = await asyncio.to_thread(
        client.embeddings.create,
        model="text-embedding-3-small",
        input=text
    )
    return response.data[0].embedding

async def validate_search(query: str):
    embedding = await generate_embedding(query)

    async with grpc.aio.insecure_channel("ragindex_service:5006") as index_channel:
        index_stub = index_pb_grpc.RagIndexServiceStub(index_channel)
        search_request = index_pb.SearchDocumentRequest(
            embedding=embedding,
            top_k=5
        )
        search_response = await index_stub.SearchDocument(search_request)
        print(f"Search results for query '{query}':")
        for res in search_response.results:
            print(f"- doc_id: {res.doc_id}, score: {res.score}")

if __name__ == "__main__":
    query = "FAQ Buku Reuni Van Lith"
    asyncio.run(validate_search(query))
