"""Configuration for action_executor service."""
import os


class Settings:
    SERVICE_NAME = "action_executor"
    SERVICE_VERSION = "0.1.0"

    GRPC_PORT = int(os.getenv("GRPC_PORT", "5092"))
    GRPC_MAX_WORKERS = int(os.getenv("GRPC_MAX_WORKERS", "10"))

    DB_HOST = os.getenv("DB_HOST", "postgres")
    DB_PORT = int(os.getenv("DB_PORT", "5432"))
    DB_USER = os.getenv("DB_USER", "postgres")
    DB_PASSWORD = os.getenv("DB_PASSWORD", "Proyek771977")
    DB_NAME = os.getenv("DB_NAME", "milkydb")
    DB_MIN_POOL = int(os.getenv("DB_MIN_POOL", "2"))
    DB_MAX_POOL = int(os.getenv("DB_MAX_POOL", "10"))

    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

    PENDING_ACTION_TTL_MINUTES = int(os.getenv("PENDING_ACTION_TTL_MINUTES", "15"))
    EXPIRATION_CHECK_INTERVAL_SECONDS = int(os.getenv("EXPIRATION_CHECK_INTERVAL", "60"))

    # Kernel routing (future)
    API_GATEWAY_URL = os.getenv("API_GATEWAY_URL", "http://api_gateway:8000")

    @property
    def dsn(self) -> str:
        return (
            f"postgresql://{self.DB_USER}:{self.DB_PASSWORD}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
        )


settings = Settings()
