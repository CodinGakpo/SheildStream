import time
from collections import defaultdict

# Deliberately more permissive than the real limit: this path only runs
# while the distributed limiter (Redis) is unreachable, so availability is
# prioritized over exact enforcement for the (hopefully brief) outage
# window — see DECISIONS.md, Week 5.
FALLBACK_MULTIPLIER = 10

# Per-key idle threshold before an entry is evicted. Not tied to any single
# window_s (many tenants/routes with different windows share this dict), so
# a fixed, generous threshold is used instead of tracking each key's own
# window — cheap, and this path is only ever exercised during an actual
# Redis outage, which is expected to be short.
_EVICT_IDLE_S = 300

_buckets: dict[str, list[float]] = defaultdict(list)
_last_seen: dict[str, float] = {}


def in_memory_check(tenant_id: str, route: str, limit: int, window_s: int) -> tuple[bool, int]:
    """Non-distributed, per-replica fallback used only when Redis is down.

    REVISION #7: uses the tenant's *actual* policy limit (passed in), not a
    hardcoded ceiling — a hardcoded number is either too strict for a
    high-limit tenant or too permissive for a low-limit one; the caller
    already has the real policy in hand from app.policy.get_policy, so
    there's no reason not to use it. Each gateway replica tracks its own
    independent bucket: with N replicas behind a load balancer, the
    effective fail-open ceiling is roughly N times more permissive than a
    single replica's — a known, documented imprecision, acceptable
    specifically because fail-open is a short-duration degraded mode, not
    the steady-state enforcement path.
    """
    key = f"{tenant_id}:{route}"
    now = time.time()

    _evict_stale(now)
    _last_seen[key] = now

    _buckets[key] = [t for t in _buckets[key] if t > now - window_s]
    if len(_buckets[key]) < limit * FALLBACK_MULTIPLIER:
        _buckets[key].append(now)
        return True, 0
    return False, 0


def _evict_stale(now: float) -> None:
    stale = [k for k, seen in _last_seen.items() if now - seen > _EVICT_IDLE_S]
    for key in stale:
        _last_seen.pop(key, None)
        _buckets.pop(key, None)
