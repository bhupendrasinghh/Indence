import time
import logging
import threading
from typing import Optional
from .config import settings

logger = logging.getLogger("evidence_platform.rate_limiter")

try:
    import redis
    _HAS_REDIS = True
except ImportError:
    _HAS_REDIS = False
    redis = None


class RedisRateLimiter:
    """Sliding window rate limiter using Redis."""

    def __init__(self, redis_url: str) -> None:
        self.redis_url = redis_url
        self.client: Optional[redis.Redis] = None
        if _HAS_REDIS and redis_url:
            try:
                self.client = redis.Redis.from_url(redis_url, socket_connect_timeout=2.0)
                # Test connection
                self.client.ping()
                logger.info(f"Connected to Redis rate limiter at {redis_url}")
            except Exception as e:
                logger.warning(f"Could not connect to Redis at {redis_url}: {e}. Rate limiter falling back to memory.")
                self.client = None

    def is_allowed(self, key: str, limit: int, window: int) -> bool:
        """Checks if a request is allowed under the limit within the time window."""
        if not self.client:
            return False

        try:
            now = time.time()
            pipe = self.client.pipeline()
            # Clear old records outside the window
            pipe.zremrangebyscore(key, 0, now - window)
            # Count elements in window
            pipe.zcard(key)
            # Add current timestamp
            pipe.zadd(key, {str(now): now})
            # Set key expiration to avoid leakage
            pipe.expire(key, window + 5)
            # Execute transactions
            _, count, _, _ = pipe.execute()
            
            return count < limit
        except Exception as e:
            logger.error(f"Redis rate limiter execution failed: {e}")
            return True  # Fail open in production to prevent complete outages


class MemoryRateLimiter:
    """Thread-safe sliding window rate limiter in memory for fallback."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # Maps key -> list of float timestamps
        self._requests: dict[str, list[float]] = {}

    def is_allowed(self, key: str, limit: int, window: int) -> bool:
        now = time.time()
        with self._lock:
            if key not in self._requests:
                self._requests[key] = []
            
            # Filter timestamps in the window
            history = self._requests.get(key, [])
            history = [t for t in history if t > now - window]
            
            if len(history) < limit:
                history.append(now)
                self._requests[key] = history
                return True
            else:
                if history:
                    self._requests[key] = history
                elif key in self._requests:
                    del self._requests[key]
                return False


class RateLimiter:
    """Orchestrator choosing between Redis and local memory rate limiter."""

    def __init__(self) -> None:
        self.redis_limiter = RedisRateLimiter(settings.REDIS_URL)
        self.memory_limiter = MemoryRateLimiter()

    def is_allowed(self, identifier: str, limit: int = 60, window_seconds: int = 60) -> bool:
        """Determines if the client identifier is allowed to make a request."""
        # Namespace keys to prevent collisions
        key = f"rate_limit:{identifier}"
        
        if self.redis_limiter.client:
            return self.redis_limiter.is_allowed(key, limit, window_seconds)
        else:
            return self.memory_limiter.is_allowed(key, limit, window_seconds)

rate_limiter = RateLimiter()
