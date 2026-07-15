"""Rate limiting on the request path.

REVISION #3: the guide implements this as Starlette `@app.middleware("http")`
— but Starlette's middleware chain runs *before* FastAPI resolves the
route's own `Depends()` parameters, so a middleware function has no access
to `request.state.tenant`, which only exists after the `get_tenant`
dependency has run. The guide's own code reads `request.state.tenant` from
inside the middleware and would simply crash (AttributeError) the first
time it ran, since nothing sets that attribute before the middleware chain
executes.

Fixed here by implementing rate limiting as a FastAPI *dependency* instead
— `enforce_rate_limit` takes `tenant: dict = Depends(get_tenant)` as its own
sub-dependency, so FastAPI resolves auth first, then this, in an explicit,
testable, per-route chain. FastAPI caches dependency results per request,
so `get_tenant` only actually runs once even though both the route and this
dependency declare it.
"""

import logging
import time
import uuid

from fastapi import Depends, HTTPException, Request
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.auth import get_tenant
from app.config import settings
from app.event_emitter import emit_event
from app.events import RequestEvent, client_ip, hash_ip
from app.fallback_limiter import in_memory_check
from app.policy import get_policy
from app.rate_limiter import check_rate_limit
from app.redis_client import get_redis
from app.tracing import tracer

logger = logging.getLogger("shieldstream.ratelimit")


async def enforce_rate_limit(
    request: Request,
    tenant: dict = Depends(get_tenant),
    redis: Redis = Depends(get_redis),
) -> dict:
    """Returns the info needed to set X-RateLimit-* response headers.
    Raises HTTPException(429) directly if the request is over the limit —
    by the time this returns normally, the request is always allowed."""
    route = request.url.path

    with tracer.start_as_current_span("rate_limiter.check") as span:
        policy = await get_policy(redis, tenant["id"], route)
        if policy is None:
            # No policy matches this tenant/route at all. Fail open with a
            # generous default rather than blocking a request the operator
            # never configured a limit for — an unconfigured route
            # shouldn't be indistinguishable from a rate-limited one.
            span.set_attribute("shieldstream.policy_found", False)
            return {"limit": None, "remaining": None}
        span.set_attribute("shieldstream.policy_found", True)
        span.set_attribute("shieldstream.rate_limit_rps", policy["rate_limit_rps"])

        try:
            allowed, remaining = await check_rate_limit(
                redis,
                tenant["id"],
                route,
                policy["rate_limit_rps"],
                policy["rate_limit_window_s"],
            )
        except RedisError:
            logger.error("redis_unavailable", extra={"tenant_id": tenant["id"], "route": route})
            if not settings.rate_limit_fail_open:
                raise HTTPException(
                    status_code=503,
                    detail="rate limiter unavailable, failing closed",
                )
            allowed, remaining = in_memory_check(
                tenant["id"], route, policy["rate_limit_rps"], policy["rate_limit_window_s"]
            )
            span.set_attribute("shieldstream.fail_open", True)

    if not allowed:
        # Blocked requests never reach the proxy handler's own emit call, so
        # this path emits its own event before short-circuiting — otherwise
        # the analytics pipeline would systematically undercount exactly the
        # traffic a security gateway most needs to see. latency_ms is 0.0 by
        # convention: there is no upstream round trip to measure, and the
        # analytics consumer excludes rate-limited events from latency
        # percentiles for that reason.
        emit_event(
            redis,
            RequestEvent(
                request_id=str(uuid.uuid4()),
                tenant_id=tenant["id"],
                endpoint=route,
                method=request.method,
                status_code=429,
                latency_ms=0.0,
                rate_limited=True,
                remote_ip_hash=hash_ip(client_ip(request), tenant["ip_hash_salt"]),
                timestamp_ms=int(time.time() * 1000),
                query_string=str(request.url.query),
                user_agent=request.headers.get("user-agent", ""),
            ),
        )
        raise HTTPException(
            status_code=429,
            detail="rate limit exceeded",
            headers={
                "Retry-After": str(policy["rate_limit_window_s"]),
                "X-RateLimit-Limit": str(policy["rate_limit_rps"]),
                "X-RateLimit-Remaining": "0",
            },
        )

    return {"limit": policy["rate_limit_rps"], "remaining": remaining}
