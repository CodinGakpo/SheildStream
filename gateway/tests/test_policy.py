import json

import fakeredis.aioredis
import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from app import policy as policy_module
from app.policy import get_policy

FAKE_POLICY = {"rate_limit_rps": 100, "rate_limit_window_s": 60, "policy_version": 1}


@pytest.fixture
def redis():
    return fakeredis.aioredis.FakeRedis()


async def test_cache_miss_falls_back_to_db_and_populates_cache(redis, monkeypatch):
    calls = []

    async def fake_fetch(db, tenant_id, route):
        calls.append((tenant_id, route))
        return dict(FAKE_POLICY)

    monkeypatch.setattr(policy_module, "_fetch_matching_policy", fake_fetch)

    result = await get_policy(redis, "t1", "/proxy/get")
    assert result == FAKE_POLICY
    assert calls == [("t1", "/proxy/get")]

    cached = await redis.get("policy:t1:/proxy/get")
    assert json.loads(cached) == FAKE_POLICY


async def test_cache_hit_skips_db(redis, monkeypatch):
    async def fake_fetch(db, tenant_id, route):
        raise AssertionError("DB should not be queried on a cache hit")

    monkeypatch.setattr(policy_module, "_fetch_matching_policy", fake_fetch)
    await redis.set("policy:t1:/proxy/get", json.dumps(FAKE_POLICY), ex=10)

    result = await get_policy(redis, "t1", "/proxy/get")
    assert result == FAKE_POLICY


async def test_no_matching_policy_returns_none(redis, monkeypatch):
    async def fake_fetch(db, tenant_id, route):
        return None

    monkeypatch.setattr(policy_module, "_fetch_matching_policy", fake_fetch)

    result = await get_policy(redis, "t1", "/unconfigured")
    assert result is None


async def test_redis_error_on_read_falls_through_to_db(monkeypatch):
    # A cache that raises on GET (not just a clean miss) still needs to
    # reach the database rather than propagate the error — see the
    # docstring in app/policy.py.
    class ExplodingRedis:
        async def get(self, key):
            raise RedisConnectionError("simulated outage")

        async def set(self, *args, **kwargs):
            raise RedisConnectionError("simulated outage")

    async def fake_fetch(db, tenant_id, route):
        return dict(FAKE_POLICY)

    monkeypatch.setattr(policy_module, "_fetch_matching_policy", fake_fetch)

    result = await get_policy(ExplodingRedis(), "t1", "/proxy/get")
    assert result == FAKE_POLICY
