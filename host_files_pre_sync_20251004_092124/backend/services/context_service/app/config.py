import os
from dotenv import load_dotenv

load_dotenv()

class Settings:
    GRPC_PORT: int = int(os.getenv("GRPC_PORT", 5009))
    SERVICE_NAME: str = os.getenv("SERVICE_NAME", "Context_servicePythonPrisma")

settings = Settings()
