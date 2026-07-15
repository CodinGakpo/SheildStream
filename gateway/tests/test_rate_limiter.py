import asyncio
import time

import fakeredis.aioredis
import pytest

from app.rate_limiter import check_rate_limit, load_script


@pytest.fixture
async def redis():
    r = fakeredis.aioredis.FakeRedis()
    await load_script(r)
    return r


async def test_allows_up_to_limit(redis):
    results = [await check_rate_limit(redis, "t1", "/api", 10, 60) for _ in range(10)]
    assert all(allowed for allowed, _ in results)


async def test_blocks_over_limit(redis):
    for _ in range(10):
        await check_rate_limit(redis, "t1", "/api", 10, 60)
    allowed, remaining = await check_rate_limit(redis, "t1", "/api", 10, 60)
    assert allowed is False
    assert remaining == 0


async def test_concurrent_requests_never_exceed_limit(redis):
    """The actual race-condition proof. A rate limiter implemented as
    separate ZCARD-then-ZADD calls (no Lua) passes every *sequential* test
    perfectly — the race is invisible until requests genuinely overlap.
    asyncio.gather() with many simultaneous calls is what actually exercises
    it; run this repeatedly (see test_rate_limiter_repeat.py) before trusting
    the atomicity claim, since a race that only manifests 1 in 200 runs is
    exactly the kind of bug that passes a single CI run and then reappears
    intermittently in production."""
    results = await asyncio.gather(
        *[check_rate_limit(redis, "t1", "/api", 10, 60) for _ in range(100)]
    )
    allowed_count = sum(1 for allowed, _ in results if allowed)
    assert allowed_count == 10  # exactly 10, never 9, never 11


async def test_window_slides_correctly(redis, monkeypatch):
    t0 = time.time()
    monkeypatch.setattr(time, "time", lambda: t0)
    for _ in range(10):
        await check_rate_limit(redis, "t1", "/api", 10, 60)

    # Still inside the window: the 11th request is blocked.
    allowed, _ = await check_rate_limit(redis, "t1", "/api", 10, 60)
    assert allowed is False

    # 61s later, outside the 60s window: entries have aged out, so the
    # window has room again — this is the sliding-window-log's defining
    # property, the exact behavior a fixed-window counter gets wrong at
    # its boundary.
    monkeypatch.setattr(time, "time", lambda: t0 + 61)
    allowed, _ = await check_rate_limit(redis, "t1", "/api", 10, 60)
    assert allowed is True


async def test_different_tenants_have_independent_limits(redis):
    for _ in range(10):
        await check_rate_limit(redis, "tenant-a", "/api", 10, 60)
    # tenant-a is now exhausted; tenant-b, a separate key, is unaffected.
    allowed, _ = await check_rate_limit(redis, "tenant-b", "/api", 10, 60)
    assert allowed is True


async def test_different_routes_have_independent_limits(redis):
    for _ in range(10):
        await check_rate_limit(redis, "t1", "/api/hot", 10, 60)
    allowed, _ = await check_rate_limit(redis, "t1", "/api/cold", 10, 60)
    assert allowed is True
