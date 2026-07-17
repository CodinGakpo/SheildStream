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

# A separate connection for long-lived Pub/Sub listeners (Week 9's policy
# invalidation subscriber and dashboard WS fan-out) — pubsub.listen() blocks
# on the socket waiting for the *next message*, which during a quiet period
# can easily exceed the 0.2s timeout above and get mistaken for a dead
# connection, spinning the reconnect loop continuously even though Redis is
# perfectly healthy. That short timeout is deliberately tuned for the
# request-path fail-open checks (see comment above); a subscriber needs a
# connection that can sit idle indefinitely without erroring.
_pubsub_redis: Redis = Redis.from_url(settings.redis_url, decode_responses=True)


async def get_redis() -> Redis:
    return _redis


async def get_pubsub_redis() -> Redis:
    return _pubsub_redis
