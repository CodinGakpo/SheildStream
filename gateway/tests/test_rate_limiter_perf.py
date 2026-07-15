"""Live-Redis latency benchmark for the sliding-window Lua script.

Not a pytest test — fakeredis (used by test_rate_limiter.py) is a
pure-Python reimplementation and its call latency says nothing about real
Redis round-trip cost. Run directly against a live Redis instance:

    python tests/test_rate_limiter_perf.py

(from inside the gateway container, or against localhost:6379 if Redis is
exposed to the host, as it is in docker-compose.yml).
"""

import asyncio
import time

from redis.asyncio import Redis

from app.rate_limiter import check_rate_limit, load_script


async def benchmark() -> None:
    redis = Redis.from_url("redis://localhost:6379/0")
    await load_script(redis)
    latencies = []
    for i in range(2000):
        t0 = time.perf_counter()
        # A limit far above the sample count means every call is "allowed" —
        # this benchmark measures the script's round-trip cost, not rejection
        # behavior (already covered by the fakeredis tests).
        await check_rate_limit(redis, f"bench-{i % 50}", "/api", 1_000_000, 60)
        latencies.append((time.perf_counter() - t0) * 1000)
    await redis.aclose()

    latencies.sort()
    p50 = latencies[len(latencies) // 2]
    p99 = latencies[int(len(latencies) * 0.99)]
    print(f"p50={p50:.3f}ms  p99={p99:.3f}ms")


if __name__ == "__main__":
    asyncio.run(benchmark())
