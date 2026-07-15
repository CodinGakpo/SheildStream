import hashlib
import json

from fastapi import Depends, Header, HTTPException
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session_factory
from app.redis_client import get_redis
from app.tracing import tracer

CACHE_TTL_S = 30  # see Week 3 decision log


def hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


async def lookup_tenant_by_key_hash(db: AsyncSession, key_hash: str) -> dict | None:
    result = await db.execute(
        text(
            """
            SELECT id, name, upstream_base_url, ip_hash_salt
            FROM tenants
            WHERE api_key_hash = :hash
            """
        ),
        {"hash": key_hash},
    )
    row = result.mappings().one_or_none()
    if row is None:
        return None
    return {
        "id": str(row["id"]),
        "name": row["name"],
        "upstream_base_url": row["upstream_base_url"],
        "ip_hash_salt": row["ip_hash_salt"],
    }


async def get_tenant(
    x_api_key: str = Header(...),
    redis: Redis = Depends(get_redis),
) -> dict:
    """REVISION #1: keyed by the full SHA-256 hash of the presented key, not a
    raw-key prefix — the hash is already one-way, so caching it directly (rather
    than a truncated raw prefix, as the original guide does) leaks nothing extra
    while still giving a single indexed DB lookup on a cache miss.

    No `db: AsyncSession = Depends(...)` parameter here — see the comment on
    `get_db_session` in app/db.py for why. A session is opened explicitly,
    only inside the cache-miss branch below."""
    with tracer.start_as_current_span("auth.validate_key") as span:
        key_hash = hash_key(x_api_key)
        cache_key = f"tenant:apikey:{key_hash}"

        cached = await redis.get(cache_key)
        if cached:
            span.set_attribute("shieldstream.cache_hit", True)
            return json.loads(cached)

        span.set_attribute("shieldstream.cache_hit", False)
        async with session_factory() as db:
            tenant = await lookup_tenant_by_key_hash(db, key_hash)
        if tenant is None:
            raise HTTPException(status_code=401, detail="invalid API key")

        await redis.set(cache_key, json.dumps(tenant), ex=CACHE_TTL_S)
        return tenant
