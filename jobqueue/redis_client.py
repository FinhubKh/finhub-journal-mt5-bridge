import redis


def make_redis(url: str, *, max_connections: int = 32) -> redis.Redis:
    """Pooled Redis client tuned for the bridge API and workers."""
    return redis.from_url(
        url,
        decode_responses=True,
        health_check_interval=30,
        socket_keepalive=True,
        retry_on_timeout=True,
        max_connections=max_connections,
    )
