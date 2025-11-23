"""
rule_engine/app/config.py

Configuration for Rule Engine Service

Author: MilkyHoop Team
Version: 1.0.0
"""

import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    """Rule Engine service settings"""

    # Service Configuration
    GRPC_PORT: int = int(os.getenv("GRPC_PORT", 5070))
    SERVICE_NAME: str = os.getenv("SERVICE_NAME", "RuleEngine")

    # Database Configuration
    DATABASE_URL: str = os.getenv("DATABASE_URL", "")

    # Cache Configuration
    CACHE_TTL_SECONDS: int = int(os.getenv("CACHE_TTL_SECONDS", "300"))  # 5 minutes

    # Logging
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")


settings = Settings()
