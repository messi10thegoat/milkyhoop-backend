"""
Configuration for Accounting Service
"""
import os

class Settings:
    SERVICE_NAME = "AccountingService"
    GRPC_PORT = int(os.getenv("GRPC_PORT", "7050"))
    DATABASE_URL = os.getenv("DATABASE_URL", "")
    
settings = Settings()
