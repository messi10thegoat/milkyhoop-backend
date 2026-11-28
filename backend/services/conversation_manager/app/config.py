import os
from dotenv import load_dotenv

load_dotenv()

class Settings:
    GRPC_PORT: int = int(os.getenv("GRPC_PORT", 5016))
    SERVICE_NAME: str = os.getenv("SERVICE_NAME", "ConversationManager")

    # Redis Configuration (INSIDE class!)
    # Format: redis://:<REDIS_PASSWORD>@[HOST]:[PORT]
    REDIS_URL: str = os.environ["REDIS_URL"]
    REDIS_PASSWORD: str = os.environ["REDIS_PASSWORD"]

settings = Settings()
