import os
from dotenv import load_dotenv

load_dotenv()

class Settings:
    GRPC_PORT: int = int(os.getenv("GRPC_PORT", 5012))
    SERVICE_NAME: str = os.getenv("SERVICE_NAME", "Tenant_parserPythonPrisma")

settings = Settings()
