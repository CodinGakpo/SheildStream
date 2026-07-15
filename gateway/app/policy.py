import json
import logging

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session_factory

logger = logging.getLogger("shieldstream.policy")

# Shorter than auth.py's 30s tenant-identity cache: a rate limit is exactly
# the lever an operator pulls during an active incident (tightening a limit
# on an attacking tenant), so a shorter window trades a small amount of
# extra DB load for faster propagation of an urgent change. Week 9 adds a
# Redis Pub/Sub push channel on top of this TTL for near-instant propagation
# without removing the TTL — the TTL stays as the correctness backstop for
# a replica that missed the push message.
CACHE_TTL_S = 10


async def _fetch_matching_policy(db: AsyncSession, tenant_id: str, route: str) -> dict | None:
    # route_pattern is a simple glob (e.g. "/proxy/*"); '*' -> SQL LIKE '%'.
    # Ties broken by longest pattern first, so a more specific rule
    # (e.g. "/proxy/admin/*") wins over a catch-all ("/proxy/*") if both
    # would otherwise match.
    result = await db.execute(
        text(
            """
            SELECT rate_limit_rps, rate_limit_window_s, policy_version
            FROM policies
            WHERE tenant_id = :tenant_id
              AND :route LIKE replace(route_pattern, '*', '%')
            ORDER BY length(route_pattern) DESC
            LIMIT 1
            """
        ),
        {"tenant_id": tenant_id, "route": route},
    )
    row = result.mappings().one_or_none()
    if row is None:
        return None
    return {
        "rate_limit_rps": row["rate_limit_rps"],
        "rate_limit_window_s": row["rate_limit_window_s"],
        "policy_version": row["policy_version"],
    }


async def get_policy(redis: Redis, tenant_id: str, route: str) -> dict | None:
    """Redis-cached, Postgres-backed policy lookup.

    Resilient to Redis being unreachable: a RedisError on the cache read
    (not just a cache miss) falls through to Postgres, and a failure on the
    cache repopulation write is swallowed rather than raised — a read-through
    cache degrading to "always ask the source of truth" is exactly the
    correct behavior when the cache itself is unavailable, and it means the
    Redis-outage handling that matters (the sliding-window check itself,
    which has no Postgres equivalent) is the only place fail-open logic is
    needed — see app/middleware/rate_limit.py.
    """
    cache_key = f"policy:{tenant_id}:{route}"

    try:
        cached = await redis.get(cache_key)
        if cached:
            return json.loads(cached)
    except RedisError:
        logger.warning("policy_cache_unavailable", extra={"tenant_id": tenant_id})

    async with session_factory() as db:
        policy = await _fetch_matching_policy(db, tenant_id, route)
    if policy is None:
        return None

    try:
        await redis.set(cache_key, json.dumps(policy), ex=CACHE_TTL_S)
    except RedisError:
        pass  # best-effort repopulation; the DB read above already succeeded

    return policy
