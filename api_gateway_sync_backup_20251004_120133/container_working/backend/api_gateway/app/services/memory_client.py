import grpc
import asyncio
import json
import logging
from typing import Dict, Optional, List
from milkyhoop_protos import memory_service_pb2_grpc, memory_service_pb2

logger = logging.getLogger(__name__)

class MemoryClient:
    """gRPC client for Memory Service"""
    
    def __init__(self, grpc_host: str = "memory_service:5000"):
        self.grpc_host = grpc_host
        
    async def store_memory(self, user_id: str, tenant_id: str, key: str, value: dict, ttl: int = 3600) -> bool:
        """Store memory with TTL"""
        try:
            async with grpc.aio.insecure_channel(self.grpc_host) as channel:
                stub = memory_service_pb2_grpc.MemoryServiceStub(channel)
                
                request = memory_service_pb2.StoreMemoryRequest(
                    user_id=user_id,
                    tenant_id=tenant_id,
                    key=key,
                    value=json.dumps(value),
                    ttl=ttl
                )
                
                response = await stub.StoreMemory(request)
                logger.info(f"Memory stored: {response.message}")
                return response.success
                
        except Exception as e:
            logger.error(f"Error storing memory: {e}")
            return False
    
    async def get_memory(self, user_id: str, tenant_id: str, key: str) -> Optional[Dict]:
        """Get memory by key"""
        try:
            async with grpc.aio.insecure_channel(self.grpc_host) as channel:
                stub = memory_service_pb2_grpc.MemoryServiceStub(channel)
                
                request = memory_service_pb2.GetMemoryRequest(
                    user_id=user_id,
                    tenant_id=tenant_id,
                    key=key
                )
                
                response = await stub.GetMemory(request)
                
                if response.found:
                    return json.loads(response.value)
                return None
                
        except Exception as e:
            logger.error(f"Error getting memory: {e}")
            return None
    
    async def update_memory(self, user_id: str, tenant_id: str, key: str, value: dict) -> bool:
        """Update existing memory"""
        try:
            async with grpc.aio.insecure_channel(self.grpc_host) as channel:
                stub = memory_service_pb2_grpc.MemoryServiceStub(channel)
                
                request = memory_service_pb2.UpdateMemoryRequest(
                    user_id=user_id,
                    tenant_id=tenant_id,
                    key=key,
                    value=json.dumps(value)
                )
                
                response = await stub.UpdateMemory(request)
                logger.info(f"Memory updated: {response.message}")
                return response.success
                
        except Exception as e:
            logger.error(f"Error updating memory: {e}")
            return False
    
    async def clear_memory(self, user_id: str, tenant_id: str, key: Optional[str] = None) -> bool:
        """Clear memory (specific or all)"""
        try:
            async with grpc.aio.insecure_channel(self.grpc_host) as channel:
                stub = memory_service_pb2_grpc.MemoryServiceStub(channel)
                
                request = memory_service_pb2.ClearMemoryRequest(
                    user_id=user_id,
                    tenant_id=tenant_id,
                    key=key or ""
                )
                
                response = await stub.ClearMemory(request)
                logger.info(f"Memory cleared: {response.message}")
                return response.success
                
        except Exception as e:
            logger.error(f"Error clearing memory: {e}")
            return False
