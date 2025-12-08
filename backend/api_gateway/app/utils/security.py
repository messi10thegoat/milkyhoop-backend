"""
Security Utilities
Input sanitization, validation, and security helpers
"""
import re
import html
import hashlib
import secrets
from typing import Optional, List, Any
import logging

logger = logging.getLogger(__name__)


class InputSanitizer:
    """
    Sanitizes user input to prevent XSS, SQL injection, and other attacks.
    """

    # Dangerous patterns
    SQL_INJECTION_PATTERNS = [
        r"(\b(SELECT|INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|TRUNCATE)\b)",
        r"(--|#|/\*|\*/)",
        r"(\b(UNION|JOIN)\b.*\b(SELECT)\b)",
        r"(;.*\b(DROP|DELETE|UPDATE)\b)",
    ]

    XSS_PATTERNS = [
        r"<script[^>]*>.*?</script>",
        r"javascript:",
        r"on\w+\s*=",
        r"<iframe[^>]*>",
        r"<object[^>]*>",
        r"<embed[^>]*>",
    ]

    @classmethod
    def sanitize_string(cls, value: str, max_length: int = 10000) -> str:
        """
        Sanitize a string input:
        - Escape HTML entities
        - Truncate to max length
        - Remove null bytes
        """
        if not value:
            return ""

        # Remove null bytes
        value = value.replace("\x00", "")

        # Truncate
        if len(value) > max_length:
            value = value[:max_length]

        # Escape HTML entities
        value = html.escape(value)

        return value

    @classmethod
    def sanitize_html(cls, value: str) -> str:
        """Remove all HTML tags from input"""
        if not value:
            return ""
        return re.sub(r"<[^>]+>", "", value)

    @classmethod
    def is_safe_sql_input(cls, value: str) -> bool:
        """Check if input is safe from SQL injection attempts"""
        if not value:
            return True

        value_upper = value.upper()
        for pattern in cls.SQL_INJECTION_PATTERNS:
            if re.search(pattern, value_upper, re.IGNORECASE):
                logger.warning(f"Potential SQL injection detected: {value[:50]}...")
                return False
        return True

    @classmethod
    def is_safe_xss_input(cls, value: str) -> bool:
        """Check if input is safe from XSS attempts"""
        if not value:
            return True

        for pattern in cls.XSS_PATTERNS:
            if re.search(pattern, value, re.IGNORECASE):
                logger.warning(f"Potential XSS detected: {value[:50]}...")
                return False
        return True

    @classmethod
    def sanitize_filename(cls, filename: str) -> str:
        """Sanitize filename to prevent path traversal"""
        if not filename:
            return ""

        # Remove path separators and dangerous characters
        filename = re.sub(r"[/\\]", "", filename)
        filename = re.sub(r"\.{2,}", ".", filename)  # Remove ..
        filename = re.sub(r"[<>:\"'|?*\x00-\x1f]", "", filename)

        return filename[:255]  # Max filename length

    @classmethod
    def validate_email(cls, email: str) -> bool:
        """Validate email format"""
        if not email:
            return False

        pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
        return bool(re.match(pattern, email)) and len(email) <= 254

    @classmethod
    def validate_phone_indonesia(cls, phone: str) -> bool:
        """Validate Indonesian phone number format"""
        if not phone:
            return False

        # Remove spaces and dashes
        phone = re.sub(r"[\s\-]", "", phone)

        # Indonesian phone patterns
        patterns = [
            r"^(\+62|62|0)[8][0-9]{8,11}$",  # Mobile
            r"^(\+62|62|0)[2-9][0-9]{6,10}$",  # Landline
        ]

        return any(re.match(p, phone) for p in patterns)


class SecureTokenGenerator:
    """Generate cryptographically secure tokens"""

    @staticmethod
    def generate_token(length: int = 32) -> str:
        """Generate a URL-safe token"""
        return secrets.token_urlsafe(length)

    @staticmethod
    def generate_hex_token(length: int = 32) -> str:
        """Generate a hex token"""
        return secrets.token_hex(length)

    @staticmethod
    def generate_numeric_otp(length: int = 6) -> str:
        """Generate numeric OTP"""
        return "".join(str(secrets.randbelow(10)) for _ in range(length))

    @staticmethod
    def hash_token(token: str, salt: Optional[str] = None) -> str:
        """Hash a token with optional salt"""
        if salt:
            token = f"{salt}:{token}"
        return hashlib.sha256(token.encode()).hexdigest()

    @staticmethod
    def compare_tokens(token1: str, token2: str) -> bool:
        """Constant-time token comparison to prevent timing attacks"""
        return secrets.compare_digest(token1, token2)


class PasswordValidator:
    """
    Validates password strength according to security best practices.
    """

    MIN_LENGTH = 8
    MAX_LENGTH = 128

    @classmethod
    def validate(cls, password: str) -> List[str]:
        """
        Validate password strength.
        Returns list of validation errors (empty if valid).
        """
        errors = []

        if not password:
            return ["Password is required"]

        if len(password) < cls.MIN_LENGTH:
            errors.append(f"Password must be at least {cls.MIN_LENGTH} characters")

        if len(password) > cls.MAX_LENGTH:
            errors.append(f"Password must not exceed {cls.MAX_LENGTH} characters")

        if not re.search(r"[a-z]", password):
            errors.append("Password must contain at least one lowercase letter")

        if not re.search(r"[A-Z]", password):
            errors.append("Password must contain at least one uppercase letter")

        if not re.search(r"\d", password):
            errors.append("Password must contain at least one digit")

        if not re.search(r"[!@#$%^&*(),.?\":{}|<>]", password):
            errors.append("Password must contain at least one special character")

        # Check for common weak passwords
        weak_passwords = [
            "password", "123456", "qwerty", "admin", "letmein",
            "welcome", "monkey", "dragon", "master", "login"
        ]
        if password.lower() in weak_passwords:
            errors.append("Password is too common")

        return errors

    @classmethod
    def get_strength_score(cls, password: str) -> int:
        """
        Calculate password strength score (0-100).
        """
        if not password:
            return 0

        score = 0

        # Length score (up to 30 points)
        score += min(len(password) * 2, 30)

        # Character variety (up to 40 points)
        if re.search(r"[a-z]", password):
            score += 10
        if re.search(r"[A-Z]", password):
            score += 10
        if re.search(r"\d", password):
            score += 10
        if re.search(r"[!@#$%^&*(),.?\":{}|<>]", password):
            score += 10

        # Bonus for mixed case and special chars together (up to 20 points)
        if re.search(r"[a-z]", password) and re.search(r"[A-Z]", password):
            score += 10
        if re.search(r"\d", password) and re.search(r"[!@#$%^&*(),.?\":{}|<>]", password):
            score += 10

        return min(score, 100)
