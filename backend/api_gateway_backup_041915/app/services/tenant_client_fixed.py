import grpc
import asyncio
import logging
import json
from typing import Dict, Any
from libs.milkyhoop_protos import tenant_parser_pb2_grpc, tenant_parser_pb2

logger = logging.getLogger(__name__)

class TenantParserClient:
    def __init__(self, host: str = "tenant_parser", port: int = 5012):
        self.target = f"{host}:{port}"
        self.channel = grpc.aio.insecure_channel(self.target)
        self.stub = tenant_parser_pb2_grpc.TenantParserStub(self.channel)
        logger.info(f"✅ TenantParserClient initialized for {self.target}")
    
    async def parse_customer_query(self, tenant_id: str, message: str, session_id: str) -> Dict[str, Any]:
        """Parse customer query using tenant parser gRPC service"""
        try:
            logger.info(f"🔄 Calling tenant parser: tenant={tenant_id}, message={message}")
            
            # Create the gRPC request
            request = tenant_parser_pb2.ParseRequest(
                user_id=session_id,
                message=message,
                tenant_id=tenant_id
            )
            
            # Call the gRPC service
            response = await self.stub.Parse(request)
            
            logger.info(f"✅ Tenant parser response: {response.response[:100]}...")
            
            return {
                "response": response.response,
                "intent": response.intent,
                "confidence": response.confidence,
                "entities": response.entities if hasattr(response, 'entities') else {}
            }
            
        except grpc.RpcError as e:
            logger.error(f"❌ gRPC error: {e.code()} - {e.details()}")
            # Return a default response instead of failing
            return {
                "response": f"Halo! Selamat datang di {tenant_id}. Ada yang bisa saya bantu?",
                "intent": "greeting",
                "confidence": 0.5
            }
        except Exception as e:
            logger.error(f"❌ Tenant parser error: {str(e)}")
            return {
                "response": f"Maaf, ada kendala teknis. Silakan coba lagi.",
                "intent": "error",
                "confidence": 0.0
            }
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.channel.close()
