import hashlib
import time

# In-memory cache (simple dict)
# For production, use Redis
_recent_cache = {}
CACHE_TTL = 3.0  # seconds

def cache_lookup(img_bytes: bytes) -> list[dict] | None:
    """
    Check if image has been decoded recently.
    Uses SHA256 hash of image bytes as key.
    """
    h = hashlib.sha256(img_bytes).hexdigest()
    now = time.time()

    entry = _recent_cache.get(h)
    if entry and now - entry['timestamp'] < CACHE_TTL:
        return entry['results']

    return None

def cache_store(img_bytes: bytes, results: list[dict]) -> None:
    """
    Store decode result in cache.
    """
    h = hashlib.sha256(img_bytes).hexdigest()
    _recent_cache[h] = {
        'timestamp': time.time(),
        'results': results
    }

    # Simple cleanup: remove entries older than TTL
    now = time.time()
    to_delete = [k for k, v in _recent_cache.items() if now - v['timestamp'] > CACHE_TTL]
    for k in to_delete:
        del _recent_cache[k]
