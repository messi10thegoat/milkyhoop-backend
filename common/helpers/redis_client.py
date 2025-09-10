import os
import time
import logging
from redis import Redis
from redis.exceptions import RedisError
from redis.retry import Retry
from redis.backoff import ExponentialBackoff

class RedisClient:
    def __init__(
        self,
        host: str = os.getenv("REDIS_HOST", "redis"),
        port: int = int(os.getenv("REDIS_PORT", 6379)),
        db: int = 0,
        max_retries: int = 5,
        retry_delay: float = 1.0,
        socket_timeout: int = 5,
    ):
        self.host = host
        self.port = port
        self.db = db
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.socket_timeout = socket_timeout

        self.logger = logging.getLogger(__name__)
        self.client = self._connect_with_retry()

    def _connect_with_retry(self) -> Redis:
        retries = 0
        while retries < self.max_retries:
            try:
                client = Redis(
                    host=self.host,
                    port=self.port,
                    db=self.db,
                    decode_responses=True,
                    socket_timeout=self.socket_timeout,
                    retry=Retry(ExponentialBackoff(base=self.retry_delay), retries=self.max_retries),
                )
                client.ping()
                self.logger.info("✅ Redis connected successfully", extra={
                    "host": self.host,
                    "port": self.port,
                    "db": self.db
                })
                return client
            except RedisError as e:
                retries += 1
                self.logger.warning(
                    f"⚠️ Redis connection failed ({retries}/{self.max_retries}): {e}",
                    extra={"host": self.host, "port": self.port}
                )
                time.sleep(self.retry_delay)
        raise ConnectionError(f"❌ Could not connect to Redis at {self.host}:{self.port} after {self.max_retries} retries")

    def get_client(self) -> Redis:
        return self.client

# ✅ Fungsi global agar bisa diimpor langsung
def get_redis_client() -> Redis:
    return RedisClient().get_client()
