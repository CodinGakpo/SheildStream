from redis.asyncio import Redis

from app.config import settings

_redis: Redis = Redis.from_url(settings.redis_url, decode_responses=True)


async def get_redis() -> Redis:
    return _redis
