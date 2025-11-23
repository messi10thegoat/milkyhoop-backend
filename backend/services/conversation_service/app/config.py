import os
from dotenv import load_dotenv

load_dotenv()

class Settings:
    # Service config
    GRPC_PORT: int = int(os.getenv("GRPC_PORT", "5002"))
    SERVICE_NAME: str = os.getenv("SERVICE_NAME", "ConversationService")
    
    # Database
    DATABASE_URL: str = os.getenv("DATABASE_URL", "")
    
    # Retention policy (90 days in seconds)
    MESSAGE_RETENTION_DAYS: int = int(os.getenv("MESSAGE_RETENTION_DAYS", "90"))
    
    # Pagination defaults
    DEFAULT_PAGE_LIMIT: int = 30
    MAX_PAGE_LIMIT: int = 100
    
    # Logging
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

settings = Settings()