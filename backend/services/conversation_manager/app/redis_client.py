"""
Redis Client for conversation_manager
Purpose: Manage session state with TTL
"""
import json
import logging
from typing import Optional, Dict, Any
from datetime import datetime
import redis.asyncio as redis

logger = logging.getLogger(__name__)


class ConversationRedisClient:
    """
    Redis client for conversation state management
    """
    
    def __init__(self, redis_url: str = "redis://redis:6379", password: str = ""):
        """
        Initialize Redis client
        
        Args:
            redis_url: Redis connection URL
            password: Redis password (if required)
        """
        self.redis_url = redis_url
        self.password = password
        self.client: Optional[redis.Redis] = None
        self.session_ttl = 3600  # 1 hour in seconds
        
        logger.info(f"ConversationRedisClient initialized with URL: {redis_url}")
    
    async def connect(self):
        """Connect to Redis"""
        try:
            self.client = await redis.from_url(
                self.redis_url,
                password=self.password,
                encoding="utf-8",
                decode_responses=True
            )
            
            # Test connection
            await self.client.ping()
            logger.info("✅ Redis connected successfully")
            
        except Exception as e:
            logger.error(f"❌ Failed to connect to Redis: {e}")
            raise
    
    async def disconnect(self):
        """Disconnect from Redis"""
        if self.client:
            await self.client.close()
            logger.info("Redis connection closed")
    
    def _session_key(self, session_id: str) -> str:
        """Generate Redis key for session"""
        return f"session:{session_id}"
    
    async def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """
        Get session data from Redis
        
        Args:
            session_id: Session identifier
            
        Returns:
            Session data dict or None if not found
        """
        try:
            key = self._session_key(session_id)
            data = await self.client.get(key)
            
            if data:
                session = json.loads(data)
                logger.info(f"Retrieved session {session_id}, state: {session.get('state')}")
                return session
            else:
                logger.info(f"Session {session_id} not found")
                return None
                
        except Exception as e:
            logger.error(f"Error getting session {session_id}: {e}")
            return None
    
    async def set_session(
        self, 
        session_id: str, 
        data: Dict[str, Any],
        ttl: Optional[int] = None
    ) -> bool:
        """
        Store session data in Redis with TTL
        
        Args:
            session_id: Session identifier
            data: Session data to store
            ttl: Time to live in seconds (default: 1 hour)
            
        Returns:
            True if successful
        """
        try:
            key = self._session_key(session_id)
            ttl = ttl or self.session_ttl
            
            # Add metadata
            data['updated_at'] = datetime.utcnow().isoformat()
            if 'created_at' not in data:
                data['created_at'] = data['updated_at']
            
            # Store with TTL
            await self.client.setex(
                key,
                ttl,
                json.dumps(data)
            )
            
            logger.info(f"Stored session {session_id}, TTL: {ttl}s")
            return True
            
        except Exception as e:
            logger.error(f"Error storing session {session_id}: {e}")
            return False
    
    async def update_session(
        self,
        session_id: str,
        updates: Dict[str, Any]
    ) -> bool:
        """
        Update existing session (merge with existing data)
        
        Args:
            session_id: Session identifier
            updates: Data to merge/update
            
        Returns:
            True if successful
        """
        try:
            # Get existing session
            session = await self.get_session(session_id)
            
            if not session:
                # Create new session if doesn't exist
                session = updates
            else:
                # Merge updates
                session.update(updates)
            
            # Store updated session
            return await self.set_session(session_id, session)
            
        except Exception as e:
            logger.error(f"Error updating session {session_id}: {e}")
            return False
    
    async def delete_session(self, session_id: str) -> bool:
        """
        Delete session from Redis
        
        Args:
            session_id: Session identifier
            
        Returns:
            True if successful
        """
        try:
            key = self._session_key(session_id)
            await self.client.delete(key)
            logger.info(f"Deleted session {session_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error deleting session {session_id}: {e}")
            return False
    
    async def get_session_ttl(self, session_id: str) -> int:
        """
        Get remaining TTL for session

        Args:
            session_id: Session identifier

        Returns:
            Remaining seconds or -1 if not found
        """
        try:
            key = self._session_key(session_id)
            ttl = await self.client.ttl(key)
            return ttl

        except Exception as e:
            logger.error(f"Error getting TTL for {session_id}: {e}")
            return -1

    # ============================================
    # DRAFT TRANSACTION METHODS (Phase 1.5)
    # ============================================

    def _draft_key(self, tenant_id: str, session_id: str) -> str:
        """Generate Redis key for draft transaction"""
        return f"draft:{tenant_id}:{session_id}"

    async def save_draft(
        self,
        tenant_id: str,
        session_id: str,
        draft_data: Dict[str, Any],
        ttl: int = 1800  # 30 minutes
    ) -> bool:
        """
        Save draft transaction state to Redis

        Args:
            tenant_id: Tenant identifier
            session_id: Session/conversation identifier
            draft_data: Draft transaction data
            ttl: Time to live in seconds (default: 30 min)

        Returns:
            True if successful
        """
        try:
            key = self._draft_key(tenant_id, session_id)

            # Add timestamp metadata
            draft_data['updated_at'] = datetime.utcnow().isoformat()
            if 'created_at' not in draft_data:
                draft_data['created_at'] = draft_data['updated_at']

            # Store with TTL
            await self.client.setex(
                key,
                ttl,
                json.dumps(draft_data)
            )

            logger.info(f"✅ Saved draft for tenant={tenant_id}, session={session_id}, TTL={ttl}s")
            return True

        except Exception as e:
            logger.error(f"❌ Error saving draft for {tenant_id}/{session_id}: {e}")
            return False

    async def get_draft(self, tenant_id: str, session_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve draft transaction from Redis

        Args:
            tenant_id: Tenant identifier
            session_id: Session/conversation identifier

        Returns:
            Draft data dict or None if not found
        """
        try:
            key = self._draft_key(tenant_id, session_id)
            data = await self.client.get(key)

            if data:
                draft = json.loads(data)
                logger.info(f"📥 Retrieved draft for tenant={tenant_id}, session={session_id}")
                return draft
            else:
                logger.debug(f"No draft found for {tenant_id}/{session_id}")
                return None

        except Exception as e:
            logger.error(f"❌ Error getting draft for {tenant_id}/{session_id}: {e}")
            return None

    async def delete_draft(self, tenant_id: str, session_id: str) -> bool:
        """
        Delete draft transaction from Redis

        Args:
            tenant_id: Tenant identifier
            session_id: Session/conversation identifier

        Returns:
            True if successful
        """
        try:
            key = self._draft_key(tenant_id, session_id)
            await self.client.delete(key)
            logger.info(f"🗑️ Deleted draft for tenant={tenant_id}, session={session_id}")
            return True

        except Exception as e:
            logger.error(f"❌ Error deleting draft for {tenant_id}/{session_id}: {e}")
            return False
