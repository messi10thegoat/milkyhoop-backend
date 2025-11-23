import jwt
import os
from datetime import datetime, timedelta
from typing import Dict, Optional, Any
import logging

logger = logging.getLogger(__name__)

# JWT Configuration
JWT_SECRET = os.getenv("JWT_SECRET", "bb599073be39674d540ba07d77967282d4fa26247f6d17d8a60b093002d70d40")
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 10080  # 7 days (7 * 24 * 60)
REFRESH_TOKEN_EXPIRE_DAYS = 30    # 30 days

class JWTHandler:
    """Enterprise JWT Token Handler"""
    
    @staticmethod
    def create_access_token(
        user_id: str,
        tenant_id: str,
        role: str,
        email: str,
        username: str
    ) -> str:
        """
        Create JWT access token with user claims
        
        Args:
            user_id: Unique user identifier
            tenant_id: Tenant isolation identifier
            role: User role (ADMIN, USER, etc.)
            email: User email
            username: Username
            
        Returns:
            Encoded JWT token string
        """
        try:
            now = datetime.utcnow()
            expires_at = now + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
            
            payload = {
                "user_id": user_id,
                "tenant_id": tenant_id,
                "role": role,
                "email": email,
                "username": username,
                "token_type": "access",
                "iat": now,
                "exp": expires_at,
                "nbf": now  # Not valid before
            }
            
            token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
            logger.info(f"Access token created for user: {user_id}, tenant: {tenant_id}")
            return token
            
        except Exception as e:
            logger.error(f"Error creating access token: {e}")
            raise
    
    @staticmethod
    def create_refresh_token(
        user_id: str,
        session_id: str,
        tenant_id: str
    ) -> str:
        """
        Create JWT refresh token for token rotation
        
        Args:
            user_id: Unique user identifier
            session_id: Session tracking identifier
            tenant_id: Tenant identifier
            
        Returns:
            Encoded refresh token string
        """
        try:
            now = datetime.utcnow()
            expires_at = now + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
            
            payload = {
                "user_id": user_id,
                "session_id": session_id,
                "tenant_id": tenant_id,
                "token_type": "refresh",
                "iat": now,
                "exp": expires_at,
                "nbf": now
            }
            
            token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
            logger.info(f"Refresh token created for session: {session_id}")
            return token
            
        except Exception as e:
            logger.error(f"Error creating refresh token: {e}")
            raise
    
    @staticmethod
    def verify_token(token: str, token_type: str = "access") -> Optional[Dict[str, Any]]:
        """
        Verify and decode JWT token with validation
        
        Args:
            token: JWT token string
            token_type: Expected token type (access/refresh)
            
        Returns:
            Decoded payload if valid, None if invalid
        """
        try:
            # Decode and verify token
            payload = jwt.decode(
                token,
                JWT_SECRET,
                algorithms=[JWT_ALGORITHM],
                options={
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_nbf": True,
                    "require": ["exp", "iat", "user_id", "token_type"]
                }
            )
            
            # Validate token type
            if payload.get("token_type") != token_type:
                logger.warning(f"Token type mismatch. Expected: {token_type}, Got: {payload.get('token_type')}")
                return None
            
            logger.info(f"Token verified successfully for user: {payload.get('user_id')}")
            return payload
            
        except jwt.ExpiredSignatureError:
            logger.warning("Token has expired")
            return None
        except jwt.InvalidTokenError as e:
            logger.warning(f"Invalid token: {e}")
            return None
        except Exception as e:
            logger.error(f"Token verification error: {e}")
            return None
    
    @staticmethod
    def decode_token_unsafe(token: str) -> Optional[Dict[str, Any]]:
        """
        Decode token WITHOUT verification (use only for debugging)
        
        Args:
            token: JWT token string
            
        Returns:
            Decoded payload (unverified)
        """
        try:
            payload = jwt.decode(
                token,
                options={"verify_signature": False}
            )
            return payload
        except Exception as e:
            logger.error(f"Token decode error: {e}")
            return None
    
    @staticmethod
    def get_token_expiration(token: str) -> Optional[datetime]:
        """
        Extract expiration time from token
        
        Args:
            token: JWT token string
            
        Returns:
            Expiration datetime if valid
        """
        try:
            payload = jwt.decode(token, options={"verify_signature": False})
            exp_timestamp = payload.get("exp")
            if exp_timestamp:
                return datetime.fromtimestamp(exp_timestamp)
            return None
        except Exception as e:
            logger.error(f"Error extracting expiration: {e}")
            return None
    
    @staticmethod
    def is_token_expired(token: str) -> bool:
        """
        Check if token is expired
        
        Args:
            token: JWT token string
            
        Returns:
            True if expired, False otherwise
        """
        try:
            jwt.decode(
                token,
                JWT_SECRET,
                algorithms=[JWT_ALGORITHM],
                options={"verify_exp": True}
            )
            return False
        except jwt.ExpiredSignatureError:
            return True
        except Exception:
            return True

# Convenience functions for backward compatibility
def create_access_token(user_id: str, tenant_id: str, role: str, email: str, username: str) -> str:
    return JWTHandler.create_access_token(user_id, tenant_id, role, email, username)

def create_refresh_token(user_id: str, session_id: str, tenant_id: str) -> str:
    return JWTHandler.create_refresh_token(user_id, session_id, tenant_id)

def verify_token(token: str, token_type: str = "access") -> Optional[Dict[str, Any]]:
    return JWTHandler.verify_token(token, token_type)
