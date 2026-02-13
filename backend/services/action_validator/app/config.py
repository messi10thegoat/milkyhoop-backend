import os


class Settings:
    GRPC_PORT = int(os.getenv("GRPC_PORT", "5091"))
    GRPC_MAX_WORKERS = int(os.getenv("GRPC_MAX_WORKERS", "10"))
    DB_HOST = os.getenv("DB_HOST", "postgres")
    DB_PORT = int(os.getenv("DB_PORT", "5432"))
    DB_USER = os.getenv("DB_USER", "postgres")
    DB_PASSWORD = os.getenv("DB_PASSWORD", "Proyek771977")
    DB_NAME = os.getenv("DB_NAME", "milkydb")
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

    # Validation thresholds
    HIGH_AMOUNT_THRESHOLD = float(os.getenv("HIGH_AMOUNT_THRESHOLD", "50000000"))  # 50M IDR
    CRITICAL_AMOUNT_THRESHOLD = float(os.getenv("CRITICAL_AMOUNT_THRESHOLD", "500000000"))  # 500M IDR

    # DB pool
    DB_MIN_POOL = int(os.getenv("DB_MIN_POOL", "2"))
    DB_MAX_POOL = int(os.getenv("DB_MAX_POOL", "10"))

    @property
    def dsn(self) -> str:
        return f"postgresql://{self.DB_USER}:{self.DB_PASSWORD}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"


settings = Settings()
