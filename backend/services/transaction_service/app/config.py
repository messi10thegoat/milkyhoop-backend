import os
from dotenv import load_dotenv

load_dotenv()

class Settings:
    GRPC_PORT: int = int(os.getenv("GRPC_PORT", 7020))
    SERVICE_NAME: str = os.getenv("SERVICE_NAME", "TransactionService")
    DATABASE_URL: str = os.getenv("DATABASE_URL", "")
    
    # Inventory Service Configuration
    INVENTORY_SERVICE_URL: str = os.getenv("INVENTORY_SERVICE_URL", "milkyhoop-dev-inventory_service-1:7040")

settings = Settings()