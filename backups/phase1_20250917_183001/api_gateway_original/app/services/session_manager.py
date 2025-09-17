"""
Session Manager - Phase 2 Implementation
Redis-based session storage with TTL management
Based on Phase 2 Authentication System Documentation
"""

import redis
import json
import structlog
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
import os

logger = structlog.get_logger(__name__)

class SessionManager:
    """
    Redis session storage with automatic TTL management
    Implements session CRUD operations per Phase 2 documentation
    """
    
    def __init__(self):
        self.redis_host = os.getenv('REDIS_HOST', 'redis')
        self.redis_port = int(os.getenv('REDIS_PORT', '6379'))
        self.redis_password = os.getenv('REDIS_PASSWORD', 'MilkyRedis2025Secure')
        self.default_ttl = 3600  # 1 hour
        
        try:
            self.redis_client = redis.Redis(
                host=self.redis_host,
                port=self.redis_port,
                password=self.redis_password,
                decode_responses=True
            )
            # Test connection
            self.redis_client.ping()
            logger.info("✅ SessionManager connected to Redis",
                       host=self.redis_host, port=self.redis_port)
        except Exception as e:
            logger.error("❌ Redis connection failed", error=str(e))
            self.redis_client = None
    
    def store_session(self, token: str, user_context: Dict[str, Any], expires_in: int = None) -> bool:
        """
        Store user session in Redis with TTL
        Per Phase 2 documentation: session caching for performance
        """
        if not self.redis_client:
            logger.warning("⚠️ Redis not available - session not stored")
            return False
        
        try:
            # Prepare session data
            session_data = {
                "user_context": user_context,
                "created_at": datetime.utcnow().isoformat(),
                "last_accessed": datetime.utcnow().isoformat()
            }
            
            # Store with TTL
            ttl = expires_in or self.default_ttl
            session_key = f"session:{token[:16]}"  # Use token prefix as key
            
            self.redis_client.setex(
                session_key,
                ttl,
                json.dumps(session_data)
            )
            
            logger.info("✅ Session stored", 
                       session_key=session_key,
                       user_id=user_context.get('user_id'),
                       ttl=ttl)
            return True
            
        except Exception as e:
            logger.error("❌ Session storage failed", error=str(e))
            return False
    
    def get_session(self, token: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve session from Redis
        Updates last_accessed timestamp
        """
        if not self.redis_client:
            return None
        
        try:
            session_key = f"session:{token[:16]}"
            session_data = self.redis_client.get(session_key)
            
            if not session_data:
                logger.debug("📭 Session not found", session_key=session_key)
                return None
            
            # Parse session data
            session = json.loads(session_data)
            
            # Update last accessed
            session['last_accessed'] = datetime.utcnow().isoformat()
            
            # Update in Redis
            ttl = self.redis_client.ttl(session_key)
            if ttl > 0:
                self.redis_client.setex(session_key, ttl, json.dumps(session))
            
            logger.debug("✅ Session retrieved", session_key=session_key)
            return session['user_context']
            
        except Exception as e:
            logger.error("❌ Session retrieval failed", error=str(e))
            return None
    
    def revoke_session(self, token: str) -> bool:
        """Remove session from Redis"""
        if not self.redis_client:
            return False
        
        try:
            session_key = f"session:{token[:16]}"
            result = self.redis_client.delete(session_key)
            logger.info("✅ Session revoked", session_key=session_key)
            return bool(result)
        except Exception as e:
            logger.error("❌ Session revocation failed", error=str(e))
            return False
    
    def list_user_sessions(self, user_id: str) -> List[Dict[str, Any]]:
        """
        List all active sessions for a user
        Per Phase 2 documentation: cross-device session tracking
        """
        if not self.redis_client:
            return []
        
        try:
            sessions = []
            for key in self.redis_client.scan_iter(match="session:*"):
                session_data = self.redis_client.get(key)
                if session_data:
                    session = json.loads(session_data)
                    if session['user_context'].get('user_id') == user_id:
                        sessions.append({
                            "session_key": key,
                            "created_at": session['created_at'],
                            "last_accessed": session['last_accessed'],
                            "ttl": self.redis_client.ttl(key)
                        })
            return sessions
        except Exception as e:
            logger.error("❌ Session listing failed", error=str(e))
            return []
    
    def health_check(self) -> bool:
        """Check Redis connectivity"""
        try:
            if self.redis_client:
                self.redis_client.ping()
                return True
        except:
            pass
        return False
