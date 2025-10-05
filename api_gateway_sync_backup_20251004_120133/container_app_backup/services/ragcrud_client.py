import os
import asyncio
import logging
from typing import Optional, List
import grpc
from milkyhoop_protos import ragcrud_service_pb2_grpc, ragcrud_service_pb2

logger = logging.getLogger(__name__)

class RagCrudClient:
    def __init__(self, host: str = None, port: int = None, timeout: float = 5.0):
        host = host or os.getenv("RAGCRUD_GRPC_HOST", "ragcrud_service")
        port = port or int(os.getenv("RAGCRUD_GRPC_PORT", "5001"))
        self.target = f"{host}:{port}"
        self.timeout = timeout
        self.channel: Optional[grpc.aio.Channel] = None
        self.stub: Optional[ragcrud_service_pb2_grpc.RagCrudServiceStub] = None

    async def connect(self):
        if self.channel is None:
            self.channel = grpc.aio.insecure_channel(self.target)
            self.stub = ragcrud_service_pb2_grpc.RagCrudServiceStub(self.channel)
            logger.info(f"Connected to RagCRUD gRPC service at {self.target}")

    async def close(self):
        if self.channel:
            await self.channel.close()
            self.channel = None
            self.stub = None
            logger.info("RagCRUD gRPC channel closed")

    async def list_documents(self, tenant_id: str = None):
        await self.connect()
        request = ragcrud_service_pb2.ListRagDocumentsRequest(tenant_id=tenant_id)
        try:
            response = await asyncio.wait_for(
                self.stub.ListRagDocuments(request),
                timeout=self.timeout
            )
            return response
        except grpc.RpcError as e:
            logger.error(f"gRPC error: {e.code()} - {e.details()}")
            raise
        except asyncio.TimeoutError:
            logger.error("Timeout while calling RagCRUD service")
            raise

    async def get_document(self, doc_id: int):
        await self.connect()
        request = ragcrud_service_pb2.GetRagDocumentRequest(id=doc_id)
        try:
            response = await asyncio.wait_for(
                self.stub.GetRagDocument(request),
                timeout=self.timeout
            )
            return response
        except grpc.RpcError as e:
            logger.error(f"gRPC error: {e.code()} - {e.details()}")
            raise
        except asyncio.TimeoutError:
            logger.error("Timeout while calling RagCRUD service")
            raise

    async def create_document(
        self,
        tenant_id: str,
        title: str,
        content: str,
        source: str,
        tags: List[str]
    ):
        await self.connect()
        request = ragcrud_service_pb2.CreateRagDocumentRequest(
            tenant_id=tenant_id,
            title=title,
            content=content,
            source=source,
            tags=tags
        )
        try:
            response = await asyncio.wait_for(
                self.stub.CreateRagDocument(request),
                timeout=self.timeout
            )
            return response
        except grpc.RpcError as e:
            logger.error(f"gRPC error: {e.code()} - {e.details()}")
            raise
        except asyncio.TimeoutError:
            logger.error("Timeout while calling RagCRUD service")
            raise

    async def search_documents(self, tenant_id: str, query: str, top_k: int = 5):
        """
        Call ragcrud_service fuzzy search (same as setup mode)
        """
        await self.connect()
        
        # Check if FuzzySearchDocuments method exists
        try:
            request = ragcrud_service_pb2.FuzzySearchRequest(
                tenant_id=tenant_id,
                search_content=query,
                similarity_threshold=0.7
            )
            response = await asyncio.wait_for(
                self.stub.FuzzySearchDocuments(request),
                timeout=self.timeout
            )
            return response.documents[:top_k]
            
        except (grpc.RpcError, AttributeError) as e:
            # Fallback to basic search if fuzzy search not available
            logger.warning(f"Fuzzy search failed, using basic search: {e}")
            
            # Get all documents and do basic filtering
            response = await self.list_documents(tenant_id)
            docs = response.documents
            
            if not docs:
                return []
            
            # Basic contains search
            matches = []
            query_lower = query.lower()
            
            for doc in docs:
                if (query_lower in doc.title.lower() or 
                    query_lower in doc.content.lower()):
                    matches.append(doc)
            
            return matches[:top_k]

    async def fuzzy_search_documents(self, tenant_id: str, search_content: str, similarity_threshold: float = 0.7):
        """
        Direct call to ragcrud_service fuzzy search
        """
        await self.connect()
        request = ragcrud_service_pb2.FuzzySearchRequest(
            tenant_id=tenant_id,
            search_content=search_content,
            similarity_threshold=similarity_threshold
        )
        try:
            response = await asyncio.wait_for(
                self.stub.FuzzySearchDocuments(request),
                timeout=self.timeout
            )
            return response.documents
        except grpc.RpcError as e:
            logger.error(f"Fuzzy search gRPC error: {e.code()} - {e.details()}")
            raise
        except asyncio.TimeoutError:
            logger.error("Timeout while calling fuzzy search")
            raise