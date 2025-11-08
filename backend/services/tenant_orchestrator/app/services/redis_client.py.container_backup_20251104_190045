"""
Redis Session Manager with Authentication
Handles conversation session persistence and turn tracking
"""

import json
import logging
from datetime import datetime
from typing import Dict, Any, Optional

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)


class SessionManager:
    """
    Manages conversation sessions in Redis for continuous extraction
    
    Features:
    - Session context persistence with TTL
    - Conversation turn history tracking
    - Business data incremental storage
    - Progress tracking per session
    """
    
    def __init__(self, redis_url: str = "redis://:MilkyRedis2025Secure@redis:6379"):
        """
        Initialize SessionManager with authenticated Redis connection
        
        Args:
            redis_url: Redis connection URL with password (default: MilkyRedis2025Secure)
        """
        self.redis_url = redis_url
        self.redis = None
        logger.info(f"SessionManager initialized with Redis URL: {redis_url.replace('MilkyRedis2025Secure', '***')}")
    
    async def initialize(self):
        """Initialize Redis connection with authentication"""
        try:
            self.redis = await aioredis.from_url(
                self.redis_url,
                decode_responses=True
            )
            await self.redis.ping()
            logger.info("✅ SessionManager connected to Redis successfully")
        except Exception as e:
            logger.error(f"❌ Failed to initialize SessionManager: {e}")
            raise
    
    async def get_context(self, session_id: str) -> Dict[str, Any]:
        """
        Get session context from Redis
        
        Args:
            session_id: Unique session identifier
            
        Returns:
            Session context dictionary or empty dict if not found
        """
        try:
            context_data = await self.redis.get(f"session:{session_id}")
            if context_data:
                context = json.loads(context_data)
                logger.info(f"📦 Retrieved context for session {session_id}")
                return context
            else:
                logger.info(f"🆕 No existing context for session {session_id}")
                return {}
        except Exception as e:
            logger.error(f"❌ Error getting context for {session_id}: {e}")
            return {}
    
    async def save_context(
        self, 
        session_id: str, 
        context: Dict[str, Any],
        ttl: int = 3600  # 1 hour default
    ):
        """
        Save session context to Redis with TTL
        
        Args:
            session_id: Unique session identifier
            context: Context data to save
            ttl: Time to live in seconds (default 1 hour)
        """
        try:
            context_json = json.dumps(context)
            await self.redis.setex(
                f"session:{session_id}",
                ttl,
                context_json
            )
            logger.info(f"💾 Saved context for session {session_id} (TTL: {ttl}s)")
        except Exception as e:
            logger.error(f"❌ Error saving context for {session_id}: {e}")
            raise
    
    async def update_context(
        self,
        session_id: str,
        updates: Dict[str, Any]
    ):
        """
        Update existing session context
        
        Args:
            session_id: Unique session identifier
            updates: Dictionary of updates to merge
        """
        try:
            existing = await self.get_context(session_id)
            existing.update(updates)
            existing["updated_at"] = datetime.now().isoformat()
            await self.save_context(session_id, existing)
            logger.info(f"🔄 Updated context for session {session_id}")
        except Exception as e:
            logger.error(f"❌ Error updating context for {session_id}: {e}")
            raise
    
    async def add_turn(
        self,
        session_id: str,
        user_message: str,
        assistant_response: str
    ):
        """
        Add conversation turn to session history
        
        Args:
            session_id: Unique session identifier
            user_message: User's message
            assistant_response: Assistant's response
        """
        try:
            context = await self.get_context(session_id)
            
            if "turns" not in context:
                context["turns"] = []
            
            turn = {
                "user": user_message,
                "assistant": assistant_response,
                "timestamp": datetime.now().isoformat()
            }
            
            context["turns"].append(turn)
            context["turn_count"] = len(context["turns"])
            
            await self.save_context(session_id, context)
            logger.info(f"📝 Added turn #{context['turn_count']} to session {session_id}")
            
        except Exception as e:
            logger.error(f"❌ Error adding turn for {session_id}: {e}")
            raise
    
    async def get_business_data(self, session_id: str) -> Dict[str, Any]:
        """
        Get accumulated business data from session
        
        Args:
            session_id: Unique session identifier
            
        Returns:
            Business data dictionary
        """
        try:
            context = await self.get_context(session_id)
            return context.get("business_data", {})
        except Exception as e:
            logger.error(f"❌ Error getting business data for {session_id}: {e}")
            return {}
    
    async def update_business_data(
        self,
        session_id: str,
        business_updates: Dict[str, Any]
    ):
        """
        Update business data in session
        
        Args:
            session_id: Unique session identifier
            business_updates: New business information to merge
        """
        try:
            context = await self.get_context(session_id)
            
            if "business_data" not in context:
                context["business_data"] = {}
            
            context["business_data"].update(business_updates)
            await self.save_context(session_id, context)
            
            logger.info(f"📊 Updated business data for session {session_id}")
            
        except Exception as e:
            logger.error(f"❌ Error updating business data for {session_id}: {e}")
            raise
    
    async def clear_session(self, session_id: str):
        """
        Clear session data from Redis
        
        Args:
            session_id: Unique session identifier
        """
        try:
            await self.redis.delete(f"session:{session_id}")
            logger.info(f"🗑️ Cleared session {session_id}")
        except Exception as e:
            logger.error(f"❌ Error clearing session {session_id}: {e}")
            raise
    
    async def acquire_lock(
        self, 
        key: str, 
        ttl: int = 60,
        lock_value: str = "locked"
    ) -> bool:
        """
        Acquire distributed lock using Redis SET NX EX (atomic operation)
        
        Args:
            key: Lock key (e.g., 'request_lock:session_id:message_hash')
            ttl: Lock TTL in seconds (default 60s)
            lock_value: Value to set (default 'locked')
            
        Returns:
            True if lock acquired, False if already locked
        """
        try:
            # SET key value NX EX ttl - atomic operation
            result = await self.redis.set(
                key, 
                lock_value, 
                nx=True,  # Only set if not exists
                ex=ttl    # Expire after ttl seconds
            )
            
            if result:
                logger.info(f"🔒 Lock acquired: {key} (TTL: {ttl}s)")
                return True
            else:
                logger.warning(f"⚠️ Lock already exists: {key}")
                return False
                
        except Exception as e:
            logger.error(f"❌ Error acquiring lock {key}: {e}")
            return False
    
    async def release_lock(self, key: str):
        """
        Release distributed lock
        
        Args:
            key: Lock key to release
        """
        try:
            deleted = await self.redis.delete(key)
            if deleted:
                logger.info(f"🔓 Lock released: {key}")
            else:
                logger.warning(f"⚠️ Lock not found (already expired?): {key}")
        except Exception as e:
            logger.error(f"❌ Error releasing lock {key}: {e}")
