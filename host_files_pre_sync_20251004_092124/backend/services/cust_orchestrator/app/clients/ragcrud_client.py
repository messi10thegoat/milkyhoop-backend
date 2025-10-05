"""
RAG CRUD gRPC Client - PURE gRPC IMPLEMENTATION
Handles FAQ search and context retrieval via actual gRPC calls only
"""
import grpc
import asyncio
import logging
from typing import Dict, Any, List, Optional
import sys

logger = logging.getLogger(__name__)

class RAGCRUDClient:
    """Pure gRPC client for RAG CRUD service communication"""
    
    def __init__(self, host: str = "milkyhoop-dev-ragcrud_service-1", port: int = 5001):
        self.endpoint = f"{host}:{port}"
        self.timeout = 15
        self.max_retries = 2
        logger.info(f"Real gRPC RAGCRUDClient initialized: {self.endpoint}")
    
    async def search_faq(
        self, 
        query: str, 
        tenant_id: str, 
        intent: str = "general_inquiry"
    ) -> Dict[str, Any]:
        """
        Search FAQ database for relevant context via real gRPC call
        
        Returns:
            Dict with actual FAQ results, confidence scores, and context
        """
        try:
            # Import protobuf stubs using working pattern from other clients
            try:
                sys.path.append('/app/protos')
                import ragcrud_service_pb2 as pb
                import ragcrud_service_pb2_grpc as pb_grpc
            except ImportError as e:
                logger.error(f"[Real gRPC] Protobuf import failed: {e}")
                return {
                    "results": [],
                    "confidence": 0.0,
                    "direct_answer": None,
                    "relevant_faqs": [],
                    "status": "error",
                    "error": f"Protobuf import failed: {e}",
                    "source": "import_error"
                }

            for attempt in range(self.max_retries + 1):
                try:
                    # Create gRPC channel
                    channel = grpc.aio.insecure_channel(self.endpoint)
                    
                    try:
                        # Create gRPC stub
                        stub = pb_grpc.RagCrudServiceStub(channel)
                        
                        # Create request message
                        request = pb.FuzzySearchRequest()
                        request.tenant_id = tenant_id
                        request.search_content = query
                        request.similarity_threshold = 0.1
                        
                        logger.info(f"[Real gRPC] Searching FAQ for tenant {tenant_id}: {query[:30]}...")
                        
                        # Make gRPC call with timeout
                        response = await asyncio.wait_for(
                            stub.FuzzySearchDocuments(request),
                            timeout=self.timeout
                        )
                        
                        # Process response
                        if response and hasattr(response, 'documents') and response.documents:
                            # Extract best match for confidence calculation
                            best_match = response.documents[0] if response.documents else None
                            confidence = float(getattr(best_match, 'similarity_score', 0.0)) if best_match else 0.0
                            
                            # Convert documents to dict format
                            results = []
                            for doc in response.documents:
                                results.append({
                                    "id": getattr(doc, 'id', ''),
                                    "content": getattr(doc, 'content', ''),
                                    "score": float(getattr(doc, 'similarity_score', 0.0)),
                                    "metadata": dict(getattr(doc, 'metadata', {})) if hasattr(doc, 'metadata') else {}
                                })
                            
                            logger.info(f"[Real gRPC] Found {len(results)} FAQs, best score: {confidence:.3f}")
                            
                            return {
                                "results": results,
                                "confidence": confidence,
                                "direct_answer": getattr(best_match, 'content', '') if confidence >= 0.9 else None,
                                "relevant_faqs": results,
                                "status": "success",
                                "source": "real_grpc_ragcrud"
                            }
                        else:
                            logger.warning(f"[Real gRPC] Empty response from RAG CRUD service")
                            return {
                                "results": [],
                                "confidence": 0.0,
                                "direct_answer": None,
                                "relevant_faqs": [],
                                "status": "no_results",
                                "source": "real_grpc_ragcrud"
                            }
                            
                    finally:
                        await channel.close()
                        
                except asyncio.TimeoutError:
                    if attempt == self.max_retries:
                        logger.error(f"[Real gRPC] Timeout after {self.timeout}s")
                        return {
                            "results": [],
                            "confidence": 0.0,
                            "direct_answer": None,
                            "relevant_faqs": [],
                            "status": "error",
                            "error": f"Timeout after {self.timeout}s",
                            "source": "timeout_error"
                        }
                    else:
                        logger.warning(f"[Real gRPC] Timeout on attempt {attempt + 1}, retrying...")
                        await asyncio.sleep(0.5 * (attempt + 1))
                        
                except grpc.RpcError as e:
                    if attempt == self.max_retries:
                        logger.error(f"[Real gRPC] RPC error: {e.code()} - {e.details()}")
                        return {
                            "results": [],
                            "confidence": 0.0,
                            "direct_answer": None,
                            "relevant_faqs": [],
                            "status": "error",
                            "error": f"gRPC error: {e.details()}",
                            "source": "grpc_error"
                        }
                    else:
                        logger.warning(f"[Real gRPC] RPC error on attempt {attempt + 1}, retrying...")
                        await asyncio.sleep(0.5 * (attempt + 1))
                        
        except Exception as e:
            logger.error(f"[Real gRPC] FAQ search failed: {str(e)}")
            return {
                "results": [],
                "confidence": 0.0,
                "direct_answer": None,
                "relevant_faqs": [],
                "status": "error", 
                "error": str(e),
                "source": "general_error"
            }
    
    async def health_check(self) -> bool:
        """
        Check RAG CRUD service health via gRPC call
        """
        try:
            sys.path.append('/app/protos')
            import ragcrud_service_pb2_grpc as pb_grpc
            from google.protobuf.empty_pb2 import Empty
            
            channel = grpc.aio.insecure_channel(self.endpoint)
            
            try:
                stub = pb_grpc.RagCrudServiceStub(channel)
                
                response = await asyncio.wait_for(
                    stub.HealthCheck(Empty()),
                    timeout=3.0
                )
                
                logger.info("[Real gRPC] RAG CRUD health check: healthy")
                return True
                
            finally:
                await channel.close()
                
        except Exception as e:
            logger.warning(f"[Real gRPC] RAG CRUD health check failed: {e}")
            return False