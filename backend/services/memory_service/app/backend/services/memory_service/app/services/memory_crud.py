import redis
import json
import asyncio
from typing import Dict, Optional, List
from datetime import datetime
import logging
from ..models.memory import MemoryModel

logger = logging.getLogger(__name__)

class MemoryCrudService:
    """Redis-based memory CRUD operations"""
    
    def __init__(self, redis_url: str = "redis://redis:6379"):
        self.redis_client = redis.from_url(redis_url, decode_responses=True)
        
    def _get_redis_key(self, user_id: str, tenant_id: str, key: str) -> str:
        """Generate Redis key with tenant isolation"""
        return f"memory:{tenant_id}:{user_id}:{key}"
    
    def _get_user_pattern(self, user_id: str, tenant_id: str) -> str:
        """Get pattern for user's all memories"""
        return f"memory:{tenant_id}:{user_id}:*"
    
    async def store_memory(self, user_id: str, tenant_id: str, key: str, value: dict, ttl: int = 3600) -> bool:
        """Store memory with TTL"""
        try:
            memory = MemoryModel(user_id, tenant_id, key, value, ttl)
            redis_key = self._get_redis_key(user_id, tenant_id, key)
            
            # Store in Redis with TTL
            self.redis_client.setex(
                redis_key, 
                ttl, 
                json.dumps(memory.to_dict())
            )
            
            logger.info(f"Memory stored: {redis_key}")
            return True
            
        except Exception as e:
            logger.error(f"Error storing memory: {e}")
            return False
    
    async def get_memory(self, user_id: str, tenant_id: str, key: str) -> Optional[Dict]:
        """Get memory by key"""
        try:
            redis_key = self._get_redis_key(user_id, tenant_id, key)
            data = self.redis_client.get(redis_key)
            
            if not data:
                return None
                
            memory_dict = json.loads(data)
            memory = MemoryModel.from_dict(memory_dict)
            
            # Check if expired
            if memory.is_expired():
                self.redis_client.delete(redis_key)
                return None
                
            return memory.value
            
        except Exception as e:
            logger.error(f"Error getting memory: {e}")
            return None
    
    async def update_memory(self, user_id: str, tenant_id: str, key: str, value: dict) -> bool:
        """Update existing memory"""
        try:
            redis_key = self._get_redis_key(user_id, tenant_id, key)
            existing_data = self.redis_client.get(redis_key)
            
            if not existing_data:
                return False
                
            existing_memory = MemoryModel.from_dict(json.loads(existing_data))
            existing_memory.value = value
            
            # Get remaining TTL
            ttl = self.redis_client.ttl(redis_key)
            if ttl <= 0:
                ttl = 3600  # Default if no TTL
                
            self.redis_client.setex(
                redis_key,
                ttl,
                json.dumps(existing_memory.to_dict())
            )
            
            logger.info(f"Memory updated: {redis_key}")
            return True
            
        except Exception as e:
            logger.error(f"Error updating memory: {e}")
            return False
    
    async def clear_memory(self, user_id: str, tenant_id: str, key: Optional[str] = None) -> bool:
        """Clear memory (specific key or all user memories)"""
        try:
            if key:
                # Clear specific memory
                redis_key = self._get_redis_key(user_id, tenant_id, key)
                result = self.redis_client.delete(redis_key)
                logger.info(f"Memory cleared: {redis_key}")
                return result > 0
            else:
                # Clear all user memories
                pattern = self._get_user_pattern(user_id, tenant_id)
                keys = self.redis_client.keys(pattern)
                if keys:
                    result = self.redis_client.delete(*keys)
                    logger.info(f"All user memories cleared: {len(keys)} keys")
                    return result > 0
                return True
                
        except Exception as e:
            logger.error(f"Error clearing memory: {e}")
            return False
    
    async def list_memories(self, user_id: str, tenant_id: str) -> List[Dict]:
        """List all user memories"""
        try:
            pattern = self._get_user_pattern(user_id, tenant_id)
            keys = self.redis_client.keys(pattern)
            
            memories = []
            for redis_key in keys:
                data = self.redis_client.get(redis_key)
                if data:
                    memory_dict = json.loads(data)
                    memory = MemoryModel.from_dict(memory_dict)
                    
                    # Skip expired memories
                    if not memory.is_expired():
                        memories.append({
                            "key": memory.key,
                            "value": memory.value,
                            "created_at": memory.created_at.isoformat(),
                            "expires_at": memory.expires_at.isoformat()
                        })
                    else:
                        # Clean up expired memory
                        self.redis_client.delete(redis_key)
            
            return memories
            
        except Exception as e:
            logger.error(f"Error listing memories: {e}")
            return []