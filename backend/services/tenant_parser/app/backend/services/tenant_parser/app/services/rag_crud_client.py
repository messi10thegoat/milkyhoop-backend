import grpc
import asyncio
from typing import List, Optional
import logging

logger = logging.getLogger(__name__)

class RagCrudService:
    """RagCrudService client for tenant_parser to call ragcrud_service via gRPC"""
    
    def __init__(self):
        self.RAG_CRUD_AVAILABLE = False
        self._rag_crud_stub = None
        self._initialize_grpc_connection()
    
    def _initialize_grpc_connection(self):
        """Initialize gRPC connection to ragcrud_service"""
        try:
            # Import from correct tenant_parser app directory (not libs!)
            import sys
            sys.path.append('/app/backend/services/tenant_parser/app')
            
            try:
                # Try direct import from app directory
                import rag_pb2_grpc
                import rag_pb2
                logger.info("✅ Imported rag_pb2_grpc from app directory")
            except ImportError:
                try:
                    # Try ragcrud_service proto files
                    import ragcrud_service_pb2_grpc as rag_pb2_grpc
                    import ragcrud_service_pb2 as rag_pb2
                    logger.info("✅ Imported ragcrud_service_pb2_grpc from app directory")
                except ImportError:
                    raise ImportError("Cannot find rag proto files in app directory")
            
            # Create gRPC channel to ragcrud_service (port 5001)
            channel = grpc.insecure_channel('ragcrud_service:5001')
            self._rag_crud_stub = rag_pb2_grpc.RagCrudServiceStub(channel)
            self.RAG_CRUD_AVAILABLE = True
            logger.info("✅ RAG CRUD gRPC connection established")
            
        except Exception as e:
            logger.warning(f"❌ RAG CRUD gRPC connection failed: {e}")
            self.RAG_CRUD_AVAILABLE = False
            self._rag_crud_stub = None
    
    async def fuzzy_search_documents(self, tenant_id: str, query: str, max_results: int = 10) -> List[dict]:
        """Search documents using fuzzy matching via gRPC"""
        if not self.RAG_CRUD_AVAILABLE or not self._rag_crud_stub:
            logger.warning("RAG CRUD not available for fuzzy search")
            return []
            
        try:
            # Import protobuf request/response classes from app directory
            import sys
            sys.path.append('/app/backend/services/tenant_parser/app')
            
            try:
                import rag_pb2
            except ImportError:
                import ragcrud_service_pb2 as rag_pb2
            
            # Create gRPC request
            request = rag_pb2.FuzzySearchRequest(
                tenant_id=tenant_id,
                query=query,
                max_results=max_results
            )
            
            # Make gRPC call
            response = self._rag_crud_stub.FuzzySearch(request)
            logger.info(f"✅ FuzzySearch gRPC call successful for tenant {tenant_id}")
            
            # Convert response to list of dicts
            results = []
            for doc in response.documents:
                results.append({
                    'id': doc.id,
                    'content': doc.content,
                    'similarity_score': doc.similarity_score
                })
            
            return results
            
        except Exception as e:
            logger.error(f"❌ FuzzySearch gRPC call failed: {e}")
            return []
    
    def test_connection(self) -> bool:
        """Test if gRPC connection is working"""
        return self.RAG_CRUD_AVAILABLE and self._rag_crud_stub is not None
