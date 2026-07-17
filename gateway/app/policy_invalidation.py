"""Push-based policy cache invalidation — Week 9 Part A.

Layered on top of app/policy.py's 10s TTL, never a replacement for it: this
only makes invalidation *faster*, from up-to-10s down to sub-second. A
gateway replica that misses this message (mid-restart, subscribed a moment
too late) simply falls back to the TTL — worst case a 10-second-old policy,
never an indefinitely stale one.

REVISION #8: the guide reconstructs the cache key it deletes as
`policy:{tenant_id}:{route_pattern}` (e.g. "policy:<id>:/proxy/*"). That key
never exists in this codebase's cache — app/policy.py caches by the
*literal* requested path (e.g. "/proxy/get"), not the glob pattern that
matched it, because one policy row's route_pattern can match many distinct
literal routes, each getting its own cache entry from _fetch_matching_policy.
Deleting the guide's reconstructed key would silently miss every other
literal route cached under the stale value until its own TTL expired —
defeating the sub-second propagation this feature exists to provide. Fixed
by SCANning and deleting every `policy:{tenant_id}:*` entry for the tenant
named in the invalidation message: a tenant has at most a handful of
policies, so a full per-tenant cache flush is cheap, and it's correct
regardless of how many literal routes happen to be cached at the moment.
"""

import asyncio
import json
import logging

from redis.asyncio import Redis
from redis.exceptions import RedisError

logger = logging.getLogger("shieldstream.policy_invalidation")

CHANNEL = "policy:invalidate"


async def _invalidate_tenant_cache(redis: Redis, tenant_id: str) -> None:
    keys = [key async for key in redis.scan_iter(match=f"policy:{tenant_id}:*")]
    if keys:
        await redis.delete(*keys)


async def _listen_once(redis: Redis) -> None:
    pubsub = redis.pubsub()
    await pubsub.subscribe(CHANNEL)
    async for message in pubsub.listen():
        if message["type"] != "message":
            continue
        try:
            payload = json.loads(message["data"])
            await _invalidate_tenant_cache(redis, payload["tenant_id"])
            logger.info(
                "policy_cache_invalidated",
                extra={
                    "tenant_id": payload["tenant_id"],
                    "new_version": payload.get("new_version"),
                },
            )
        except (KeyError, json.JSONDecodeError):
            # Deliberately swallowed, not re-raised — a malformed invalidation
            # message must never crash the listener loop and silently stop
            # every future invalidation for every tenant.
            logger.exception("failed_to_process_invalidation")


async def listen_for_invalidations(redis: Redis) -> None:
    """Runs for the process lifetime as a background task (see main.py's
    lifespan). Reconnects on a dropped Redis connection instead of dying
    silently — without this, one Redis blip would permanently disable
    sub-second hot-reload for the rest of the process's uptime, forcing
    total reliance back on the 10s TTL until the next restart."""
    while True:
        try:
            await _listen_once(redis)
        except RedisError:
            logger.warning("invalidation_listener_reconnecting")
            await asyncio.sleep(1.0)
