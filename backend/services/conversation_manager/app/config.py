import os
from dotenv import load_dotenv

load_dotenv()

class Settings:
    GRPC_PORT: int = int(os.getenv("GRPC_PORT", 5016))
    SERVICE_NAME: str = os.getenv("SERVICE_NAME", "ConversationManager")
    
    # Redis Configuration (INSIDE class!)
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://redis:6379")
    REDIS_PASSWORD: str = os.getenv("REDIS_PASSWORD", "MilkyRedis2025Secure")

settings = Settings()
