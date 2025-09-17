import grpc
from typing import Optional, Dict, Any
import logging

# Import Level 13 proto stubs
import sys
sys.path.append('backend/api_gateway/libs/milkyhoop_protos')
from cust_context import cust_context_pb2 as pb
from cust_context import cust_context_pb2_grpc as pb_grpc

logger = logging.getLogger(__name__)

class CustContextClient:
    """Level 13 Customer Context Service Client"""
    
    def __init__(self, server_address: str = "cust_context:5008"):
        self.server_address = server_address
        self.channel = None
        self.stub = None
    
    async def initialize(self):
        """Initialize gRPC connection to Level 13 service"""
        try:
            self.channel = grpc.aio.insecure_channel(self.server_address)
            self.stub = pb_grpc.CustContextServiceStub(self.channel)
            logger.info(f"✅ Level 13 Context client initialized: {self.server_address}")
        except Exception as e:
            logger.error(f"❌ Failed to initialize Level 13 client: {e}")
            raise
    
    async def create_context(self, session_id: str, tenant_id: str, ttl_seconds: int = 3600) -> bool:
        """Create new conversation context"""
        try:
            request = pb.CreateContextRequest(
                session_id=session_id,
                tenant_id=tenant_id,
                ttl_seconds=ttl_seconds
            )
            response = await self.stub.CreateContext(request)
            logger.info(f"📥 CreateContext: {session_id} → {response.success}")
            return response.success
        except Exception as e:
            logger.error(f"❌ CreateContext failed: {e}")
            return False
    
    async def update_context(self, session_id: str, tenant_id: str, user_query: str, entities: str = "") -> bool:
        """Update conversation context with new user message"""
        try:
            request = pb.UpdateContextRequest(
                session_id=session_id,
                tenant_id=tenant_id,
                user_query=user_query,
                entities=entities
            )
            response = await self.stub.UpdateContext(request)
            logger.info(f"🔄 UpdateContext: {session_id} → {response.success}")
            return response.success
        except Exception as e:
            logger.error(f"❌ UpdateContext failed: {e}")
            return False
    
    async def get_context(self, session_id: str, tenant_id: str) -> Optional[str]:
        """Get conversation context"""
        try:
            request = pb.GetContextRequest(
                session_id=session_id,
                tenant_id=tenant_id
            )
            response = await self.stub.GetContext(request)
            if response.success:
                logger.info(f"📖 GetContext: {session_id} → context retrieved")
                return response.context_data
            return None
        except Exception as e:
            logger.error(f"❌ GetContext failed: {e}")
            return None
    
    async def close(self):
        """Close gRPC connection"""
        if self.channel:
            await self.channel.close()
            logger.info("🔒 Level 13 Context client closed")
