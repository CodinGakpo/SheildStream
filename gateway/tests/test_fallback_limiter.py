import time

from app.fallback_limiter import FALLBACK_MULTIPLIER, _buckets, _last_seen, in_memory_check


def setup_function():
    # Module-level state — reset between tests so they don't interfere.
    _buckets.clear()
    _last_seen.clear()


def test_allows_up_to_limit_times_multiplier():
    limit = 3
    for _ in range(limit * FALLBACK_MULTIPLIER):
        allowed, _ = in_memory_check("t1", "/api", limit, 60)
        assert allowed is True


def test_blocks_beyond_limit_times_multiplier():
    limit = 3
    for _ in range(limit * FALLBACK_MULTIPLIER):
        in_memory_check("t1", "/api", limit, 60)
    allowed, remaining = in_memory_check("t1", "/api", limit, 60)
    assert allowed is False
    assert remaining == 0


def test_window_slides(monkeypatch):
    t0 = time.time()
    monkeypatch.setattr(time, "time", lambda: t0)
    limit = 2
    for _ in range(limit * FALLBACK_MULTIPLIER):
        in_memory_check("t1", "/api", limit, 60)
    assert in_memory_check("t1", "/api", limit, 60)[0] is False

    monkeypatch.setattr(time, "time", lambda: t0 + 61)
    assert in_memory_check("t1", "/api", limit, 60)[0] is True


def test_uses_actual_policy_limit_not_a_hardcoded_ceiling():
    # REVISION #7: a tenant with a tiny limit and a tenant with a huge limit
    # get proportionally different fallback ceilings, not the same fixed
    # number.
    for _ in range(1 * FALLBACK_MULTIPLIER):
        in_memory_check("tiny-tenant", "/api", 1, 60)
    assert in_memory_check("tiny-tenant", "/api", 1, 60)[0] is False

    for _ in range(1000 * FALLBACK_MULTIPLIER):
        allowed, _ = in_memory_check("huge-tenant", "/api", 1000, 60)
    assert allowed is True  # still well under its own, much larger ceiling


def test_stale_keys_are_evicted(monkeypatch):
    t0 = time.time()
    monkeypatch.setattr(time, "time", lambda: t0)
    in_memory_check("t1", "/api", 10, 60)
    assert "t1:/api" in _last_seen

    # Idle past the eviction threshold, then touch a *different* key — the
    # stale entry should be swept, not accumulate forever across an
    # extended Redis outage.
    monkeypatch.setattr(time, "time", lambda: t0 + 301)
    in_memory_check("t2", "/api", 10, 60)
    assert "t1:/api" not in _last_seen
    assert "t1:/api" not in _buckets
