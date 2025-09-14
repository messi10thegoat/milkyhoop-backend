import grpc
from typing import Dict, Any, Optional
import asyncio
import json
import logging

# Import protobuf for tenant_parser gRPC communication
import sys
sys.path.append('/app/backend/api_gateway/libs/milkyhoop_protos')

try:
    from tenant_parser_pb2 import IntentParserRequest
    from tenant_parser_pb2_grpc import IntentParserServiceStub
    GRPC_AVAILABLE = True
except ImportError:
    GRPC_AVAILABLE = False

logger = logging.getLogger(__name__)

class TenantParserClient:
    def __init__(self, host: str = "tenant_parser", port: int = 5012):
        """Initialize REAL tenant parser gRPC client for Enhanced Confidence Engine"""
        self.host = host
        self.port = port
        self.target = f"{host}:{port}"
        self.channel: Optional[grpc.aio.Channel] = None
        self.stub: Optional[IntentParserServiceStub] = None
        print(f"🎯 REAL TenantParserClient initialized for Enhanced Confidence Engine: {self.target}")

    async def connect(self):
        """Establish gRPC connection to tenant_parser service"""
        if not GRPC_AVAILABLE:
            print("⚠️ gRPC protobuf not available, falling back to direct RAG")
            return False
            
        try:
            if self.channel is None:
                self.channel = grpc.aio.insecure_channel(self.target)
                self.stub = IntentParserServiceStub(self.channel)
                print(f"🔗 Connected to Enhanced Confidence Engine: {self.target}")
            return True
        except Exception as e:
            print(f"❌ gRPC connection failed: {e}")
            return False

    async def close(self):
        """Close gRPC connection"""
        if self.channel:
            await self.channel.close()
            self.channel = None
            self.stub = None
            print("🔐 Enhanced Confidence Engine connection closed")

    async def parse_customer_query(self, tenant_id: str, message: str, session_id: str = None) -> Dict[str, Any]:
        """
        REAL Enhanced Confidence Engine Implementation
        Routes through tenant_parser service for:
        - Universal confidence calculation
        - 3-tier routing (direct FAQ / GPT-3.5 synthesis / deep analysis)
        - Cost optimization (98.7% reduction target)
        """
        try:
            print(f"🎯 Enhanced Confidence Engine: Processing {tenant_id} query: {message[:50]}...")
            
            # Try Enhanced Confidence Engine first
            grpc_connected = await self.connect()
            
            if grpc_connected and self.stub:
                try:
                    # Create proper gRPC request for Enhanced Confidence Engine
                    request = IntentParserRequest(
                        input=message,
                        user_id=session_id or "default"
                    )
                    
                    # Add tenant_id to gRPC metadata
                    metadata = [('tenant-id', tenant_id)]
                    
                    # Call Enhanced Confidence Engine in tenant_parser service
                    print(f"📡 Calling Enhanced Confidence Engine: {self.target}")
                    response = await self.stub.DoSomething(request, metadata=metadata, timeout=10.0)
                    
                    if response.status == "success":
                        # Parse JSON result from Enhanced Confidence Engine
                        parsed_result = json.loads(response.result)
                        
                        # Extract confidence metadata from Enhanced Confidence Engine
                        confidence_metadata = parsed_result.get("confidence_metadata", {})
                        route_taken = confidence_metadata.get("route_taken", "unknown")
                        cost_estimate = confidence_metadata.get("cost_estimate", 0.0)
                        
                        # Return structured response with REAL confidence data
                        result = {
                            "intent": parsed_result.get("intent", "general_inquiry"),
                            "confidence": confidence_metadata.get("confidence_score", 0.0),
                            "entities": parsed_result.get("entities", {}),
                            "answer": parsed_result.get("response", "Informasi tidak tersedia"),
                            "confidence_metadata": confidence_metadata,
                            "method": "enhanced_confidence_engine"
                        }
                        
                        print(f"✅ Enhanced Confidence Engine Success: Route={route_taken}, Cost=Rp{cost_estimate}")
                        return result
                        
                    else:
                        print(f"⚠️ Enhanced Confidence Engine error: {response.status}")
                        
                except grpc.RpcError as e:
                    print(f"❌ gRPC call failed: {e}")
                except json.JSONDecodeError as e:
                    print(f"❌ JSON parsing failed: {e}")
                except Exception as e:
                    print(f"❌ Enhanced Confidence Engine call failed: {e}")
            
            # Fallback to direct RAG CRUD (backward compatibility)
            print("🔄 Fallback: Using direct RAG CRUD approach")
            return await self._fallback_rag_direct(tenant_id, message, session_id)
                
        except Exception as e:
            print(f"❌ Customer query processing error: {e}")
            return {
                "intent": "general_inquiry",
                "confidence": 0.0,
                "entities": {"query": message, "tenant": tenant_id, "error": str(e)},
                "answer": "Maaf, terjadi kesalahan sistem. Silakan coba lagi.",
                "confidence_metadata": {
                    "confidence_score": 0.0,
                    "route_taken": "error_fallback",
                    "cost_estimate": 0.0,
                    "tokens_used": 0,
                    "optimization_active": False
                },
                "method": "error_fallback"
            }

    async def _fallback_rag_direct(self, tenant_id: str, message: str, session_id: str = None) -> Dict[str, Any]:
        """
        Fallback to direct RAG CRUD (preserved from original implementation)
        Used when Enhanced Confidence Engine is not available
        """
        try:
            # Import RAG CRUD client (same as original approach)
            from .ragcrud_client import RagCrudClient
            
            # Use RAG CRUD for semantic search
            rag_client = RagCrudClient()
            
            # Search for relevant FAQ
            search_results = await rag_client.fuzzy_search_documents(
                tenant_id=tenant_id,
                search_content=message,
                similarity_threshold=0.7
            )
            
            # Process search results
            if search_results and len(search_results) > 0:
                best_match = search_results[0]
                similarity_score = getattr(best_match, 'similarity_score', 0.8)
                
                # Calculate basic confidence (fallback logic)
                confidence = min(similarity_score + 0.1, 1.0)  # Slight boost for fallback
                
                # Determine intent based on message content
                intent = self._classify_intent(message)
                
                result = {
                    "intent": intent,
                    "confidence": confidence,
                    "entities": {
                        "query": message,
                        "tenant": tenant_id,
                        "matched_content": getattr(best_match, 'content', ''),
                        "doc_id": getattr(best_match, 'id', None)
                    },
                    "answer": getattr(best_match, 'content', 'Informasi tidak ditemukan'),
                    "confidence_metadata": {
                        "confidence_score": confidence,
                        "route_taken": "fallback_direct_faq",
                        "cost_estimate": 0.0,
                        "tokens_used": 0,
                        "optimization_active": False
                    },
                    "method": "rag_direct_fallback"
                }
                
                print(f"✅ Fallback RAG success: {intent} (confidence: {confidence:.3f})")
                return result
            else:
                # No matches found
                result = {
                    "intent": "general_inquiry",
                    "confidence": 0.3,
                    "entities": {"query": message, "tenant": tenant_id},
                    "answer": "Maaf, informasi yang Anda cari belum tersedia. Silakan hubungi customer service kami.",
                    "confidence_metadata": {
                        "confidence_score": 0.3,
                        "route_taken": "no_match_deflection",
                        "cost_estimate": 0.0,
                        "tokens_used": 0,
                        "optimization_active": False
                    },
                    "method": "rag_direct_fallback"
                }
                
                print(f"⚠️ No matches found for query: {message}")
                return result
                
        except Exception as e:
            print(f"❌ Fallback RAG error: {e}")
            return {
                "intent": "general_inquiry",
                "confidence": 0.0,
                "entities": {"query": message, "tenant": tenant_id, "error": str(e)},
                "answer": "Maaf, terjadi kesalahan sistem. Silakan coba lagi.",
                "confidence_metadata": {
                    "confidence_score": 0.0,
                    "route_taken": "system_error",
                    "cost_estimate": 0.0,
                    "tokens_used": 0,
                    "optimization_active": False
                },
                "method": "error_fallback"
            }

    def _classify_intent(self, message: str) -> str:
        """Simple intent classification based on keywords (preserved from original)"""
        message_lower = message.lower()
        
        if any(word in message_lower for word in ['harga', 'biaya', 'tarif', 'cost', 'berapa']):
            return "pricing_inquiry"
        elif any(word in message_lower for word in ['bahan', 'material', 'terbuat', 'dari']):
            return "product_inquiry"
        elif any(word in message_lower for word in ['cara', 'bagaimana', 'how', 'gimana', 'order', 'pesan']):
            return "booking_request"
        elif any(word in message_lower for word in ['jam', 'buka', 'tutup', 'operasional', 'open']):
            return "schedule_inquiry"
        elif any(word in message_lower for word in ['lokasi', 'alamat', 'dimana', 'where']):
            return "location_inquiry"
        else:
            return "general_inquiry"