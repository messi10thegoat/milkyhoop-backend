import os
from dotenv import load_dotenv

load_dotenv()

class Settings:
    GRPC_PORT: int = int(os.getenv("GRPC_PORT", 7040))
    SERVICE_NAME: str = os.getenv("SERVICE_NAME", "InventoryService")
    DATABASE_URL: str = os.getenv("DATABASE_URL", "")

settings = Settings()