import time
import uuid
from pathlib import Path

from redis.asyncio import Redis
from redis.exceptions import NoScriptError

_SCRIPT_PATH = Path(__file__).parent / "lua" / "sliding_window.lua"
_script_sha: str | None = None


async def load_script(redis: Redis) -> None:
    global _script_sha
    script_body = _SCRIPT_PATH.read_text()
    _script_sha = await redis.script_load(script_body)


async def check_rate_limit(
    redis: Redis, tenant_id: str, route: str, limit: int, window_s: int
) -> tuple[bool, int]:
    """Atomic sliding-window check via a server-side Lua script.

    Sliding window log, not fixed window or token bucket: a fixed-window
    counter allows a 2x burst at window boundaries (full limit in the last
    second of one window, full limit again in the first second of the
    next) — a well-known flaw. Token bucket avoids that but loses
    per-request auditability (its state is one opaque number, not a
    replayable log of exactly which requests counted) and adds distributed
    refill-timing complexity not justified at this project's scale.

    Key is hash-tagged (`{tenant_id}`) so that in Redis Cluster mode, every
    key belonging to one tenant's rate limiting lands on the same hash
    slot — required for the Lua script to be able to touch them atomically.
    """
    key = f"rate:{{{tenant_id}}}:{route}"
    now_ms = int(time.time() * 1000)
    request_id = str(uuid.uuid4())

    global _script_sha
    if _script_sha is None:
        await load_script(redis)

    try:
        allowed, remaining = await redis.evalsha(
            _script_sha, 1, key, now_ms, window_s * 1000, limit, request_id
        )
    except NoScriptError:
        # Redis restarted or SCRIPT FLUSH ran — its script cache is empty
        # even though we still hold a SHA computed before that happened.
        # Reload once and retry rather than crash the request.
        await load_script(redis)
        allowed, remaining = await redis.evalsha(
            _script_sha, 1, key, now_ms, window_s * 1000, limit, request_id
        )

    return bool(allowed), remaining
