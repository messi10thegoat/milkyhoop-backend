import redis
import structlog
from typing import List, Optional

logger = structlog.get_logger(__name__)

class SessionManager:
    def __init__(self):
        try:
            self.redis_client = redis.Redis(
                host='redis', port=6379, 
                password='MilkyRedis2025Secure',
                decode_responses=True
            )
        except Exception as e:
            logger.error(f"Redis connection failed: {e}")
            self.redis_client = None

    def health_check(self) -> bool:
        """Check if Redis is accessible"""
        try:
            if self.redis_client:
                self.redis_client.ping()
                return True
            return False
        except:
            return False

    def create_session(self, user_id: str, token: str, ttl: int = 3600) -> bool:
        """Create a new user session"""
        try:
            if not self.redis_client:
                return False
            
            session_key = f"session:{user_id}:{token}"
            self.redis_client.setex(session_key, ttl, "active")
            return True
        except Exception as e:
            logger.error(f"Failed to create session: {e}")
            return False

    def get_user_sessions(self, user_id: str) -> List[dict]:
        """Get all active sessions for a user - MISSING METHOD FIXED"""
        try:
            if not self.redis_client:
                return []
            
            # Search for all sessions for this user
            pattern = f"session:{user_id}:*"
            session_keys = self.redis_client.keys(pattern)
            
            sessions = []
            for key in session_keys:
                # Extract token from key: session:user_id:token
                parts = key.split(":")
                if len(parts) >= 3:
                    token = parts[2]
                    ttl = self.redis_client.ttl(key)
                    sessions.append({
                        "token_preview": token[:16] + "...",
                        "expires_in": ttl if ttl > 0 else 0,
                        "status": "active"
                    })
            
            return sessions
            
        except Exception as e:
            logger.error(f"Failed to get user sessions: {e}")
            return []

    def revoke_session(self, user_id: str, token: str) -> bool:
        """Revoke a specific session"""
        try:
            if not self.redis_client:
                return False
            
            session_key = f"session:{user_id}:{token}"
            result = self.redis_client.delete(session_key)
            return result > 0
        except Exception as e:
            logger.error(f"Failed to revoke session: {e}")
            return False

    def revoke_all_sessions(self, user_id: str) -> int:
        """Revoke all sessions for a user"""
        try:
            if not self.redis_client:
                return 0
            
            pattern = f"session:{user_id}:*"
            session_keys = self.redis_client.keys(pattern)
            
            if session_keys:
                return self.redis_client.delete(*session_keys)
            return 0
            
        except Exception as e:
            logger.error(f"Failed to revoke all sessions: {e}")
            return 0
