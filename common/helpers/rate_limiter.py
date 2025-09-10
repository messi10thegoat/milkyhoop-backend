import redis
import os
import time
from structlog import get_logger

logger = get_logger()

RATE_LIMIT_LUA_SCRIPT = """
local key = KEYS[1]
local rate = tonumber(ARGV[1])
local capacity = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local fill_time = capacity / rate
local ttl = math.floor(fill_time * 2)
local last_tokens = tonumber(redis.call("get", key) or capacity)
local delta = math.max(0, now - (tonumber(redis.call("get", key .. ":ts") or now)))
local filled_tokens = math.min(capacity, last_tokens + (delta * rate))
if filled_tokens < 1 then
  return -1
else
  redis.call("set", key, filled_tokens - 1, "EX", ttl)
  redis.call("set", key .. ":ts", now, "EX", ttl)
  return filled_tokens - 1
end
"""

class RateLimiter:
    def __init__(self, redis_host='redis', redis_port=6379, prefix='rate:user:', rate=1, capacity=10):
        self.prefix = prefix
        self.rate = rate
        self.capacity = capacity
        self.client = redis.Redis(host=redis_host, port=redis_port, decode_responses=True)
        self.lua = self.client.register_script(RATE_LIMIT_LUA_SCRIPT)

    def is_allowed(self, key: str) -> bool:
        full_key = self.prefix + key
        now = int(time.time())
        try:
            result = self.lua(keys=[full_key], args=[self.rate, self.capacity, now])
            if int(result) == -1:
                logger.warn("Rate limit exceeded", key=key)
                return False
            return True
        except Exception as e:
            logger.error("Rate limiter failed", error=str(e))
            # fallback: always allow
            return True

