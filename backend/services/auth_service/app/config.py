import os

class Settings:
    """Service Configuration"""
    SERVICE_NAME = "Auth_servicePythonPrisma"
    SERVICE_PORT = int(os.getenv("SERVICE_PORT", "8013"))
    DATABASE_URL = os.getenv("DATABASE_URL", "")
    JWT_SECRET = os.getenv("JWT_SECRET", "")
    REDIS_URL = os.getenv("REDIS_URL", "")

settings = Settings()
