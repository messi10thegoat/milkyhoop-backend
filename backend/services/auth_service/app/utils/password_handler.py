import bcrypt
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# Password policy configuration
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128
BCRYPT_ROUNDS = 12  # Industry standard for security vs performance

class PasswordHandler:
    """Enterprise Password Security Handler"""
    
    @staticmethod
    def hash_password(plain_password: str) -> str:
        """
        Hash password using bcrypt with secure work factor
        
        Args:
            plain_password: Plain text password
            
        Returns:
            Bcrypt hashed password string
            
        Raises:
            ValueError: If password validation fails
        """
        try:
            # Validate password
            PasswordHandler.validate_password(plain_password)
            
            # Generate salt and hash
            salt = bcrypt.gensalt(rounds=BCRYPT_ROUNDS)
            hashed = bcrypt.hashpw(plain_password.encode('utf-8'), salt)
            
            logger.info("Password hashed successfully")
            return hashed.decode('utf-8')
            
        except ValueError as e:
            logger.error(f"Password validation failed: {e}")
            raise
        except Exception as e:
            logger.error(f"Password hashing error: {e}")
            raise
    
    @staticmethod
    def verify_password(plain_password: str, hashed_password: str) -> bool:
        """
        Verify password against bcrypt hash
        
        Args:
            plain_password: Plain text password to verify
            hashed_password: Bcrypt hashed password from database
            
        Returns:
            True if password matches, False otherwise
        """
        try:
            # Convert strings to bytes
            plain_bytes = plain_password.encode('utf-8')
            hashed_bytes = hashed_password.encode('utf-8')
            
            # Verify using bcrypt
            result = bcrypt.checkpw(plain_bytes, hashed_bytes)
            
            if result:
                logger.info("Password verification successful")
            else:
                logger.warning("Password verification failed")
                
            return result
            
        except Exception as e:
            logger.error(f"Password verification error: {e}")
            return False
    
    @staticmethod
    def validate_password(password: str) -> bool:
        """
        Validate password against security policy
        
        Password Requirements:
        - Minimum 8 characters
        - Maximum 128 characters
        - At least one uppercase letter
        - At least one lowercase letter
        - At least one digit
        - At least one special character
        
        Args:
            password: Password to validate
            
        Returns:
            True if valid
            
        Raises:
            ValueError: If validation fails with specific reason
        """
        if not password:
            raise ValueError("Password cannot be empty")
        
        if len(password) < MIN_PASSWORD_LENGTH:
            raise ValueError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters")
        
        if len(password) > MAX_PASSWORD_LENGTH:
            raise ValueError(f"Password must not exceed {MAX_PASSWORD_LENGTH} characters")
        
        # Check for uppercase letter
        if not re.search(r'[A-Z]', password):
            raise ValueError("Password must contain at least one uppercase letter")
        
        # Check for lowercase letter
        if not re.search(r'[a-z]', password):
            raise ValueError("Password must contain at least one lowercase letter")
        
        # Check for digit
        if not re.search(r'\d', password):
            raise ValueError("Password must contain at least one digit")
        
        # Check for special character
        if not re.search(r'[!@#$%^&*(),.?":{}|<>]', password):
            raise ValueError("Password must contain at least one special character")
        
        logger.info("Password validation passed")
        return True
    
    @staticmethod
    def validate_password_simple(password: str) -> bool:
        """
        Simple password validation (minimum length only)
        Use for less strict environments
        
        Args:
            password: Password to validate
            
        Returns:
            True if valid
            
        Raises:
            ValueError: If validation fails
        """
        if not password:
            raise ValueError("Password cannot be empty")
        
        if len(password) < MIN_PASSWORD_LENGTH:
            raise ValueError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters")
        
        if len(password) > MAX_PASSWORD_LENGTH:
            raise ValueError(f"Password must not exceed {MAX_PASSWORD_LENGTH} characters")
        
        return True
    
    @staticmethod
    def check_password_strength(password: str) -> dict:
        """
        Check password strength and return detailed assessment
        
        Args:
            password: Password to assess
            
        Returns:
            Dict with strength score and feedback
        """
        strength = {
            "score": 0,
            "feedback": [],
            "level": "weak"
        }
        
        # Length check
        if len(password) >= 8:
            strength["score"] += 1
        if len(password) >= 12:
            strength["score"] += 1
        if len(password) >= 16:
            strength["score"] += 1
        
        # Character variety
        if re.search(r'[A-Z]', password):
            strength["score"] += 1
        else:
            strength["feedback"].append("Add uppercase letters")
        
        if re.search(r'[a-z]', password):
            strength["score"] += 1
        else:
            strength["feedback"].append("Add lowercase letters")
        
        if re.search(r'\d', password):
            strength["score"] += 1
        else:
            strength["feedback"].append("Add numbers")
        
        if re.search(r'[!@#$%^&*(),.?":{}|<>]', password):
            strength["score"] += 1
        else:
            strength["feedback"].append("Add special characters")
        
        # Determine level
        if strength["score"] <= 3:
            strength["level"] = "weak"
        elif strength["score"] <= 5:
            strength["level"] = "medium"
        else:
            strength["level"] = "strong"
        
        return strength

# Convenience functions for backward compatibility
def hash_password(password: str) -> str:
    return PasswordHandler.hash_password(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return PasswordHandler.verify_password(plain_password, hashed_password)

def validate_password(password: str) -> bool:
    return PasswordHandler.validate_password(password)
