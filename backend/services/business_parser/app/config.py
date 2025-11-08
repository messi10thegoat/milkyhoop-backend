"""
business_parser/app/config.py

Configuration for Business Parser Service

Author: MilkyHoop Team
Version: 1.0.0
"""

import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    """Business Parser service settings"""
    
    GRPC_PORT: int = int(os.getenv("GRPC_PORT", 5018))
    SERVICE_NAME: str = os.getenv("SERVICE_NAME", "BusinessParser")
    
    # OpenAI Configuration
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    
    # Database (if needed)
    DATABASE_URL: str = os.getenv("DATABASE_URL", "")


settings = Settings()