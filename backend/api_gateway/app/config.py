"""
Centralized Configuration - API Gateway
All secrets loaded from environment variables
"""
import os
from typing import Optional, List
from functools import lru_cache
from dotenv import load_dotenv

load_dotenv()


class Settings:
    """Application settings loaded from environment variables"""

    # Service Configuration
    GRPC_PORT: int = int(os.getenv("GRPC_PORT", 5009))
    SERVICE_NAME: str = os.getenv("SERVICE_NAME", "MilkyHoopAPIGateway")

    # Database Configuration (loaded from env - NO DEFAULTS for secrets)
    DB_HOST: str = os.getenv("DB_HOST", "postgres")
    DB_PORT: int = int(os.getenv("DB_PORT", "5432"))
    DB_USER: str = os.getenv("DB_USER", "postgres")
    DB_PASSWORD: str = os.getenv("DB_PASSWORD", "")  # REQUIRED
    DB_NAME: str = os.getenv("DB_NAME", "milkydb")
    DB_SSL_MODE: str = os.getenv("DB_SSL_MODE", "prefer")

    # Full DATABASE_URL (alternative)
    DATABASE_URL: Optional[str] = os.getenv("DATABASE_URL")

    # Redis Configuration
    REDIS_HOST: str = os.getenv("REDIS_HOST", "redis")
    REDIS_PORT: int = int(os.getenv("REDIS_PORT", "6379"))
    REDIS_PASSWORD: str = os.getenv("REDIS_PASSWORD", "")  # REQUIRED

    @property
    def REDIS_URL(self) -> str:
        """Build Redis URL from components or use env var directly"""
        url = os.getenv("REDIS_URL")
        if url:
            return url
        if self.REDIS_PASSWORD:
            return f"redis://:{self.REDIS_PASSWORD}@{self.REDIS_HOST}:{self.REDIS_PORT}/0"
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/0"

    # JWT Configuration
    JWT_SECRET: str = os.getenv("JWT_SECRET", "")  # REQUIRED
    JWT_ALGORITHM: str = os.getenv("JWT_ALGORITHM", "HS256")

    # Rate Limiting Configuration
    RATE_LIMIT_ENABLED: bool = os.getenv("RATE_LIMIT_ENABLED", "true").lower() == "true"
    RATE_LIMIT_REQUESTS: int = int(os.getenv("RATE_LIMIT_REQUESTS", "100"))
    RATE_LIMIT_WINDOW: int = int(os.getenv("RATE_LIMIT_WINDOW", "60"))  # seconds
    RATE_LIMIT_AUTH_REQUESTS: int = int(os.getenv("RATE_LIMIT_AUTH_REQUESTS", "10"))  # stricter for auth
    RATE_LIMIT_AUTH_WINDOW: int = int(os.getenv("RATE_LIMIT_AUTH_WINDOW", "60"))

    # CORS Configuration
    CORS_ORIGINS: List[str] = [
        origin.strip() for origin in
        os.getenv(
            "CORS_ORIGINS",
            "http://localhost:3000,http://localhost:3001,http://localhost:3002,http://localhost:3003,http://localhost:3004,http://localhost:5173,http://127.0.0.1:3000,http://127.0.0.1:3003,http://127.0.0.1:5173,https://milkyhoop.com,https://dev.milkyhoop.com,https://staging.milkyhoop.com"
        ).split(",")
    ]
    CORS_ALLOW_HEADERS: List[str] = [
        "Content-Type",
        "Authorization",
        "Accept",
        "Origin",
        "X-Requested-With",
        "X-Request-ID",
    ]

    # Security
    DEBUG: bool = os.getenv("DEBUG", "false").lower() == "true"
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "production")

    # Field-Level Encryption (FLE) Configuration
    FLE_ENABLED: bool = os.getenv("FLE_ENABLED", "true").lower() == "true"
    FLE_PRIMARY_KEK: str = os.getenv("FLE_PRIMARY_KEK", "")
    FLE_SECONDARY_KEK: str = os.getenv("FLE_SECONDARY_KEK", "")
    FLE_BLIND_INDEX_SALT: str = os.getenv("FLE_BLIND_INDEX_SALT", "milkyhoop-blind-index-default")

    # HashiCorp Vault Configuration (optional KMS)
    VAULT_ENABLED: bool = os.getenv("VAULT_ENABLED", "false").lower() == "true"
    VAULT_ADDR: str = os.getenv("VAULT_ADDR", "http://127.0.0.1:8200")
    VAULT_TOKEN: str = os.getenv("VAULT_TOKEN", "")
    VAULT_KEY_NAME: str = os.getenv("VAULT_KEY_NAME", "milkyhoop-fle")
    VAULT_TRANSIT_PATH: str = os.getenv("VAULT_TRANSIT_PATH", "transit")

    @classmethod
    def validate(cls) -> List[str]:
        """Validate required environment variables are set"""
        errors = []

        if not cls.DB_PASSWORD and not cls.DATABASE_URL:
            errors.append("DB_PASSWORD or DATABASE_URL environment variable is required")

        if not cls.JWT_SECRET:
            errors.append("JWT_SECRET environment variable is required")

        if cls.JWT_SECRET and len(cls.JWT_SECRET) < 32:
            errors.append("JWT_SECRET must be at least 32 characters")

        # FLE validation (warning only, not error, for backward compatibility)
        if cls.FLE_ENABLED and not cls.FLE_PRIMARY_KEK:
            errors.append("FLE_PRIMARY_KEK required when FLE_ENABLED=true (or set FLE_ENABLED=false)")

        # Vault validation
        if cls.VAULT_ENABLED and not cls.VAULT_TOKEN:
            errors.append("VAULT_TOKEN required when VAULT_ENABLED=true")

        return errors

    # Database SSL Configuration
    DB_SSL_ENABLED: bool = os.getenv("DB_SSL_ENABLED", "false").lower() == "true"
    DB_SSL_CA_PATH: str = os.getenv("DB_SSL_CA_PATH", "/etc/ssl/milkyhoop/ca.crt")

    @classmethod
    def get_db_config(cls) -> dict:
        """Get database connection configuration with optional SSL"""
        import ssl

        config = {
            "host": cls.DB_HOST,
            "port": cls.DB_PORT,
            "user": cls.DB_USER,
            "password": cls.DB_PASSWORD,
            "database": cls.DB_NAME,
        }

        # Add SSL context if enabled
        if cls.DB_SSL_ENABLED:
            ssl_context = ssl.create_default_context(
                ssl.Purpose.SERVER_AUTH,
                cafile=cls.DB_SSL_CA_PATH if os.path.exists(cls.DB_SSL_CA_PATH) else None
            )
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_OPTIONAL
            config["ssl"] = ssl_context

        return config


settings = Settings()
