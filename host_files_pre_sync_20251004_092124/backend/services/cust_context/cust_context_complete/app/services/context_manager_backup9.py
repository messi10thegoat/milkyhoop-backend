import redis
import json
import logging
from typing import Optional, Dict, List, Any

# Absolute imports to avoid relative import issues
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models.conversation import ConversationContext, ConversationEntity

logger = logging.getLogger(__name__)

class CustomerContextManager:
    """Manages customer conversation context with Redis backend"""
    
    def __init__(self, redis_url: str = "redis://redis:6379"):
        self.redis_client = redis.from_url(redis_url, decode_responses=True)
        self.key_prefix = "cust_context"
    
    def _get_context_key(self, session_id: str, tenant_id: str) -> str:
        """Generate Redis key for conversation context"""
        return f"{self.key_prefix}:{tenant_id}:{session_id}"
    
    async def get_context(self, session_id: str, tenant_id: str) -> Optional[ConversationContext]:
        """Retrieve conversation context from Redis"""
        try:
            key = self._get_context_key(session_id, tenant_id)
            data = self.redis_client.get(key)
            
            if not data:
                return None
            
            context_dict = json.loads(data)
            context = ConversationContext.from_dict(context_dict)
            
            # Check if expired
            if context.is_expired():
                await self.delete_context(session_id, tenant_id)
                return None
            
            return context
            
        except Exception as e:
            logger.error(f"Error getting context for {session_id}: {e}")
            return None
    
    async def save_context(self, context: ConversationContext) -> bool:
        """Save conversation context to Redis"""
        try:
            key = self._get_context_key(context.session_id, context.tenant_id)
            data = json.dumps(context.to_dict())
            
            # Set with TTL
            self.redis_client.setex(key, context.ttl_seconds, data)
            
            logger.info(f"✅ Context saved for session {context.session_id}, turn {context.turn_count}")
            return True
            
        except Exception as e:
            logger.error(f"Error saving context for {context.session_id}: {e}")
            return False
    
    async def create_context(self, session_id: str, tenant_id: str, ttl_seconds: int = 3600) -> ConversationContext:
        """Create new conversation context"""
        context = ConversationContext(
            session_id=session_id,
            tenant_id=tenant_id,
            entities=[],
            ttl_seconds=ttl_seconds
        )
        
        await self.save_context(context)
        logger.info(f"🆕 New context created for session {session_id}")
        return context
    
    async def update_context(self, session_id: str, tenant_id: str, query: str, 
                           entities: Optional[List[Dict[str, Any]]] = None) -> ConversationContext:
        """Update conversation context with new turn"""
        context = await self.get_context(session_id, tenant_id)
        
        if not context:
            context = await self.create_context(session_id, tenant_id)
        
        # Increment turn
        context.increment_turn(query)
        
        # Add new entities if provided
        if entities:
            for entity_data in entities:
                context.add_entity(
                    entity_type=entity_data.get("type", "unknown"),
                    entity_name=entity_data.get("name", ""),
                    details=entity_data.get("details", {})
                )
        
        await self.save_context(context)
        return context
    
    async def delete_context(self, session_id: str, tenant_id: str) -> bool:
        """Delete conversation context"""
        try:
            key = self._get_context_key(session_id, tenant_id)
            self.redis_client.delete(key)
            logger.info(f"🗑️ Context deleted for session {session_id}")
            return True
        except Exception as e:
            logger.error(f"Error deleting context for {session_id}: {e}")
            return False
    
    async def get_session_stats(self, tenant_id: str) -> Dict[str, int]:
        """Get statistics for tenant sessions"""
        try:
            pattern = f"{self.key_prefix}:{tenant_id}:*"
            keys = self.redis_client.keys(pattern)
            
            active_sessions = len(keys)
            total_turns = 0
            
            for key in keys:
                try:
                    data = self.redis_client.get(key)
                    if data:
                        context_dict = json.loads(data)
                        total_turns += context_dict.get("turn_count", 0)
                except:
                    continue
            
            return {
                "active_sessions": active_sessions,
                "total_turns": total_turns,
                "avg_turns_per_session": total_turns / max(1, active_sessions)
            }
        except Exception as e:
            logger.error(f"Error getting session stats: {e}")
            return {"active_sessions": 0, "total_turns": 0, "avg_turns_per_session": 0}
