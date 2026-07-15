from redis.asyncio import Redis

from app.config import settings

# Explicit, short timeouts are what actually makes "fail open" mean
# something: with the client library's defaults (no timeout at all), a
# stopped-but-not-DNS-broken Redis container makes every request hang on a
# TCP-level timeout (which can be tens of seconds) before the RedisError
# fail-open path in auth.py / rate_limit.py ever gets a chance to run —
# discovered live, killing the Redis container mid-traffic while verifying
# Week 5's fail-open behavior. A fail-open design that takes 30+ seconds per
# request to trigger isn't preserving availability, it's just replacing a
# clean failure with a slow one.
_redis: Redis = Redis.from_url(
    settings.redis_url,
    decode_responses=True,
    socket_connect_timeout=0.2,
    socket_timeout=0.2,
)


async def get_redis() -> Redis:
    return _redis
