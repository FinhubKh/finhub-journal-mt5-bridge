import time
import uuid

# Atomic compare-and-delete so we never release another worker's lock.
_RELEASE_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
else
  return 0
end
"""

# Refresh TTL only if we still own the lock.
_REFRESH_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('expire', KEYS[1], ARGV[2])
else
  return 0
end
"""


def lock_held(redis_client, key: str) -> bool:
    return bool(redis_client.get(key))


class RedisLock:
    """Simple Redis NX lock so only one process drives the MT5 terminal at a time."""

    def __init__(
        self,
        client,
        key: str,
        *,
        ttl_seconds: int = 300,
        wait_seconds: int = 120,
        poll_seconds: float = 0.05,
    ):
        self.client = client
        self.key = key
        self.ttl_seconds = ttl_seconds
        self.wait_seconds = wait_seconds
        self.poll_seconds = poll_seconds
        self.token = uuid.uuid4().hex

    def acquire(self) -> bool:
        deadline = time.monotonic() + self.wait_seconds
        while time.monotonic() < deadline:
            if self.client.set(self.key, self.token, nx=True, ex=self.ttl_seconds):
                return True
            time.sleep(self.poll_seconds)
        return False

    def refresh(self) -> bool:
        """Extend lock TTL during long terminal I/O (history pulls)."""
        try:
            if hasattr(self.client, "eval"):
                return bool(
                    self.client.eval(
                        _REFRESH_LUA,
                        1,
                        self.key,
                        self.token,
                        int(self.ttl_seconds),
                    )
                )
            if self.client.get(self.key) == self.token:
                expire = getattr(self.client, "expire", None)
                if callable(expire):
                    expire(self.key, int(self.ttl_seconds))
                    return True
                # Fake / minimal redis: rewrite with same token + TTL.
                self.client.set(self.key, self.token, ex=self.ttl_seconds)
                return True
        except Exception:
            return False
        return False

    def release(self) -> None:
        try:
            if hasattr(self.client, "eval"):
                self.client.eval(_RELEASE_LUA, 1, self.key, self.token)
                return
            if self.client.get(self.key) == self.token:
                self.client.delete(self.key)
        except Exception:
            pass

    def __enter__(self):
        if not self.acquire():
            raise TimeoutError(f"Could not acquire lock {self.key}")
        return self

    def __exit__(self, exc_type, exc, tb):
        self.release()
        return False
