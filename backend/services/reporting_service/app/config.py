import os
from dotenv import load_dotenv

load_dotenv()

class Settings:
    GRPC_PORT: int = int(os.getenv("GRPC_PORT", 7030))
    SERVICE_NAME: str = os.getenv("SERVICE_NAME", "ReportingService")
    DATABASE_URL: str = os.getenv("DATABASE_URL", "")

settings = Settings()