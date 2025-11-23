"""
business_parser/app/config.py

Configuration for Business Parser Service

Author: MilkyHoop Team
Version: 2.0.0 - Phase 2 Optimization (Redis Cache)
"""

import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    """Business Parser service settings"""
    
    # Service Configuration
    GRPC_PORT: int = int(os.getenv("GRPC_PORT", 5018))
    SERVICE_NAME: str = os.getenv("SERVICE_NAME", "BusinessParser")
    
    # OpenAI Configuration
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    
    # Redis Configuration (Phase 2: Caching)
    REDIS_HOST: str = os.getenv("REDIS_HOST", "redis")
    REDIS_PORT: int = int(os.getenv("REDIS_PORT", 6379))
    REDIS_PASSWORD: str = os.getenv("REDIS_PASSWORD", "MilkyRedis2025Secure")
    REDIS_DB: int = int(os.getenv("REDIS_DB", "0"))  # DB 0 for business_parser
    REDIS_CACHE_TTL: int = int(os.getenv("REDIS_CACHE_TTL", "300"))  # 5 minutes
    
    # Database (if needed)
    DATABASE_URL: str = os.getenv("DATABASE_URL", "")


settings = Settings()